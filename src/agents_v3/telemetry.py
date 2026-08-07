"""Structured, append-only telemetry for provider and worker diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping


ERROR_CATEGORIES = frozenset(
    {
        "transport",
        "rate_limit",
        "http_4xx",
        "http_5xx",
        "timeout",
        "empty_response",
        "truncated",
        "context_overflow",
        "schema_invalid",
        "safety_refusal",
        "semantic_invalid",
        "authentication",
        "client_bug",
        "provider_disabled",
        "circuit_open",
        "unknown",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    request_id: str
    task_id: str
    provider: str
    model: str
    profile_hash: str
    started_at: str
    latency_ms: int
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    reserved_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    provider_request_id: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    retry_number: int = 0
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if self.status not in {"SUCCESS", "FAILED"}:
            raise ValueError("status must be SUCCESS or FAILED")
        if self.status == "FAILED" and not self.error_category:
            raise ValueError("failed records require error_category")
        if self.error_category and self.error_category not in ERROR_CATEGORIES:
            raise ValueError(f"unknown error category: {self.error_category}")
        for name in ("latency_ms", "input_tokens", "output_tokens", "retry_number"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        for name in ("reserved_cost_usd", "actual_cost_usd"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")


class JsonlTelemetry:
    """Single-process writer with durable flush for human-auditable call data."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: CallRecord) -> None:
        record.validate()
        payload = asdict(record)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"corrupt telemetry at line {number}") from exc
        return rows

    def summary(self) -> dict[str, Any]:
        rows = self.read_all()
        failures: dict[str, int] = {}
        for row in rows:
            category = row.get("error_category")
            if category:
                failures[category] = failures.get(category, 0) + 1
        latencies = sorted(int(row["latency_ms"]) for row in rows)

        def percentile(q: float) -> int | None:
            if not latencies:
                return None
            index = round((len(latencies) - 1) * q)
            return latencies[index]

        return {
            "calls": len(rows),
            "successes": sum(row.get("status") == "SUCCESS" for row in rows),
            "failures": sum(row.get("status") == "FAILED" for row in rows),
            "failure_categories": failures,
            "input_tokens": sum(int(row.get("input_tokens", 0)) for row in rows),
            "output_tokens": sum(int(row.get("output_tokens", 0)) for row in rows),
            "actual_cost_usd": round(
                sum(float(row.get("actual_cost_usd", 0.0)) for row in rows), 8
            ),
            "latency_p50_ms": percentile(0.50),
            "latency_p95_ms": percentile(0.95),
        }
