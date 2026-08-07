"""Closed task/run state machines.

The controller must reject transitions not listed here.  There is no generic
"set status" escape hatch, which keeps retry, split, terminal and final-test
semantics reviewable.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import RunPhase, TaskStatus


class InvalidTransition(RuntimeError):
    pass


TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.READY: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.RETRY_WAIT,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.RETRY_WAIT: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.SUPERSEDED: frozenset(),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


RUN_TRANSITIONS: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.BOOTSTRAP: frozenset(
        {RunPhase.PROTOCOL, RunPhase.CLOSEOUT, RunPhase.TERMINAL}
    ),
    RunPhase.PROTOCOL: frozenset(
        {RunPhase.CENSUS, RunPhase.CLOSEOUT, RunPhase.TERMINAL}
    ),
    RunPhase.CENSUS: frozenset(
        {RunPhase.VERTICAL_SLICE, RunPhase.CLOSEOUT, RunPhase.TERMINAL}
    ),
    RunPhase.VERTICAL_SLICE: frozenset(
        {
            RunPhase.DEVELOPMENT,
            RunPhase.FREEZE,
            RunPhase.CLOSEOUT,
            RunPhase.TERMINAL,
        }
    ),
    RunPhase.DEVELOPMENT: frozenset(
        {RunPhase.FREEZE, RunPhase.CLOSEOUT, RunPhase.TERMINAL}
    ),
    RunPhase.FREEZE: frozenset(
        {RunPhase.FINAL_TEST, RunPhase.CLOSEOUT, RunPhase.TERMINAL}
    ),
    RunPhase.FINAL_TEST: frozenset(
        {RunPhase.CLOSEOUT, RunPhase.TERMINAL}
    ),
    # Closeout may spend its protected reserve on the already frozen final
    # test.  It may not return to development or mutate the candidate.
    RunPhase.CLOSEOUT: frozenset({RunPhase.FINAL_TEST, RunPhase.TERMINAL}),
    RunPhase.TERMINAL: frozenset(),
}


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUPERSEDED,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)


DEPENDENCY_FAILURE_STATUSES = frozenset(
    {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
)


def validate_task_transition(
    current: TaskStatus | str, target: TaskStatus | str
) -> None:
    current_status = TaskStatus(current)
    target_status = TaskStatus(target)
    if current_status == target_status:
        return
    if target_status not in TASK_TRANSITIONS[current_status]:
        raise InvalidTransition(
            f"task transition {current_status.value} -> {target_status.value} is not allowed"
        )


def validate_run_transition(current: RunPhase | str, target: RunPhase | str) -> None:
    current_phase = RunPhase(current)
    target_phase = RunPhase(target)
    if current_phase == target_phase:
        return
    if target_phase not in RUN_TRANSITIONS[current_phase]:
        raise InvalidTransition(
            f"run transition {current_phase.value} -> {target_phase.value} is not allowed"
        )


def dependency_resolution(statuses: Iterable[TaskStatus | str]) -> TaskStatus:
    """Return the correct dependent state from persisted dependency states."""

    values = tuple(TaskStatus(status) for status in statuses)
    if any(status in DEPENDENCY_FAILURE_STATUSES for status in values):
        return TaskStatus.BLOCKED
    if values and not all(status == TaskStatus.SUCCEEDED for status in values):
        return TaskStatus.PENDING
    return TaskStatus.READY
