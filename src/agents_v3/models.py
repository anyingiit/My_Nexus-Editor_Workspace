"""Stable value objects shared by the deterministic control plane.

Money is represented as integer micro-US-dollars.  Persisting floats in the
ledger would make budget comparisons platform dependent and could allow a
concurrent request to slip past a nominal cash ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Any, Mapping, Sequence


MICRO_USD = 1_000_000


class RunPhase(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"
    PROTOCOL = "PROTOCOL"
    CENSUS = "CENSUS"
    VERTICAL_SLICE = "VERTICAL_SLICE"
    DEVELOPMENT = "DEVELOPMENT"
    FREEZE = "FREEZE"
    FINAL_TEST = "FINAL_TEST"
    CLOSEOUT = "CLOSEOUT"
    TERMINAL = "TERMINAL"


class TerminalStatus(StrEnum):
    SUCCESS = "SUCCESS"
    # Accepted only so an early v2 ledger can be diagnosed/migrated. New
    # validated runs always persist SUCCESS.
    SUCCEEDED = "SUCCEEDED"
    VALID_TEST_FAILED = "VALID_TEST_FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INFRA_INCOMPLETE = "INFRA_INCOMPLETE"
    PROTOCOL_INVALID = "PROTOCOL_INVALID"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    BLOCKED = "BLOCKED"


class FinalTestStatus(StrEnum):
    NOT_FROZEN = "NOT_FROZEN"
    FROZEN = "FROZEN"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    # Legacy spelling accepted at the store boundary and normalized to SUCCESS.
    PASSED = "PASSED"
    VALID_TEST_FAILED = "VALID_TEST_FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INFRA_INCOMPLETE = "INFRA_INCOMPLETE"
    PROTOCOL_INVALID = "PROTOCOL_INVALID"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"


class FailureClass(StrEnum):
    TRANSPORT = "transport"
    RATE_LIMIT = "rate_limit"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    TIMEOUT = "timeout"
    EMPTY_RESPONSE = "empty_response"
    TRUNCATED = "truncated"
    CONTEXT_OVERFLOW = "context_overflow"
    JSON_SCHEMA = "json_schema"
    SAFETY_REFUSAL = "safety_refusal"
    SEMANTIC_INVALID = "semantic_invalid"
    DETERMINISTIC_CHECK = "deterministic_check"
    DEPENDENCY_FAILED = "dependency_failed"
    LEASE_EXPIRED = "lease_expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BudgetVector:
    tokens: int = 0
    incremental_cash_microusd: int = 0
    provider_cash_microusd: int = 0
    wall_seconds: int = 0
    tool_calls: int = 0

    def __post_init__(self) -> None:
        values = (
            self.tokens,
            self.incremental_cash_microusd,
            self.provider_cash_microusd,
            self.wall_seconds,
            self.tool_calls,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise TypeError("budget values must be integers")
        if any(value < 0 for value in values):
            raise ValueError("budget values must be non-negative")

    def __add__(self, other: "BudgetVector") -> "BudgetVector":
        return BudgetVector(
            self.tokens + other.tokens,
            self.incremental_cash_microusd + other.incremental_cash_microusd,
            self.provider_cash_microusd + other.provider_cash_microusd,
            self.wall_seconds + other.wall_seconds,
            self.tool_calls + other.tool_calls,
        )

    def __sub__(self, other: "BudgetVector") -> "BudgetVector":
        return BudgetVector(
            max(0, self.tokens - other.tokens),
            max(0, self.incremental_cash_microusd - other.incremental_cash_microusd),
            max(0, self.provider_cash_microusd - other.provider_cash_microusd),
            max(0, self.wall_seconds - other.wall_seconds),
            max(0, self.tool_calls - other.tool_calls),
        )

    def fits_within(self, ceiling: "BudgetVector") -> bool:
        return (
            self.tokens <= ceiling.tokens
            and self.incremental_cash_microusd <= ceiling.incremental_cash_microusd
            and self.provider_cash_microusd <= ceiling.provider_cash_microusd
            and self.wall_seconds <= ceiling.wall_seconds
            and self.tool_calls <= ceiling.tool_calls
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "tokens": self.tokens,
            "incremental_cash_microusd": self.incremental_cash_microusd,
            "provider_cash_microusd": self.provider_cash_microusd,
            "wall_seconds": self.wall_seconds,
            "tool_calls": self.tool_calls,
        }


@dataclass(frozen=True)
class ProviderCallLimit:
    provider: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    max_cash_microusd: int

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("provider and model must be non-empty")
        if min(self.max_input_tokens, self.max_output_tokens, self.max_cash_microusd) <= 0:
            raise ValueError("provider call limits must be positive")


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    phase: RunPhase | str
    dependencies: Sequence[str] = field(default_factory=tuple)
    packet: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0
    max_attempts: int = 1
    parent_task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id must be non-empty")
        object.__setattr__(self, "phase", RunPhase(self.phase))
        dependencies = tuple(dict.fromkeys(self.dependencies))
        if self.task_id in dependencies:
            raise ValueError("a task cannot depend on itself")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "packet", dict(self.packet))


@dataclass(frozen=True)
class TaskLease:
    task_id: str
    worker_id: str
    attempt: int
    lease_expires_at: datetime
    phase: RunPhase
    packet: Mapping[str, Any]
    dependencies: tuple[str, ...]
    reservation_id: str
    provider: str
    model: str


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    task_id: str
    provider: str
    model: str
    phase: RunPhase
    stage: str
    purpose: str
    status: ReservationStatus
    reserved: BudgetVector
    actual: BudgetVector
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BudgetSnapshot:
    ceiling: BudgetVector
    committed: BudgetVector
    reserved: BudgetVector
    remaining: BudgetVector
    closeout_reserve: BudgetVector
    closeout_required: bool
    closeout_reasons: tuple[str, ...]
    elapsed_wall_seconds: int
    deadline_remaining_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ceiling": self.ceiling.as_dict(),
            "committed": self.committed.as_dict(),
            "reserved": self.reserved.as_dict(),
            "remaining": self.remaining.as_dict(),
            "closeout_reserve": self.closeout_reserve.as_dict(),
            "closeout_required": self.closeout_required,
            "closeout_reasons": list(self.closeout_reasons),
            "elapsed_wall_seconds": self.elapsed_wall_seconds,
            "deadline_remaining_seconds": self.deadline_remaining_seconds,
        }


def usd_to_microusd(value: Any, *, field_name: str = "cash") -> int:
    """Convert a configuration/API cash value without passing through float."""

    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative decimal amount")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative decimal amount") from exc
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"{field_name} must be a non-negative decimal amount")
    micros = (decimal * MICRO_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(micros)


def microusd_to_usd(value: int) -> str:
    if value < 0:
        raise ValueError("micro-USD must be non-negative")
    return f"{Decimal(value) / Decimal(MICRO_USD):.6f}"
