"""Deterministic DAG scheduling with leases, retries, split and recovery."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from .budget import BudgetExceeded, BudgetManager
from .config import RunConfig
from .models import (
    BudgetVector,
    FailureClass,
    ReservationStatus,
    RunPhase,
    TaskLease,
    TaskSpec,
    TaskStatus,
)
from .state_machine import InvalidTransition, validate_task_transition
from .store import SQLiteStore, StoreError, canonical_json, from_iso, to_iso, utc_now


class SchedulerError(RuntimeError):
    pass


class LeaseError(SchedulerError):
    pass


class DAGError(SchedulerError):
    pass


class Scheduler:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        config: RunConfig | None = None,
        budget_manager: BudgetManager | None = None,
        lease_seconds: int = 300,
        max_parallel_tasks: int = 1,
    ) -> None:
        if lease_seconds < 1 or max_parallel_tasks < 1:
            raise ValueError("lease_seconds and max_parallel_tasks must be positive")
        self.store = store
        self.config = config or (budget_manager.config if budget_manager else None)
        self.budget_manager = budget_manager
        if budget_manager is not None and budget_manager.store is not store:
            raise ValueError("budget manager and scheduler must share one SQLite store")
        if budget_manager is not None and config is not None and budget_manager.config != config:
            raise ValueError("scheduler and budget manager configurations differ")
        self.lease_seconds = lease_seconds
        self.max_parallel_tasks = max_parallel_tasks

    @staticmethod
    def _coerce_task(task: TaskSpec | Mapping[str, Any]) -> TaskSpec:
        if isinstance(task, TaskSpec):
            return task
        values = dict(task)
        if "deps" in values and "dependencies" not in values:
            values["dependencies"] = values.pop("deps")
        return TaskSpec(**values)

    @staticmethod
    def _load_graph(cursor: sqlite3.Cursor) -> dict[str, tuple[str, ...]]:
        return {
            row["task_id"]: tuple(json.loads(row["dependencies_json"]))
            for row in cursor.execute("SELECT task_id, dependencies_json FROM tasks")
        }

    @staticmethod
    def _assert_acyclic(graph: Mapping[str, Sequence[str]]) -> None:
        known = set(graph)
        unknown = sorted(
            {dependency for dependencies in graph.values() for dependency in dependencies}
            - known
        )
        if unknown:
            raise DAGError("unknown dependencies: " + ", ".join(unknown))
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, trail: tuple[str, ...]) -> None:
            if node in visited:
                return
            if node in visiting:
                cycle = " -> ".join((*trail, node))
                raise DAGError(f"task graph contains a cycle: {cycle}")
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency, (*trail, node))
            visiting.remove(node)
            visited.add(node)

        for task_id in sorted(graph):
            visit(task_id, ())

    @staticmethod
    def _dependency_statuses(
        cursor: sqlite3.Cursor, dependencies: Sequence[str]
    ) -> list[TaskStatus]:
        if not dependencies:
            return []
        placeholders = ",".join("?" for _ in dependencies)
        rows = cursor.execute(
            f"SELECT task_id, status FROM tasks WHERE task_id IN ({placeholders})",
            tuple(dependencies),
        ).fetchall()
        found = {row["task_id"]: TaskStatus(row["status"]) for row in rows}
        missing = [dependency for dependency in dependencies if dependency not in found]
        if missing:
            raise DAGError("unknown dependencies: " + ", ".join(missing))
        return [found[dependency] for dependency in dependencies]

    def _initial_status(
        self, cursor: sqlite3.Cursor, dependencies: Sequence[str]
    ) -> TaskStatus:
        statuses = self._dependency_statuses(cursor, dependencies)
        failed = {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
        if any(status in failed for status in statuses):
            return TaskStatus.BLOCKED
        if all(status == TaskStatus.SUCCEEDED for status in statuses):
            return TaskStatus.READY
        return TaskStatus.PENDING

    def add_tasks(self, tasks: Iterable[TaskSpec | Mapping[str, Any]]) -> list[str]:
        specs = [self._coerce_task(task) for task in tasks]
        if not specs:
            return []
        ids = [spec.task_id for spec in specs]
        if len(ids) != len(set(ids)):
            raise DAGError("task batch contains duplicate ids")
        with self.store.transaction() as cursor:
            existing_ids = {
                row["task_id"]
                for row in cursor.execute(
                    f"SELECT task_id FROM tasks WHERE task_id IN ({','.join('?' for _ in ids)})",
                    tuple(ids),
                )
            }
            if existing_ids:
                raise DAGError("task ids already exist: " + ", ".join(sorted(existing_ids)))
            run = cursor.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
            if run["terminal_status"] is not None:
                raise SchedulerError("cannot add work to a terminal run")
            if run["final_test_status"] != "NOT_FROZEN":
                forbidden = [
                    spec.task_id
                    for spec in specs
                    if spec.phase not in {RunPhase.FINAL_TEST, RunPhase.CLOSEOUT}
                ]
                if forbidden:
                    raise SchedulerError(
                        "candidate-side work is frozen: " + ", ".join(forbidden)
                    )
            graph = self._load_graph(cursor)
            graph.update({spec.task_id: tuple(spec.dependencies) for spec in specs})
            self._assert_acyclic(graph)
            now = to_iso(utc_now())
            # Insert all rows as PENDING first so dependency lookups within the
            # same batch have a complete, atomic graph.
            for spec in specs:
                cursor.execute(
                    """INSERT INTO tasks(
                           task_id, phase, priority, status, dependencies_json,
                           packet_json, max_attempts, created_at, updated_at,
                           parent_task_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        spec.task_id,
                        spec.phase.value,
                        spec.priority,
                        TaskStatus.PENDING.value,
                        canonical_json(list(spec.dependencies)),
                        canonical_json(dict(spec.packet)),
                        spec.max_attempts,
                        now,
                        now,
                        spec.parent_task_id,
                    ),
                )
            # Resolve ready roots; descendants become ready after completion.
            for spec in specs:
                status = self._initial_status(cursor, spec.dependencies)
                cursor.execute(
                    "UPDATE tasks SET status=? WHERE task_id=?",
                    (status.value, spec.task_id),
                )
                self.store._append_event(
                    cursor,
                    "TASK_ADDED",
                    "task",
                    spec.task_id,
                    {
                        "phase": spec.phase.value,
                        "dependencies": list(spec.dependencies),
                        "status": status.value,
                        "priority": spec.priority,
                        "max_attempts": spec.max_attempts,
                    },
                )
            return ids

    def _refresh_locked(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        timestamp = to_iso(now)
        retry_rows = cursor.execute(
            """SELECT * FROM tasks WHERE status=? AND available_at IS NOT NULL
               AND available_at<=? ORDER BY task_id""",
            (TaskStatus.RETRY_WAIT.value, timestamp),
        ).fetchall()
        for row in retry_rows:
            validate_task_transition(TaskStatus.RETRY_WAIT, TaskStatus.READY)
            cursor.execute(
                """UPDATE tasks SET status=?, available_at=NULL, updated_at=?
                   WHERE task_id=?""",
                (TaskStatus.READY.value, timestamp, row["task_id"]),
            )
            self.store._append_event(
                cursor,
                "TASK_RETRY_READY",
                "task",
                row["task_id"],
                {"attempt": row["attempt"]},
                occurred_at=now,
            )

        rows = cursor.execute(
            "SELECT * FROM tasks WHERE status=? ORDER BY created_at, task_id",
            (TaskStatus.PENDING.value,),
        ).fetchall()
        failure_states = {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
        for row in rows:
            dependencies = tuple(json.loads(row["dependencies_json"]))
            statuses = self._dependency_statuses(cursor, dependencies)
            if any(status in failure_states for status in statuses):
                target = TaskStatus.BLOCKED
                failure_class = FailureClass.DEPENDENCY_FAILED.value
                diagnostic = "one or more dependencies cannot succeed"
            elif all(status == TaskStatus.SUCCEEDED for status in statuses):
                target = TaskStatus.READY
                failure_class = None
                diagnostic = None
            else:
                continue
            validate_task_transition(TaskStatus.PENDING, target)
            cursor.execute(
                """UPDATE tasks SET status=?, failure_class=?, failure_diagnostic=?,
                       updated_at=? WHERE task_id=?""",
                (target.value, failure_class, diagnostic, timestamp, row["task_id"]),
            )
            self.store._append_event(
                cursor,
                "TASK_READY" if target == TaskStatus.READY else "TASK_BLOCKED",
                "task",
                row["task_id"],
                {"dependencies": list(dependencies), "reason": diagnostic},
                occurred_at=now,
            )

    def refresh(self, *, now: datetime | None = None) -> None:
        instant = now or utc_now()
        with self.store.transaction() as cursor:
            self._refresh_locked(cursor, instant)

    def claim_ready(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        phase: RunPhase | str | None = None,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> list[TaskLease]:
        raise SchedulerError(
            "direct claims are disabled: use admit_and_claim so reservation and lease are atomic"
        )

    def admit_and_claim(
        self,
        *,
        task_id: str,
        worker_id: str,
        provider: str,
        model: str,
        worst_case: BudgetVector,
        input_tokens: int,
        output_tokens: int,
        idempotency_key: str,
        purpose: str = "exploration",
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> TaskLease:
        """Atomically validate phase/caps, reserve worst case, and issue a lease."""

        if self.config is None or self.budget_manager is None:
            raise SchedulerError("admit_and_claim requires a bound RunConfig and BudgetManager")
        if not task_id or not worker_id or not idempotency_key:
            raise ValueError("task_id, worker_id and idempotency_key are required")
        instant = now or utc_now()
        duration = lease_seconds or self.lease_seconds
        if duration < 1:
            raise ValueError("lease duration must be positive")
        with self.store.transaction() as cursor:
            self._recover_stale_locked(cursor, instant)
            self._refresh_locked(cursor, instant)
            run = cursor.execute("SELECT * FROM run_state WHERE singleton=1").fetchone()
            if run["terminal_status"] is not None:
                raise SchedulerError("cannot dispatch work from a terminal run")
            row = cursor.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise SchedulerError(f"unknown task: {task_id}")

            # A repeated call with the same key is an idempotent read, never a
            # second attempt or second reservation.
            existing = cursor.execute(
                "SELECT * FROM reservations WHERE reservation_id=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None and row["status"] == TaskStatus.RUNNING.value:
                if (
                    row["reservation_id"] != idempotency_key
                    or row["lease_owner"] != worker_id
                    or existing["task_id"] != task_id
                    or existing["provider"] != provider
                    or existing["model"] != model
                    or self.budget_manager._row_vector(existing, "reserved") != worst_case
                ):
                    raise SchedulerError("idempotency key conflicts with an active claim")
                return TaskLease(
                    task_id=task_id,
                    worker_id=worker_id,
                    attempt=int(row["attempt"]),
                    lease_expires_at=from_iso(row["lease_expires_at"]),
                    phase=RunPhase(row["phase"]),
                    packet=json.loads(row["packet_json"]),
                    dependencies=tuple(json.loads(row["dependencies_json"])),
                    reservation_id=idempotency_key,
                    provider=provider,
                    model=model,
                )
            if existing is not None:
                raise SchedulerError("idempotency key already belongs to a non-active claim")
            if row["status"] != TaskStatus.READY.value:
                raise SchedulerError(f"task is {row['status']}, not READY")
            task_phase = RunPhase(row["phase"])
            active_phase = RunPhase(run["phase"])
            if task_phase is not active_phase:
                raise SchedulerError(
                    f"task phase {task_phase.value} does not match active phase {active_phase.value}"
                )
            if run["closeout_mode"] and task_phase not in {RunPhase.CLOSEOUT, RunPhase.FINAL_TEST}:
                raise SchedulerError("closeout mode rejects new exploratory dispatch")
            if task_phase is RunPhase.DEVELOPMENT:
                iteration = self.store.dev_iteration_state()
                if iteration["iterations"] >= self.config.max_dev_iterations:
                    raise SchedulerError("maximum dev iterations reached")
                if iteration["low_gain_streak"] >= self.config.low_gain_patience:
                    raise SchedulerError("low-gain patience exhausted")
            running = int(cursor.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE status=?",
                (TaskStatus.RUNNING.value,),
            ).fetchone()["count"])
            if running >= self.max_parallel_tasks:
                raise SchedulerError("worker capacity is exhausted")

            self.budget_manager._reserve_locked(
                cursor,
                task_id=task_id,
                provider=provider,
                model=model,
                phase=task_phase,
                amount=worst_case,
                purpose=purpose,
                reservation_id=idempotency_key,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                now=instant,
            )
            expiry = instant + timedelta(seconds=duration)
            attempt = int(row["attempt"]) + 1
            validate_task_transition(TaskStatus.READY, TaskStatus.RUNNING)
            cursor.execute(
                """UPDATE tasks SET status=?, attempt=?, lease_owner=?,
                       lease_expires_at=?, started_at=?, updated_at=?, reservation_id=?
                   WHERE task_id=?""",
                (
                    TaskStatus.RUNNING.value,
                    attempt,
                    worker_id,
                    to_iso(expiry),
                    to_iso(instant),
                    to_iso(instant),
                    idempotency_key,
                    task_id,
                ),
            )
            self.store._append_event(
                cursor,
                "TASK_ADMITTED_AND_CLAIMED",
                "task",
                task_id,
                {
                    "worker_id": worker_id,
                    "attempt": attempt,
                    "reservation_id": idempotency_key,
                    "provider": provider,
                    "model": model,
                    "worst_case": worst_case.as_dict(),
                    "lease_expires_at": to_iso(expiry),
                },
                occurred_at=instant,
            )
            return TaskLease(
                task_id=task_id,
                worker_id=worker_id,
                attempt=attempt,
                lease_expires_at=expiry,
                phase=task_phase,
                packet=json.loads(row["packet_json"]),
                dependencies=tuple(json.loads(row["dependencies_json"])),
                reservation_id=idempotency_key,
                provider=provider,
                model=model,
            )

    @staticmethod
    def _assert_owned_running(row: sqlite3.Row | None, worker_id: str) -> None:
        if row is None:
            raise SchedulerError("unknown task")
        if row["status"] != TaskStatus.RUNNING.value:
            raise LeaseError(f"task is {row['status']}, not RUNNING")
        if row["lease_owner"] != worker_id:
            raise LeaseError("worker does not own this lease")

    @staticmethod
    def _assert_reservation_settled(cursor: sqlite3.Cursor, row: sqlite3.Row) -> None:
        reservation_id = row["reservation_id"]
        if not reservation_id:
            raise LeaseError("RUNNING task has no atomic budget reservation")
        reservation = cursor.execute(
            "SELECT status FROM reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if reservation is None or reservation["status"] != ReservationStatus.COMMITTED.value:
            raise LeaseError("settle the task reservation before completing its lease")

    @staticmethod
    def _assert_lease_current(row: sqlite3.Row, now: datetime) -> None:
        if not row["lease_expires_at"] or from_iso(row["lease_expires_at"]) <= now:
            raise LeaseError("lease has expired; recover it before accepting a result")

    def heartbeat(
        self,
        task_id: str,
        worker_id: str,
        *,
        extend_seconds: int | None = None,
        now: datetime | None = None,
    ) -> datetime:
        instant = now or utc_now()
        duration = extend_seconds or self.lease_seconds
        if duration < 1:
            raise ValueError("heartbeat extension must be positive")
        with self.store.transaction() as cursor:
            row = cursor.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            self._assert_owned_running(row, worker_id)
            self._assert_lease_current(row, instant)
            expiry = instant + timedelta(seconds=duration)
            cursor.execute(
                "UPDATE tasks SET lease_expires_at=?, updated_at=? WHERE task_id=?",
                (to_iso(expiry), to_iso(instant), task_id),
            )
            self.store._append_event(
                cursor,
                "TASK_HEARTBEAT",
                "task",
                task_id,
                {"worker_id": worker_id, "lease_expires_at": to_iso(expiry)},
                occurred_at=instant,
            )
            return expiry

    def complete(
        self,
        task_id: str,
        worker_id: str,
        result: Mapping[str, Any] | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        instant = now or utc_now()
        with self.store.transaction() as cursor:
            row = cursor.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            self._assert_owned_running(row, worker_id)
            self._assert_lease_current(row, instant)
            self._assert_reservation_settled(cursor, row)
            validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
            started = from_iso(row["started_at"]) if row["started_at"] else instant
            duration = max(0, (instant - started).total_seconds())
            cursor.execute(
                """UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                       result_json=?, failure_class=NULL, failure_diagnostic=NULL,
                       finished_at=?, updated_at=? WHERE task_id=?""",
                (
                    TaskStatus.SUCCEEDED.value,
                    canonical_json(dict(result or {})),
                    to_iso(instant),
                    to_iso(instant),
                    task_id,
                ),
            )
            self.store._append_event(
                cursor,
                "TASK_SUCCEEDED",
                "task",
                task_id,
                {
                    "worker_id": worker_id,
                    "attempt": row["attempt"],
                    "duration_seconds": duration,
                    "result": dict(result or {}),
                },
                occurred_at=instant,
            )
            self._refresh_locked(cursor, instant)

    def fail(
        self,
        task_id: str,
        worker_id: str,
        failure_class: FailureClass | str,
        *,
        diagnostic: str,
        retryable: bool = False,
        retry_delay_seconds: int = 0,
        now: datetime | None = None,
    ) -> TaskStatus:
        if not diagnostic:
            raise ValueError("diagnostic is required before retry/failure")
        if retry_delay_seconds < 0:
            raise ValueError("retry delay cannot be negative")
        instant = now or utc_now()
        category = (
            failure_class.value
            if isinstance(failure_class, FailureClass)
            else str(failure_class)
        )
        with self.store.transaction() as cursor:
            row = cursor.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            self._assert_owned_running(row, worker_id)
            self._assert_lease_current(row, instant)
            self._assert_reservation_settled(cursor, row)
            can_retry = retryable and int(row["attempt"]) < int(row["max_attempts"])
            target = TaskStatus.RETRY_WAIT if can_retry else TaskStatus.FAILED
            validate_task_transition(TaskStatus.RUNNING, target)
            available = (
                instant + timedelta(seconds=retry_delay_seconds) if can_retry else None
            )
            cursor.execute(
                """UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                       available_at=?, failure_class=?, failure_diagnostic=?,
                       finished_at=?, updated_at=? WHERE task_id=?""",
                (
                    target.value,
                    to_iso(available) if available else None,
                    category,
                    diagnostic,
                    None if can_retry else to_iso(instant),
                    to_iso(instant),
                    task_id,
                ),
            )
            self.store._append_event(
                cursor,
                "TASK_RETRY_SCHEDULED" if can_retry else "TASK_FAILED",
                "task",
                task_id,
                {
                    "worker_id": worker_id,
                    "attempt": row["attempt"],
                    "failure_class": category,
                    "diagnostic": diagnostic,
                    "available_at": to_iso(available) if available else None,
                },
                occurred_at=instant,
            )
            if not can_retry:
                self._refresh_locked(cursor, instant)
            return target

    def recover_stale(self, *, now: datetime | None = None) -> list[str]:
        instant = now or utc_now()
        with self.store.transaction() as cursor:
            recovered = self._recover_stale_locked(cursor, instant)
            self._refresh_locked(cursor, instant)
        return recovered

    def _recover_stale_locked(
        self, cursor: sqlite3.Cursor, instant: datetime
    ) -> list[str]:
        recovered: list[str] = []
        rows = cursor.execute(
            """SELECT * FROM tasks WHERE status=? AND lease_expires_at IS NOT NULL
               AND lease_expires_at<=? ORDER BY task_id""",
            (TaskStatus.RUNNING.value, to_iso(instant)),
        ).fetchall()
        for row in rows:
            reservation_id = row["reservation_id"]
            if reservation_id:
                reservation = cursor.execute(
                    "SELECT * FROM reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                # An unknown crash outcome is charged at its reserved worst
                # case. This is deliberately conservative and prevents retry
                # admission from undercounting a request that reached a provider.
                if reservation is not None and reservation["status"] == ReservationStatus.RESERVED.value:
                    cursor.execute(
                        """UPDATE reservations SET status=?, token_actual=token_reserved,
                               incremental_cash_actual=incremental_cash_reserved,
                               provider_cash_actual=provider_cash_reserved,
                               wall_seconds_actual=wall_seconds_reserved,
                               tool_calls_actual=tool_calls_reserved, updated_at=?
                           WHERE reservation_id=?""",
                        (ReservationStatus.COMMITTED.value, to_iso(instant), reservation_id),
                    )
                    self.store._append_event(
                        cursor,
                        "BUDGET_COMMITTED_ON_LEASE_EXPIRY",
                        "reservation",
                        reservation_id,
                        {"reason": "unknown crash outcome; charged worst case"},
                        occurred_at=instant,
                    )
            retry = int(row["attempt"]) < int(row["max_attempts"])
            target = TaskStatus.RETRY_WAIT if retry else TaskStatus.FAILED
            validate_task_transition(TaskStatus.RUNNING, target)
            cursor.execute(
                """UPDATE tasks SET status=?, lease_owner=NULL,
                       lease_expires_at=NULL, available_at=?, failure_class=?,
                       failure_diagnostic=?, finished_at=?, updated_at=?,
                       reservation_id=NULL WHERE task_id=?""",
                (
                    target.value,
                    to_iso(instant) if retry else None,
                    FailureClass.LEASE_EXPIRED.value,
                    "lease expired and was reclaimed by the controller",
                    None if retry else to_iso(instant),
                    to_iso(instant),
                    row["task_id"],
                ),
            )
            self.store._append_event(
                cursor,
                "TASK_LEASE_RECOVERED" if retry else "TASK_FAILED",
                "task",
                row["task_id"],
                {
                    "previous_worker": row["lease_owner"],
                    "attempt": row["attempt"],
                    "next_status": target.value,
                    "charged_reservation": reservation_id,
                },
                occurred_at=instant,
            )
            recovered.append(row["task_id"])
        return recovered

    def split(
        self,
        task_id: str,
        worker_id: str,
        children: Iterable[TaskSpec | Mapping[str, Any]],
        *,
        reason: str,
        now: datetime | None = None,
    ) -> list[str]:
        if not reason:
            raise ValueError("split reason is required")
        specs = [self._coerce_task(child) for child in children]
        if not specs:
            raise DAGError("split needs at least one child")
        ids = [spec.task_id for spec in specs]
        if task_id in ids or len(ids) != len(set(ids)):
            raise DAGError("split children must have unique ids different from their parent")
        instant = now or utc_now()
        with self.store.transaction() as cursor:
            parent = cursor.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            self._assert_owned_running(parent, worker_id)
            self._assert_lease_current(parent, instant)
            self._assert_reservation_settled(cursor, parent)
            known = self._load_graph(cursor)
            collisions = sorted(set(ids) & set(known))
            if collisions:
                raise DAGError("split child ids already exist: " + ", ".join(collisions))
            inherited = tuple(json.loads(parent["dependencies_json"]))
            normalized: list[TaskSpec] = []
            for spec in specs:
                dependencies = tuple(spec.dependencies) if spec.dependencies else inherited
                if task_id in dependencies:
                    raise DAGError("a split child cannot depend on its superseded parent")
                normalized.append(
                    TaskSpec(
                        task_id=spec.task_id,
                        phase=spec.phase,
                        dependencies=dependencies,
                        packet=spec.packet,
                        priority=spec.priority,
                        max_attempts=spec.max_attempts,
                        parent_task_id=task_id,
                    )
                )
            graph = dict(known)
            # Every downstream node now requires every child.  This is a
            # conservative AND split; a future OR-join must be an explicit task.
            rewired: dict[str, tuple[str, ...]] = {}
            for existing_id, dependencies in graph.items():
                if existing_id == task_id or task_id not in dependencies:
                    continue
                replacement: list[str] = []
                for dependency in dependencies:
                    if dependency == task_id:
                        replacement.extend(ids)
                    else:
                        replacement.append(dependency)
                rewired[existing_id] = tuple(dict.fromkeys(replacement))
                graph[existing_id] = rewired[existing_id]
            graph.update({spec.task_id: tuple(spec.dependencies) for spec in normalized})
            self._assert_acyclic(graph)
            timestamp = to_iso(instant)
            for existing_id, dependencies in rewired.items():
                cursor.execute(
                    "UPDATE tasks SET dependencies_json=?, updated_at=? WHERE task_id=?",
                    (canonical_json(list(dependencies)), timestamp, existing_id),
                )
            for spec in normalized:
                cursor.execute(
                    """INSERT INTO tasks(
                           task_id, phase, priority, status, dependencies_json,
                           packet_json, max_attempts, created_at, updated_at,
                           parent_task_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        spec.task_id,
                        spec.phase.value,
                        spec.priority,
                        TaskStatus.PENDING.value,
                        canonical_json(list(spec.dependencies)),
                        canonical_json(dict(spec.packet)),
                        spec.max_attempts,
                        timestamp,
                        timestamp,
                        task_id,
                    ),
                )
            validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUPERSEDED)
            cursor.execute(
                """UPDATE tasks SET status=?, lease_owner=NULL, lease_expires_at=NULL,
                       superseded_by_json=?, result_json=?, finished_at=?, updated_at=?
                   WHERE task_id=?""",
                (
                    TaskStatus.SUPERSEDED.value,
                    canonical_json(ids),
                    canonical_json({"reason": reason}),
                    timestamp,
                    timestamp,
                    task_id,
                ),
            )
            self.store._append_event(
                cursor,
                "TASK_SPLIT",
                "task",
                task_id,
                {"children": ids, "reason": reason, "rewired": sorted(rewired)},
                occurred_at=instant,
            )
            for spec in normalized:
                status = self._initial_status(cursor, spec.dependencies)
                cursor.execute(
                    "UPDATE tasks SET status=? WHERE task_id=?",
                    (status.value, spec.task_id),
                )
                self.store._append_event(
                    cursor,
                    "TASK_ADDED",
                    "task",
                    spec.task_id,
                    {
                        "phase": spec.phase.value,
                        "dependencies": list(spec.dependencies),
                        "status": status.value,
                        "parent_task_id": task_id,
                    },
                    occurred_at=instant,
                )
            self._refresh_locked(cursor, instant)
        return ids

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise SchedulerError(f"unknown task: {task_id}")
        item = dict(row)
        item["dependencies"] = json.loads(item.pop("dependencies_json"))
        item["packet"] = json.loads(item.pop("packet_json"))
        return item
