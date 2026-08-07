"""Atomic multi-dimensional admission, reservation, and settlement."""

from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from typing import Any
from uuid import uuid4

from .config import ConfigError, RunConfig
from .models import (
    BudgetReservation, BudgetSnapshot, BudgetVector, ReservationStatus,
    RunPhase, usd_to_microusd,
)
from .store import SQLiteStore, from_iso, to_iso, utc_now


class BudgetError(RuntimeError):
    pass


class BudgetExceeded(BudgetError):
    def __init__(self, reasons: list[str] | tuple[str, ...]):
        self.reasons = tuple(sorted(set(reasons)))
        super().__init__("budget reservation denied: " + ", ".join(self.reasons))


class InvalidSettlement(BudgetError):
    pass


_CLOSEOUT_PURPOSES = frozenset({"closeout", "report"})


def _is_reserve_authorized(phase: RunPhase, purpose: str) -> bool:
    """Derive reserve access from controller state, never caller assertion.

    A free-form ``critical`` flag or purpose would let exploratory work consume
    the budget deliberately held back for reporting and the one final test.
    Only the two terminal-path phases can access that reserve, and only for
    their phase-specific purposes.
    """

    return (
        phase is RunPhase.FINAL_TEST
        and purpose == "final_test"
    ) or (
        phase is RunPhase.CLOSEOUT
        and purpose in _CLOSEOUT_PURPOSES
    )


class BudgetManager:
    """Reserve worst-case use before dispatch and settle actual use after it."""

    def __init__(
        self,
        store: SQLiteStore,
        config: RunConfig,
        *,
        started_at: datetime | None = None,
        bind_run: bool = True,
    ) -> None:
        self.store = store
        self.config = config
        if bind_run:
            self.store.bind_run(
                run_id=config.run_id,
                schema_version=config.schema_version,
                config_fingerprint=config.fingerprint(),
                deadline_at=config.deadline_at,
                started_at=started_at,
                control_limits={
                    "max_dev_iterations": config.max_dev_iterations,
                    "low_gain_patience": config.low_gain_patience,
                    "min_dev_gain_pp": config.min_dev_gain_pp,
                    "max_cash_per_gain_pp_microusd": config.max_cash_per_gain_pp_microusd,
                },
            )
        else:
            expected = {
                "run_id": config.run_id,
                "protocol_schema_version": config.schema_version,
                "config_fingerprint": config.fingerprint(),
            }
            mismatched = {
                key: (store.get_meta(key), value)
                for key, value in expected.items()
                if store.get_meta(key) != value
            }
            if mismatched:
                raise BudgetError(f"state/config binding mismatch: {mismatched}")

    @staticmethod
    def _row_vector(row: sqlite3.Row, suffix: str) -> BudgetVector:
        return BudgetVector(
            tokens=int(row[f"token_{suffix}"]),
            incremental_cash_microusd=int(row[f"incremental_cash_{suffix}"]),
            provider_cash_microusd=int(row[f"provider_cash_{suffix}"]),
            wall_seconds=int(row[f"wall_seconds_{suffix}"]),
            tool_calls=int(row[f"tool_calls_{suffix}"]),
        )

    @staticmethod
    def _vector(
        amount: BudgetVector | None,
        *,
        tokens: int,
        incremental_cash_usd: Any | None,
        incremental_cash_microusd: int | None,
        provider_cash_usd: Any | None,
        provider_cash_microusd: int | None,
        wall_seconds: int,
        tool_calls: int,
    ) -> BudgetVector:
        if amount is not None:
            if any(value not in (0, None) for value in (tokens, incremental_cash_usd, incremental_cash_microusd, provider_cash_usd, provider_cash_microusd, wall_seconds, tool_calls)):
                raise ValueError("pass either BudgetVector or component fields, not both")
            return amount
        incremental = usd_to_microusd(incremental_cash_usd, field_name="incremental_cash_usd") if incremental_cash_usd is not None else int(incremental_cash_microusd or 0)
        provider = usd_to_microusd(provider_cash_usd, field_name="provider_cash_usd") if provider_cash_usd is not None else int(provider_cash_microusd or 0)
        return BudgetVector(int(tokens), incremental, provider, int(wall_seconds), int(tool_calls))

    def _aggregate(self, cursor: sqlite3.Cursor, *, stage: str | None = None) -> tuple[BudgetVector, BudgetVector, dict[str, BudgetVector]]:
        query = "SELECT * FROM reservations WHERE status IN (?, ?)"
        params: list[Any] = [ReservationStatus.RESERVED.value, ReservationStatus.COMMITTED.value]
        if stage is not None:
            query += " AND stage=?"
            params.append(stage)
        committed = BudgetVector()
        reserved = BudgetVector()
        providers: dict[str, BudgetVector] = {}
        for row in cursor.execute(query, tuple(params)).fetchall():
            vector = self._row_vector(row, "reserved" if row["status"] == ReservationStatus.RESERVED.value else "actual")
            if row["status"] == ReservationStatus.RESERVED.value:
                reserved += vector
            else:
                committed += vector
            providers[row["provider"]] = providers.get(row["provider"], BudgetVector()) + vector
        return committed, reserved, providers

    def _snapshot_from_cursor(self, cursor: sqlite3.Cursor, now: datetime) -> BudgetSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        committed, reserved, _ = self._aggregate(cursor)
        used = committed + reserved
        nominal = self.config.ceiling - used
        started_row = cursor.execute("SELECT value FROM meta WHERE key='run_started_at'").fetchone()
        started = from_iso(started_row["value"]) if started_row else now
        elapsed = max(0, int((now - started).total_seconds()))
        deadline_remaining = max(0, int((self.config.deadline_at - now).total_seconds()))
        remaining = BudgetVector(
            nominal.tokens,
            nominal.incremental_cash_microusd,
            nominal.provider_cash_microusd,
            min(nominal.wall_seconds, deadline_remaining, max(0, self.config.max_wall_clock_seconds - elapsed)),
            nominal.tool_calls,
        )
        reserve = self.config.closeout_reserve
        reasons: list[str] = []
        for name, value, threshold in (
            ("tokens", remaining.tokens, reserve.tokens),
            ("incremental_cash", remaining.incremental_cash_microusd, reserve.incremental_cash_microusd),
            ("provider_cash", remaining.provider_cash_microusd, reserve.provider_cash_microusd),
            ("wall_clock", remaining.wall_seconds, reserve.wall_seconds),
            ("tool_calls", remaining.tool_calls, reserve.tool_calls),
        ):
            if value <= threshold:
                reasons.append(name)
        soft = self.config.soft_watermark
        for name, value, threshold in (
            ("tokens_soft_watermark", used.tokens, soft.tokens),
            ("incremental_cash_soft_watermark", used.incremental_cash_microusd, soft.incremental_cash_microusd),
            ("provider_cash_soft_watermark", used.provider_cash_microusd, soft.provider_cash_microusd),
            ("wall_clock_soft_watermark", elapsed, soft.wall_seconds),
            ("tool_calls_soft_watermark", used.tool_calls, soft.tool_calls),
        ):
            if value >= threshold:
                reasons.append(name)
        if deadline_remaining <= reserve.wall_seconds:
            reasons.append("deadline")
        return BudgetSnapshot(
            ceiling=self.config.ceiling,
            committed=committed,
            reserved=reserved,
            remaining=remaining,
            closeout_reserve=reserve,
            closeout_required=bool(reasons),
            closeout_reasons=tuple(sorted(set(reasons))),
            elapsed_wall_seconds=elapsed,
            deadline_remaining_seconds=deadline_remaining,
        )

    def snapshot(self, *, now: datetime | None = None) -> BudgetSnapshot:
        instant = now or utc_now()
        with self.store._lock:
            cursor = self.store.connection.cursor()
            try:
                return self._snapshot_from_cursor(cursor, instant)
            finally:
                cursor.close()

    def _reserve_locked(
        self,
        cursor: sqlite3.Cursor,
        *,
        task_id: str,
        provider: str,
        model: str,
        phase: RunPhase,
        amount: BudgetVector,
        purpose: str,
        reservation_id: str,
        input_tokens: int,
        output_tokens: int,
        now: datetime,
    ) -> BudgetReservation:
        if not task_id or not provider or not model or not purpose or not reservation_id:
            raise ValueError("reservation identity fields are required")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError("input/output token maxima must be non-negative integers")
        stage = self.config.stage_for(phase)
        existing = cursor.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if existing is not None:
            identity = (
                existing["task_id"], existing["provider"], existing["model"],
                existing["phase"], existing["stage"], existing["purpose"],
                self._row_vector(existing, "reserved"),
            )
            if identity != (task_id, provider, model, phase.value, stage, purpose, amount):
                raise BudgetError("idempotency key was reused with different parameters")
            return self._reservation_from_row(existing)

        run = cursor.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
        is_critical = _is_reserve_authorized(phase, purpose)
        reasons: list[str] = []
        if run["terminal_status"] is not None:
            reasons.append("run_terminal")
        if run["closeout_mode"] and not is_critical:
            reasons.append("closeout_mode")
        if RunPhase(run["phase"]) is not phase:
            reasons.append("phase_mismatch")
        if purpose == "final_test" and phase is not RunPhase.FINAL_TEST:
            reasons.append("purpose_phase_mismatch")
        if purpose in _CLOSEOUT_PURPOSES and phase is not RunPhase.CLOSEOUT:
            reasons.append("purpose_phase_mismatch")
        if phase is RunPhase.FINAL_TEST and purpose != "final_test":
            reasons.append("final_test_purpose_required")
        if phase is RunPhase.CLOSEOUT and purpose not in _CLOSEOUT_PURPOSES:
            reasons.append("closeout_purpose_required")
        phase_deadline = self.config.phase_deadlines[phase]
        if now >= self.config.deadline_at or now + timedelta(seconds=amount.wall_seconds) > self.config.deadline_at:
            reasons.append("deadline")
        if now >= phase_deadline or now + timedelta(seconds=amount.wall_seconds) > phase_deadline:
            reasons.append(f"phase_deadline:{phase.value}")

        try:
            limit = self.config.call_limit(provider, model, purpose)
        except ConfigError:
            reasons.append("provider_model_not_allowlisted")
        else:
            if input_tokens > limit.max_input_tokens:
                reasons.append("input_tokens_per_call")
            if output_tokens > limit.max_output_tokens:
                reasons.append("output_tokens_per_call")
            if input_tokens + output_tokens > amount.tokens:
                reasons.append("token_reservation_smaller_than_call")
            if amount.incremental_cash_microusd > limit.max_cash_microusd:
                reasons.append("cash_per_call")
            if amount.provider_cash_microusd > limit.max_cash_microusd:
                reasons.append("provider_cash_per_call")
        if amount.tool_calls < 1:
            reasons.append("tool_call_not_reserved")

        snapshot = self._snapshot_from_cursor(cursor, now)
        accessible = snapshot.remaining if is_critical else snapshot.remaining - self.config.closeout_reserve
        for key, value in amount.as_dict().items():
            if value > accessible.as_dict()[key]:
                reasons.append(f"global:{key}")
        stage_committed, stage_reserved, _ = self._aggregate(cursor, stage=stage)
        if not (stage_committed + stage_reserved + amount).fits_within(self.config.phase_caps[stage]):
            for key, value in (stage_committed + stage_reserved + amount).as_dict().items():
                if value > self.config.phase_caps[stage].as_dict()[key]:
                    reasons.append(f"stage:{stage}:{key}")
        if reasons:
            raise BudgetExceeded(reasons)

        timestamp = to_iso(now)
        cursor.execute(
            """INSERT INTO reservations(
                   reservation_id, task_id, provider, model, phase, stage, purpose, status,
                   token_reserved, incremental_cash_reserved, provider_cash_reserved,
                   wall_seconds_reserved, tool_calls_reserved, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reservation_id, task_id, provider, model, phase.value, stage, purpose,
                ReservationStatus.RESERVED.value, amount.tokens,
                amount.incremental_cash_microusd, amount.provider_cash_microusd,
                amount.wall_seconds, amount.tool_calls, timestamp, timestamp,
            ),
        )
        self.store._append_event(
            cursor, "BUDGET_RESERVED", "reservation", reservation_id,
            {"task_id": task_id, "provider": provider, "model": model, "phase": phase.value, "stage": stage, "purpose": purpose, "critical": is_critical, "amount": amount.as_dict()},
            occurred_at=now,
        )
        return self._reservation_from_row(cursor.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone())

    def reserve(
        self,
        task_id: str,
        provider: str,
        model: str,
        amount: BudgetVector | None = None,
        *,
        phase: RunPhase | str | None = None,
        tokens: int = 0,
        incremental_cash_usd: Any | None = None,
        incremental_cash_microusd: int | None = None,
        provider_cash_usd: Any | None = None,
        provider_cash_microusd: int | None = None,
        wall_seconds: int = 0,
        tool_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        purpose: str = "exploration",
        reservation_id: str | None = None,
        now: datetime | None = None,
    ) -> BudgetReservation:
        instant = now or utc_now()
        vector = self._vector(amount, tokens=tokens, incremental_cash_usd=incremental_cash_usd, incremental_cash_microusd=incremental_cash_microusd, provider_cash_usd=provider_cash_usd, provider_cash_microusd=provider_cash_microusd, wall_seconds=wall_seconds, tool_calls=tool_calls)
        with self.store.transaction() as cursor:
            active = RunPhase(cursor.execute("SELECT phase FROM run_state WHERE singleton=1").fetchone()["phase"])
            selected_phase = RunPhase(phase) if phase is not None else active
            return self._reserve_locked(
                cursor, task_id=task_id, provider=provider, model=model,
                phase=selected_phase, amount=vector, purpose=purpose,
                reservation_id=reservation_id or str(uuid4()), input_tokens=input_tokens,
                output_tokens=output_tokens, now=instant,
            )

    def commit(
        self,
        reservation_id: str,
        actual: BudgetVector | None = None,
        *,
        tokens: int = 0,
        incremental_cash_usd: Any | None = None,
        incremental_cash_microusd: int | None = None,
        provider_cash_usd: Any | None = None,
        provider_cash_microusd: int | None = None,
        wall_seconds: int = 0,
        tool_calls: int = 0,
        now: datetime | None = None,
    ) -> BudgetReservation:
        instant = now or utc_now()
        with self.store.transaction() as cursor:
            row = cursor.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise BudgetError(f"unknown reservation: {reservation_id}")
            reserved = self._row_vector(row, "reserved")
            no_parts = all(value in (0, None) for value in (tokens, incremental_cash_usd, incremental_cash_microusd, provider_cash_usd, provider_cash_microusd, wall_seconds, tool_calls))
            settled = reserved if actual is None and no_parts else self._vector(actual, tokens=tokens, incremental_cash_usd=incremental_cash_usd, incremental_cash_microusd=incremental_cash_microusd, provider_cash_usd=provider_cash_usd, provider_cash_microusd=provider_cash_microusd, wall_seconds=wall_seconds, tool_calls=tool_calls)
            if not settled.fits_within(reserved):
                raise InvalidSettlement("actual usage exceeds its worst-case reservation")
            if row["status"] == ReservationStatus.COMMITTED.value:
                if self._row_vector(row, "actual") != settled:
                    raise InvalidSettlement("committed reservation is immutable")
                return self._reservation_from_row(row)
            if row["status"] != ReservationStatus.RESERVED.value:
                raise InvalidSettlement(f"cannot commit a {row['status']} reservation")
            cursor.execute(
                """UPDATE reservations SET status=?, token_actual=?,
                       incremental_cash_actual=?, provider_cash_actual=?,
                       wall_seconds_actual=?, tool_calls_actual=?, updated_at=?
                   WHERE reservation_id=?""",
                (ReservationStatus.COMMITTED.value, settled.tokens, settled.incremental_cash_microusd, settled.provider_cash_microusd, settled.wall_seconds, settled.tool_calls, to_iso(instant), reservation_id),
            )
            self.store._append_event(cursor, "BUDGET_COMMITTED", "reservation", reservation_id, {"actual": settled.as_dict()}, occurred_at=instant)
            return self._reservation_from_row(cursor.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone())

    def release(self, reservation_id: str, *, reason: str, now: datetime | None = None) -> BudgetReservation:
        if not reason:
            raise ValueError("release reason is required")
        instant = now or utc_now()
        with self.store.transaction() as cursor:
            row = cursor.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise BudgetError(f"unknown reservation: {reservation_id}")
            if row["status"] == ReservationStatus.RELEASED.value:
                return self._reservation_from_row(row)
            if row["status"] != ReservationStatus.RESERVED.value:
                raise InvalidSettlement("only an active reservation may be released")
            cursor.execute("UPDATE reservations SET status=?, updated_at=? WHERE reservation_id=?", (ReservationStatus.RELEASED.value, to_iso(instant), reservation_id))
            self.store._append_event(cursor, "BUDGET_RELEASED", "reservation", reservation_id, {"reason": reason}, occurred_at=instant)
            return self._reservation_from_row(cursor.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone())

    def maybe_enter_closeout(self, *, now: datetime | None = None) -> BudgetSnapshot:
        snapshot = self.snapshot(now=now)
        if snapshot.closeout_required:
            self.store.set_closeout_mode("budget soft waterline reached: " + ", ".join(snapshot.closeout_reasons))
        return snapshot

    def _reservation_from_row(self, row: sqlite3.Row) -> BudgetReservation:
        return BudgetReservation(
            reservation_id=row["reservation_id"], task_id=row["task_id"],
            provider=row["provider"], model=row["model"], phase=RunPhase(row["phase"]),
            stage=row["stage"], purpose=row["purpose"],
            status=ReservationStatus(row["status"]), reserved=self._row_vector(row, "reserved"),
            actual=self._row_vector(row, "actual"), created_at=from_iso(row["created_at"]),
            updated_at=from_iso(row["updated_at"]),
        )
