"""SQLite-backed, append-only control ledger.

All mutations use ``BEGIN IMMEDIATE`` and append a hash-chained event in the
same transaction.  SQLite is the single writer: workers return completion
events through the controller and never edit task state directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator, Mapping, Sequence

from .models import FinalTestStatus, RunPhase, TaskStatus, TerminalStatus


class StoreError(RuntimeError):
    pass


class ImmutableRunError(StoreError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class SQLiteStore:
    """Durable single-writer state and event store."""

    SCHEMA_VERSION = 3

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = str(path)
        self.read_only = read_only
        if read_only and self.path == ":memory:":
            raise StoreError("read-only stores require an existing filesystem path")
        if read_only and (
            not Path(self.path).is_file() or Path(self.path).is_symlink()
        ):
            raise StoreError(f"read-only SQLite state does not exist: {self.path}")
        if self.path != ":memory:":
            if not read_only:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        connection_target = (
            Path(self.path).resolve().as_uri() + "?mode=ro" if read_only else self.path
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                connection_target,
                isolation_level=None,
                check_same_thread=False,
                timeout=30.0,
                uri=read_only,
            )
            self._conn = connection
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=30000")
            if read_only:
                self._conn.execute("PRAGMA query_only=ON")
            elif self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
            if read_only:
                version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
                if version != self.SCHEMA_VERSION:
                    raise StoreError(
                        f"unsupported SQLite schema {version}; expected {self.SCHEMA_VERSION}"
                    )
                self._conn.execute(
                    "SELECT singleton FROM run_state WHERE singleton=1"
                ).fetchone()
            else:
                self._create_schema()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise StoreError(f"invalid SQLite state: {exc}") from exc
        except Exception:
            if connection is not None:
                connection.close()
            raise

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Connection for package-internal read queries and controlled txns."""

        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _create_schema(self) -> None:
        with self.transaction() as cursor:
            version = int(cursor.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, self.SCHEMA_VERSION):
                raise StoreError(
                    f"unsupported SQLite schema {version}; expected {self.SCHEMA_VERSION}"
                )
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    phase TEXT NOT NULL,
                    terminal_status TEXT,
                    closeout_mode INTEGER NOT NULL DEFAULT 0,
                    status_reason TEXT,
                    frozen_candidate_hash TEXT,
                    final_profile_hash TEXT,
                    final_profile_json TEXT,
                    final_barrier_path TEXT,
                    final_test_status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_checksum TEXT NOT NULL,
                    checksum TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    phase TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL,
                    packet_json TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    available_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT,
                    failure_class TEXT,
                    failure_diagnostic TEXT,
                    parent_task_id TEXT,
                    superseded_by_json TEXT,
                    reservation_id TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_schedulable
                    ON tasks(status, available_at, priority DESC, created_at, task_id);

                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_reserved INTEGER NOT NULL,
                    incremental_cash_reserved INTEGER NOT NULL,
                    provider_cash_reserved INTEGER NOT NULL,
                    wall_seconds_reserved INTEGER NOT NULL,
                    tool_calls_reserved INTEGER NOT NULL,
                    token_actual INTEGER NOT NULL DEFAULT 0,
                    incremental_cash_actual INTEGER NOT NULL DEFAULT 0,
                    provider_cash_actual INTEGER NOT NULL DEFAULT 0,
                    wall_seconds_actual INTEGER NOT NULL DEFAULT 0,
                    tool_calls_actual INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS reservations_status
                    ON reservations(status, provider, stage);

                CREATE TABLE IF NOT EXISTS dev_iterations (
                    iteration_no INTEGER PRIMARY KEY,
                    candidate_hash TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    gain_pp REAL NOT NULL,
                    cash_microusd INTEGER NOT NULL,
                    elapsed_seconds INTEGER NOT NULL,
                    low_gain INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_hash TEXT PRIMARY KEY,
                    artifact_path TEXT NOT NULL,
                    dev_score REAL NOT NULL,
                    metrics_json TEXT NOT NULL,
                    is_best INTEGER NOT NULL DEFAULT 0,
                    immutable INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_best_candidate
                    ON candidates(is_best) WHERE is_best = 1;

                CREATE TABLE IF NOT EXISTS provider_requests (
                    request_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_category TEXT,
                    latency_ms INTEGER,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    provider_request_id TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_requests_provider
                    ON provider_requests(provider, finished_at);
                """
            )
            cursor.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
            now = to_iso(utc_now())
            cursor.execute(
                """INSERT OR IGNORE INTO run_state(
                       singleton, phase, final_test_status, updated_at
                   ) VALUES (1, ?, ?, ?)""",
                (RunPhase.BOOTSTRAP.value, FinalTestStatus.NOT_FROZEN.value, now),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                yield cursor
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                cursor.close()

    @contextmanager
    def read_snapshot(self) -> Iterator[None]:
        """Hold one consistent SQLite snapshot across a multi-query report."""

        with self._lock:
            if self._conn.in_transaction:
                raise StoreError("cannot nest a status snapshot in another transaction")
            self._conn.execute("BEGIN")
            try:
                yield
            finally:
                self._conn.rollback()

    def _append_event(
        self,
        cursor: sqlite3.Cursor,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        occurred_at: datetime | None = None,
    ) -> int:
        timestamp = to_iso(occurred_at or utc_now())
        previous = cursor.execute(
            "SELECT seq, checksum FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq = (int(previous["seq"]) + 1) if previous else 1
        previous_checksum = str(previous["checksum"]) if previous else "GENESIS"
        payload_json = canonical_json(dict(payload or {}))
        material = "\x1f".join(
            (
                previous_checksum,
                str(seq),
                timestamp,
                event_type,
                entity_type,
                entity_id,
                payload_json,
            )
        )
        checksum = sha256(material.encode("utf-8")).hexdigest()
        cursor.execute(
            """INSERT INTO events(
                   seq, occurred_at, event_type, entity_type, entity_id,
                   payload_json, previous_checksum, checksum
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                seq,
                timestamp,
                event_type,
                entity_type,
                entity_id,
                payload_json,
                previous_checksum,
                checksum,
            ),
        )
        cursor.execute(
            "UPDATE run_state SET updated_at=? WHERE singleton=1",
            (timestamp,),
        )
        return seq

    def append_event(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        with self.transaction() as cursor:
            return self._append_event(cursor, event_type, entity_type, entity_id, payload)

    def bind_run(
        self,
        *,
        run_id: str,
        schema_version: str,
        config_fingerprint: str,
        deadline_at: datetime,
        started_at: datetime | None = None,
        control_limits: Mapping[str, int | float] | None = None,
    ) -> None:
        if not run_id or not schema_version or not config_fingerprint:
            raise ValueError("run binding fields must be non-empty")
        values = {
            "run_id": run_id,
            "protocol_schema_version": schema_version,
            "config_fingerprint": config_fingerprint,
            "deadline_at": to_iso(deadline_at),
        }
        for key, value in dict(control_limits or {}).items():
            if key not in {
                "max_dev_iterations",
                "low_gain_patience",
                "min_dev_gain_pp",
                "max_cash_per_gain_pp_microusd",
            }:
                raise ValueError(f"unknown run control limit: {key}")
            values[f"control_{key}"] = str(value)
        with self.transaction() as cursor:
            existing = {
                row["key"]: row["value"]
                for row in cursor.execute(
                    "SELECT key, value FROM meta WHERE key IN ("
                    + ",".join("?" for _ in values)
                    + ")",
                    tuple(values),
                )
            }
            for key, value in values.items():
                if key in existing and existing[key] != value:
                    raise ImmutableRunError(
                        f"ledger is already bound to a different {key}"
                    )
            first_bind = "run_id" not in existing
            for key, value in values.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (key, value)
                )
            if cursor.execute(
                "SELECT 1 FROM meta WHERE key='run_started_at'"
            ).fetchone() is None:
                cursor.execute(
                    "INSERT INTO meta(key, value) VALUES ('run_started_at', ?)",
                    (to_iso(started_at or utc_now()),),
                )
            if first_bind:
                self._append_event(
                    cursor,
                    "RUN_BOUND",
                    "run",
                    run_id,
                    {"schema_version": schema_version, "config": config_fingerprint},
                )

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def list_events(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events ORDER BY seq"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql = "SELECT * FROM events ORDER BY seq DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        if limit is not None:
            rows = list(reversed(rows))
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def verify_event_chain(self) -> bool:
        previous_checksum = "GENESIS"
        expected_seq = 1
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        for row in rows:
            if row["seq"] != expected_seq or row["previous_checksum"] != previous_checksum:
                return False
            material = "\x1f".join(
                (
                    previous_checksum,
                    str(row["seq"]),
                    row["occurred_at"],
                    row["event_type"],
                    row["entity_type"],
                    row["entity_id"],
                    row["payload_json"],
                )
            )
            checksum = sha256(material.encode("utf-8")).hexdigest()
            if checksum != row["checksum"]:
                return False
            previous_checksum = checksum
            expected_seq += 1
        return True

    def get_run_state(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
        if row is None:
            raise StoreError("run state is missing")
        return dict(row)

    def transition_run_phase(
        self, phase: RunPhase | str, *, reason: str | None = None
    ) -> None:
        from .state_machine import validate_run_transition

        target = RunPhase(phase)
        if target is RunPhase.TERMINAL:
            raise ImmutableRunError(
                "terminal phase requires mark_terminal or validated finish_final_test"
            )
        with self.transaction() as cursor:
            row = cursor.execute(
                "SELECT phase, terminal_status FROM run_state WHERE singleton=1"
            ).fetchone()
            current = RunPhase(row["phase"])
            if row["terminal_status"] is not None:
                raise ImmutableRunError("terminal run cannot change phase")
            validate_run_transition(current, target)
            now = to_iso(utc_now())
            cursor.execute(
                "UPDATE run_state SET phase=?, status_reason=?, updated_at=? WHERE singleton=1",
                (target.value, reason, now),
            )
            self._append_event(
                cursor,
                "RUN_PHASE_CHANGED",
                "run",
                self.get_meta("run_id") or "unbound",
                {"from": current.value, "to": target.value, "reason": reason},
            )

    def set_closeout_mode(self, reason: str) -> None:
        if not reason:
            raise ValueError("closeout reason is required")
        with self.transaction() as cursor:
            row = cursor.execute(
                "SELECT closeout_mode FROM run_state WHERE singleton=1"
            ).fetchone()
            if row["closeout_mode"]:
                return
            now = to_iso(utc_now())
            cursor.execute(
                """UPDATE run_state SET closeout_mode=1, status_reason=?, updated_at=?
                   WHERE singleton=1""",
                (reason, now),
            )
            self._append_event(
                cursor,
                "CLOSEOUT_ENTERED",
                "run",
                self.get_meta("run_id") or "unbound",
                {"reason": reason},
            )

    def mark_terminal(self, status: TerminalStatus | str, *, reason: str) -> None:
        target = TerminalStatus(status)
        if not reason:
            raise ValueError("terminal reason is required")
        if target in {
            TerminalStatus.SUCCESS,
            TerminalStatus.SUCCEEDED,
            TerminalStatus.VALID_TEST_FAILED,
            TerminalStatus.INCONCLUSIVE,
        }:
            raise ImmutableRunError(
                f"{target.value} may only be produced by a validated finish_final_test result"
            )
        with self.transaction() as cursor:
            row = cursor.execute(
                "SELECT terminal_status FROM run_state WHERE singleton=1"
            ).fetchone()
            if row["terminal_status"] is not None:
                if row["terminal_status"] == target.value:
                    return
                raise ImmutableRunError("terminal status is immutable")
            now = to_iso(utc_now())
            cursor.execute(
                """UPDATE run_state SET phase=?, terminal_status=?, status_reason=?,
                       updated_at=? WHERE singleton=1""",
                (RunPhase.TERMINAL.value, target.value, reason, now),
            )
            self._append_event(
                cursor,
                "RUN_TERMINATED",
                "run",
                self.get_meta("run_id") or "unbound",
                {"status": target.value, "reason": reason},
            )

    def register_candidate(
        self,
        candidate_hash: str,
        artifact_path: str,
        dev_score: float,
        metrics: Mapping[str, Any],
    ) -> None:
        if re.fullmatch(r"[a-f0-9]{64}", candidate_hash or "") is None:
            raise ValueError("candidate_hash must be a lowercase SHA-256")
        if not artifact_path:
            raise ValueError("artifact_path is required")
        metrics_json = canonical_json(dict(metrics))
        with self.transaction() as cursor:
            run = cursor.execute(
                "SELECT final_test_status FROM run_state WHERE singleton=1"
            ).fetchone()
            existing = cursor.execute(
                "SELECT * FROM candidates WHERE candidate_hash=?", (candidate_hash,)
            ).fetchone()
            if run["final_test_status"] != FinalTestStatus.NOT_FROZEN.value:
                if existing is not None and (
                    existing["artifact_path"], existing["dev_score"], existing["metrics_json"]
                ) == (artifact_path, float(dev_score), metrics_json):
                    return
                raise ImmutableRunError("candidate set is frozen for final test")
            now = to_iso(utc_now())
            if existing is None:
                cursor.execute(
                    """INSERT INTO candidates(
                           candidate_hash, artifact_path, dev_score, metrics_json,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (candidate_hash, artifact_path, float(dev_score), metrics_json, now, now),
                )
                event = "CANDIDATE_REGISTERED"
            else:
                if existing["immutable"]:
                    raise ImmutableRunError("candidate is immutable")
                cursor.execute(
                    """UPDATE candidates SET artifact_path=?, dev_score=?, metrics_json=?,
                           updated_at=? WHERE candidate_hash=?""",
                    (artifact_path, float(dev_score), metrics_json, now, candidate_hash),
                )
                event = "CANDIDATE_UPDATED"
            self._append_event(
                cursor,
                event,
                "candidate",
                candidate_hash,
                {"artifact_path": artifact_path, "dev_score": float(dev_score)},
            )

    def promote_best_candidate(self, candidate_hash: str) -> bool:
        with self.transaction() as cursor:
            run = cursor.execute(
                "SELECT final_test_status FROM run_state WHERE singleton=1"
            ).fetchone()
            if run["final_test_status"] != FinalTestStatus.NOT_FROZEN.value:
                raise ImmutableRunError("best candidate is frozen")
            candidate = cursor.execute(
                "SELECT * FROM candidates WHERE candidate_hash=?", (candidate_hash,)
            ).fetchone()
            if candidate is None:
                raise StoreError(f"unknown candidate: {candidate_hash}")
            best = cursor.execute(
                "SELECT * FROM candidates WHERE is_best=1"
            ).fetchone()
            should_promote = best is None or (
                float(candidate["dev_score"]), candidate_hash
            ) > (float(best["dev_score"]), best["candidate_hash"])
            if not should_promote:
                return False
            cursor.execute("UPDATE candidates SET is_best=0 WHERE is_best=1")
            cursor.execute(
                "UPDATE candidates SET is_best=1, updated_at=? WHERE candidate_hash=?",
                (to_iso(utc_now()), candidate_hash),
            )
            self._append_event(
                cursor,
                "BEST_CANDIDATE_PROMOTED",
                "candidate",
                candidate_hash,
                {
                    "dev_score": float(candidate["dev_score"]),
                    "previous": best["candidate_hash"] if best else None,
                },
            )
            return True

    def best_candidate(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM candidates WHERE is_best=1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metrics"] = json.loads(result.pop("metrics_json"))
        return result

    def freeze_final_test(
        self,
        candidate_hash: str,
        profile: Mapping[str, Any],
        *,
        config: Any,
        runtime_report: Mapping[str, Any],
        capability_manifest: Mapping[str, Any],
        dependency_lock_bytes: bytes,
        launcher_attestation_key: bytes,
        resolved_artifacts: Mapping[str, bytes],
    ) -> None:
        """Cross-validate and bind the complete final-readiness bundle."""

        from .config import RunConfig
        from .readiness import validate_final_readiness
        from .runtime import runtime_report_hash
        from .security import capability_manifest_hash

        if not isinstance(config, RunConfig):
            raise StoreError("freeze_final_test requires the exact loaded RunConfig")
        if self.get_meta("run_id") != config.run_id or self.get_meta("config_fingerprint") != config.fingerprint():
            raise StoreError("freeze bundle config does not match the bound controller")
        readiness = validate_final_readiness(
            profile=profile,
            config=config,
            runtime_report=runtime_report,
            capability_manifest=capability_manifest,
            dependency_lock_bytes=dependency_lock_bytes,
            launcher_attestation_key=launcher_attestation_key,
            resolved_artifacts=resolved_artifacts,
        )
        try:
            readiness.raise_for_errors()
        except ValueError as exc:
            raise StoreError(f"final readiness bundle is invalid: {exc}") from exc
        if readiness.profile is None:
            raise StoreError("final readiness validator returned no frozen profile")
        canonical_profile = dict(readiness.profile)
        dependency_lock_sha256 = sha256(dependency_lock_bytes).hexdigest()
        profile_hash = canonical_profile["profile_sha256"]
        profile_json = canonical_json(canonical_profile)
        receipt = {
            "profile_sha256": profile_hash,
            "run_config_sha256": config.fingerprint(),
            "runtime_capability_report_sha256": runtime_report_hash(runtime_report),
            "capability_manifest_sha256": capability_manifest_hash(capability_manifest),
            "dependency_lock_sha256": dependency_lock_sha256,
            "resolved_artifacts": {
                path: sha256(content).hexdigest()
                for path, content in sorted(resolved_artifacts.items())
            },
            "launcher_attestation": dict(runtime_report["launcher_attestation"]),
        }
        receipt["receipt_sha256"] = sha256(
            canonical_json(receipt).encode("utf-8")
        ).hexdigest()
        receipt_json = canonical_json(receipt)
        if canonical_profile["candidate"]["core_sha256"] != candidate_hash:
            raise StoreError("frozen profile candidate does not match the selected candidate")
        if canonical_profile["run_id"] != self.get_meta("run_id"):
            raise StoreError("frozen profile run_id does not match the bound controller run")
        with self.transaction() as cursor:
            run = cursor.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
            if run["final_test_status"] != FinalTestStatus.NOT_FROZEN.value:
                if (
                    run["frozen_candidate_hash"] == candidate_hash
                    and run["final_profile_hash"] == profile_hash
                    and run["final_profile_json"] == profile_json
                    and self.get_meta("final_readiness_receipt") == receipt_json
                ):
                    return
                raise ImmutableRunError("final test profile is already frozen")
            if RunPhase(run["phase"]) not in {RunPhase.FREEZE, RunPhase.CLOSEOUT}:
                raise StoreError("final profile may be frozen only in FREEZE or CLOSEOUT")
            candidate = cursor.execute(
                "SELECT * FROM candidates WHERE candidate_hash=?", (candidate_hash,)
            ).fetchone()
            if candidate is None:
                raise StoreError(f"unknown candidate: {candidate_hash}")
            now = to_iso(utc_now())
            cursor.execute(
                "UPDATE candidates SET is_best=0 WHERE candidate_hash<>?", (candidate_hash,)
            )
            cursor.execute(
                "UPDATE candidates SET immutable=1, is_best=1, updated_at=? WHERE candidate_hash=?",
                (now, candidate_hash),
            )
            cursor.execute(
                """UPDATE run_state SET frozen_candidate_hash=?, final_profile_hash=?,
                       final_profile_json=?, final_test_status=?, updated_at=?
                   WHERE singleton=1""",
                (
                    candidate_hash,
                    profile_hash,
                    profile_json,
                    FinalTestStatus.FROZEN.value,
                    now,
                ),
            )
            cursor.execute(
                "INSERT INTO meta(key, value) VALUES ('final_readiness_receipt', ?)",
                (receipt_json,),
            )
            self._append_event(
                cursor,
                "FINAL_TEST_FROZEN",
                "run",
                self.get_meta("run_id") or "unbound",
                {
                    "candidate_hash": candidate_hash,
                    "profile_hash": profile_hash,
                    "readiness_receipt_sha256": receipt["receipt_sha256"],
                },
            )

    def start_final_test(
        self,
        barrier_path: str | Path,
        *,
        expected_sample_ids: Sequence[str],
    ):
        """Create/open the one barrier and move the controller into FINAL_TEST."""

        from .final_test import FinalTestBarrier, FinalTestStatus as BarrierStatus

        target_path = str(Path(barrier_path).resolve())
        run = self.get_run_state()
        if not run["final_profile_json"]:
            raise StoreError("no complete frozen evaluation profile is bound")
        profile = json.loads(run["final_profile_json"])
        if run["final_barrier_path"] not in (None, target_path):
            raise ImmutableRunError("final-test barrier path is immutable")
        if run["final_test_status"] == FinalTestStatus.RUNNING.value:
            return FinalTestBarrier.open(
                target_path,
                profile=profile,
                expected_sample_ids=expected_sample_ids,
            )
        if run["final_test_status"] == FinalTestStatus.FROZEN.value:
            barrier = FinalTestBarrier.start(
                target_path,
                profile=profile,
                expected_sample_ids=expected_sample_ids,
            )
        elif run["final_test_status"] == FinalTestStatus.INFRA_INCOMPLETE.value:
            barrier = FinalTestBarrier.open(
                target_path,
                profile=profile,
                expected_sample_ids=expected_sample_ids,
            )
            if barrier.status is BarrierStatus.INFRA_INCOMPLETE:
                barrier.resume()
            elif barrier.status is not BarrierStatus.RUNNING:
                raise StoreError("only the same INFRA_INCOMPLETE barrier may resume")
        else:
            raise StoreError(
                f"cannot start final test from {run['final_test_status']}"
            )
        with self.transaction() as cursor:
            run = cursor.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
            allowed = {
                FinalTestStatus.FROZEN.value,
                FinalTestStatus.INFRA_INCOMPLETE.value,
            }
            if run["final_test_status"] not in allowed:
                raise StoreError(
                    f"cannot start final test from {run['final_test_status']}"
                )
            current_phase = RunPhase(run["phase"])
            if current_phase not in {RunPhase.FREEZE, RunPhase.CLOSEOUT, RunPhase.FINAL_TEST}:
                raise StoreError("final test may start only after FREEZE or from CLOSEOUT")
            now = to_iso(utc_now())
            cursor.execute(
                """UPDATE run_state SET phase=?, final_test_status=?,
                       final_barrier_path=?, updated_at=? WHERE singleton=1""",
                (RunPhase.FINAL_TEST.value, FinalTestStatus.RUNNING.value, target_path, now),
            )
            self._append_event(
                cursor,
                "FINAL_TEST_STARTED",
                "run",
                self.get_meta("run_id") or "unbound",
                {
                    "candidate_hash": run["frozen_candidate_hash"],
                    "profile_hash": run["final_profile_hash"],
                    "barrier_path": target_path,
                    "resume": run["final_test_status"] == FinalTestStatus.INFRA_INCOMPLETE.value,
                },
            )
        return barrier

    def finish_final_test(
        self,
        *,
        reason: str,
    ) -> None:
        """Synchronize the controller from the persisted barrier outcome.

        The caller supplies no outcome or evidence fields.  The barrier state,
        frozen profile, completed tensor, and scorer report are re-opened and
        checked before the terminal status is derived.
        """

        from .final_test import FinalTestBarrier, FinalTestStatus as BarrierStatus

        if not reason:
            raise ValueError("final-test closeout reason is required")
        run_snapshot = self.get_run_state()
        if run_snapshot["final_test_status"] != FinalTestStatus.RUNNING.value:
            raise StoreError("final test is not running")
        if not run_snapshot["final_profile_json"] or not run_snapshot["final_barrier_path"]:
            raise StoreError("final-test profile/barrier binding is incomplete")
        profile = json.loads(run_snapshot["final_profile_json"])
        barrier = FinalTestBarrier.open(
            run_snapshot["final_barrier_path"], profile=profile
        )
        barrier_to_store = {
            BarrierStatus.SUCCESS: FinalTestStatus.SUCCESS,
            BarrierStatus.VALID_TEST_FAILED: FinalTestStatus.VALID_TEST_FAILED,
            BarrierStatus.INCONCLUSIVE: FinalTestStatus.INCONCLUSIVE,
            BarrierStatus.INFRA_INCOMPLETE: FinalTestStatus.INFRA_INCOMPLETE,
            BarrierStatus.PROTOCOL_INVALID: FinalTestStatus.PROTOCOL_INVALID,
        }
        if barrier.status is BarrierStatus.RUNNING:
            raise StoreError("barrier has not reached a final or infrastructure outcome")
        target = barrier_to_store[barrier.status]
        terminal_map = {
            FinalTestStatus.SUCCESS: TerminalStatus.SUCCESS,
            FinalTestStatus.VALID_TEST_FAILED: TerminalStatus.VALID_TEST_FAILED,
            FinalTestStatus.INCONCLUSIVE: TerminalStatus.INCONCLUSIVE,
            FinalTestStatus.PROTOCOL_INVALID: TerminalStatus.PROTOCOL_INVALID,
        }
        barrier_state = barrier.state
        expected_keys = barrier_state["immutable"]["expected_keys"]
        results = barrier_state["results"]
        evidence = {
            "candidate_hash": profile["candidate"]["core_sha256"],
            "profile_hash": profile["profile_sha256"],
            "barrier_status": barrier.status.value,
            "barrier_state_checksum": barrier_state["state_checksum"],
            "report_sha256": barrier_state["report_hash"],
            "expected_result_keys": len(expected_keys),
            "completed_result_keys": len(results),
            "expected_samples": len({item[0] for item in expected_keys}),
        }
        if target in {
            FinalTestStatus.SUCCESS,
            FinalTestStatus.VALID_TEST_FAILED,
            FinalTestStatus.INCONCLUSIVE,
        }:
            if evidence["completed_result_keys"] != evidence["expected_result_keys"]:
                raise StoreError("valid final outcome requires the complete result tensor")
            if not isinstance(evidence["report_sha256"], str) or re.fullmatch(
                r"[a-f0-9]{64}", evidence["report_sha256"]
            ) is None:
                raise StoreError("valid final outcome requires a scorer report hash")
        with self.transaction() as cursor:
            run = cursor.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
            if run["final_test_status"] != FinalTestStatus.RUNNING.value:
                raise StoreError("final test is not running")
            if evidence["candidate_hash"] != run["frozen_candidate_hash"]:
                raise StoreError("barrier candidate hash mismatch")
            if evidence["profile_hash"] != run["final_profile_hash"]:
                raise StoreError("barrier profile hash mismatch")
            now = to_iso(utc_now())
            terminal = terminal_map.get(target)
            cursor.execute(
                """UPDATE run_state SET final_test_status=?, terminal_status=?,
                       phase=?, status_reason=?, updated_at=? WHERE singleton=1""",
                (
                    target.value,
                    terminal.value if terminal else None,
                    RunPhase.TERMINAL.value if terminal else run["phase"],
                    reason,
                    now,
                ),
            )
            self._append_event(
                cursor,
                "FINAL_TEST_FINISHED",
                "run",
                self.get_meta("run_id") or "unbound",
                {"status": target.value, "reason": reason, "result": evidence},
            )

    def record_dev_iteration(
        self,
        *,
        candidate_hash: str,
        hypothesis: str,
        gain_pp: float,
        cash_microusd: int,
        elapsed_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist one dev experiment and deterministically evaluate stop rules."""

        if not candidate_hash or not hypothesis:
            raise ValueError("candidate_hash and hypothesis are required")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (cash_microusd, elapsed_seconds)
        ):
            raise ValueError("cash and elapsed values must be non-negative integers")
        instant = now or utc_now()
        with self.transaction() as cursor:
            controls = {
                row["key"].removeprefix("control_"): row["value"]
                for row in cursor.execute(
                    "SELECT key, value FROM meta WHERE key LIKE 'control_%'"
                ).fetchall()
            }
            required_controls = {
                "max_dev_iterations",
                "low_gain_patience",
                "min_dev_gain_pp",
                "max_cash_per_gain_pp_microusd",
            }
            if set(controls) != required_controls:
                raise StoreError("authoritative dev iteration controls are not bound")
            max_iterations = int(controls["max_dev_iterations"])
            low_gain_patience = int(controls["low_gain_patience"])
            min_gain_pp = float(controls["min_dev_gain_pp"])
            max_cash_per_gain_pp_microusd = int(
                controls["max_cash_per_gain_pp_microusd"]
            )
            rows = cursor.execute(
                "SELECT * FROM dev_iterations ORDER BY iteration_no"
            ).fetchall()
            low_streak = 0
            for row in reversed(rows):
                if not row["low_gain"]:
                    break
                low_streak += 1
            if len(rows) >= max_iterations:
                raise StoreError("maximum dev iterations already reached")
            if low_streak >= low_gain_patience:
                raise StoreError("low-gain patience already exhausted")
            expensive_gain = (
                gain_pp > 0
                and cash_microusd / gain_pp > max_cash_per_gain_pp_microusd
            )
            low_gain = gain_pp < min_gain_pp or expensive_gain
            iteration_no = len(rows) + 1
            cursor.execute(
                """INSERT INTO dev_iterations(
                       iteration_no, candidate_hash, hypothesis, gain_pp,
                       cash_microusd, elapsed_seconds, low_gain, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    iteration_no,
                    candidate_hash,
                    hypothesis,
                    float(gain_pp),
                    cash_microusd,
                    elapsed_seconds,
                    int(low_gain),
                    to_iso(instant),
                ),
            )
            next_low_streak = low_streak + 1 if low_gain else 0
            stop = iteration_no >= max_iterations or next_low_streak >= low_gain_patience
            self._append_event(
                cursor,
                "DEV_ITERATION_RECORDED",
                "candidate",
                candidate_hash,
                {
                    "iteration": iteration_no,
                    "gain_pp": float(gain_pp),
                    "cash_microusd": cash_microusd,
                    "low_gain": low_gain,
                    "stop": stop,
                },
                occurred_at=instant,
            )
            if stop:
                self._append_event(
                    cursor,
                    "DEV_STOP_TRIGGERED",
                    "run",
                    self.get_meta("run_id") or "unbound",
                    {
                        "iteration": iteration_no,
                        "reason": "max_iterations" if iteration_no >= max_iterations else "low_gain_patience",
                    },
                    occurred_at=instant,
                )
            return {
                "iteration": iteration_no,
                "low_gain": low_gain,
                "low_gain_streak": next_low_streak,
                "stop": stop,
            }

    def dev_iteration_state(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT low_gain FROM dev_iterations ORDER BY iteration_no"
            ).fetchall()
        streak = 0
        for row in reversed(rows):
            if not row["low_gain"]:
                break
            streak += 1
        return {"iterations": len(rows), "low_gain_streak": streak}

    def record_provider_request(self, record: Mapping[str, Any]) -> None:
        required = (
            "request_id",
            "provider",
            "model",
            "status",
            "started_at",
            "finished_at",
        )
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"provider request missing: {', '.join(missing)}")
        with self.transaction() as cursor:
            cursor.execute(
                """INSERT INTO provider_requests(
                       request_id, task_id, provider, model, status, error_category,
                       latency_ms, input_tokens, output_tokens, cache_hit, retry_count,
                       provider_request_id, started_at, finished_at, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["request_id"],
                    record.get("task_id"),
                    record["provider"],
                    record["model"],
                    record["status"],
                    record.get("error_category"),
                    record.get("latency_ms"),
                    int(record.get("input_tokens", 0)),
                    int(record.get("output_tokens", 0)),
                    int(bool(record.get("cache_hit", False))),
                    int(record.get("retry_count", 0)),
                    record.get("provider_request_id"),
                    str(record["started_at"]),
                    str(record["finished_at"]),
                    canonical_json(dict(record.get("metadata", {}))),
                ),
            )
            self._append_event(
                cursor,
                "PROVIDER_REQUEST_RECORDED",
                "request",
                str(record["request_id"]),
                {
                    "provider": record["provider"],
                    "status": record["status"],
                    "error_category": record.get("error_category"),
                },
            )

    def task_counts(self) -> dict[str, int]:
        result = {status.value: 0 for status in TaskStatus}
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
        for row in rows:
            result[row["status"]] = int(row["count"])
        return result

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at, task_id"
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["dependencies"] = json.loads(item.pop("dependencies_json"))
            item["packet"] = json.loads(item.pop("packet_json"))
            if item.get("result_json"):
                item["result"] = json.loads(item["result_json"])
            tasks.append(item)
        return tasks
