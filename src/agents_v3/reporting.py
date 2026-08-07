"""Auditable STATUS and anytime-report rendering from persisted facts."""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
from typing import Any, Iterable

from .budget import BudgetManager
from .config import RunConfig
from .models import TaskStatus, microusd_to_usd
from .store import SQLiteStore, from_iso, utc_now


def _percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def build_status(
    store: SQLiteStore, config: RunConfig, *, now: datetime | None = None
) -> dict[str, Any]:
    with store.read_snapshot():
        return _build_status_from_snapshot(store, config, now=now)


def _build_status_from_snapshot(
    store: SQLiteStore, config: RunConfig, *, now: datetime | None = None
) -> dict[str, Any]:
    instant = now or utc_now()
    state = store.get_run_state()
    state_updated_at = from_iso(state["updated_at"])
    state_age_seconds = max(0, int((instant - state_updated_at).total_seconds()))
    counts = store.task_counts()
    events = store.list_events()
    snapshot = BudgetManager(store, config, bind_run=False).snapshot(now=instant)
    best = store.best_candidate()
    readiness_receipt_json = store.get_meta("final_readiness_receipt")
    readiness_receipt = (
        json.loads(readiness_receipt_json) if readiness_receipt_json else None
    )

    effective_types = {
        "TASK_SUCCEEDED",
        "BEST_CANDIDATE_PROMOTED",
        "FINAL_TEST_FROZEN",
        "FINAL_TEST_FINISHED",
    }
    last_progress = next(
        (event for event in reversed(events) if event["event_type"] in effective_types),
        None,
    )
    durations = [
        float(event["payload"].get("duration_seconds", 0))
        for event in events
        if event["event_type"] == "TASK_SUCCEEDED"
        and event["payload"].get("duration_seconds") is not None
    ]
    p50_task = _percentile(durations, 0.50)
    p95_task = _percentile(durations, 0.95)
    unfinished = sum(
        counts[status.value]
        for status in (
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.RETRY_WAIT,
            TaskStatus.BLOCKED,
        )
    )
    eta = None
    if p50_task is not None and p95_task is not None and unfinished:
        waves = math.ceil(unfinished / config.max_parallel_tasks)
        eta = {
            "low_seconds": int(p50_task * waves),
            "high_seconds": int(p95_task * waves),
            "basis": "persisted successful task durations; blocked work may invalidate ETA",
        }

    with store._lock:
        request_rows = store.connection.execute(
            "SELECT * FROM provider_requests ORDER BY finished_at"
        ).fetchall()
    latencies = [float(row["latency_ms"]) for row in request_rows if row["latency_ms"] is not None]
    error_counts: dict[str, int] = {}
    for row in request_rows:
        category = row["error_category"] or "success"
        error_counts[category] = error_counts.get(category, 0) + 1
    cache_hits = sum(int(row["cache_hit"]) for row in request_rows)

    elapsed_hours = max(snapshot.elapsed_wall_seconds / 3600.0, 1 / 3600.0)
    burn_rate = {
        "tokens_per_hour": round(snapshot.committed.tokens / elapsed_hours, 2),
        "incremental_cash_usd_per_hour": round(
            snapshot.committed.incremental_cash_microusd / 1_000_000 / elapsed_hours,
            6,
        ),
        "provider_cash_usd_per_hour": round(
            snapshot.committed.provider_cash_microusd / 1_000_000 / elapsed_hours,
            6,
        ),
        "tool_calls_per_hour": round(
            snapshot.committed.tool_calls / elapsed_hours, 2
        ),
    }

    if counts[TaskStatus.RUNNING.value]:
        bottleneck = "running task capacity"
    elif counts[TaskStatus.BLOCKED.value]:
        bottleneck = "blocked dependencies"
    elif counts[TaskStatus.RETRY_WAIT.value]:
        bottleneck = "retry backoff"
    elif counts[TaskStatus.READY.value]:
        bottleneck = "worker availability"
    elif unfinished:
        bottleneck = "unresolved dependency graph"
    else:
        bottleneck = "none"

    return {
        "generated_at": instant.isoformat(),
        "run": {
            "run_id": config.run_id,
            "schema_version": config.schema_version,
            "phase": state["phase"],
            "terminal_status": state["terminal_status"],
            "final_test_status": state["final_test_status"],
            "closeout_mode": bool(state["closeout_mode"]),
            "status_reason": state["status_reason"],
            "frozen_candidate_hash": state["frozen_candidate_hash"],
            "evaluation_profile_hash": state["final_profile_hash"],
            "final_readiness_receipt_sha256": (
                readiness_receipt.get("receipt_sha256") if readiness_receipt else None
            ),
            "state_updated_at": state["updated_at"],
            "state_age_seconds": state_age_seconds,
            "status_stale": state_age_seconds > config.max_status_staleness_seconds,
        },
        "tasks": {
            "known": sum(counts.values()),
            "counts": counts,
            "bottleneck": bottleneck,
            "last_effective_progress": (
                {
                    "seq": last_progress["seq"],
                    "occurred_at": last_progress["occurred_at"],
                    "event_type": last_progress["event_type"],
                    "entity_id": last_progress["entity_id"],
                }
                if last_progress
                else None
            ),
            "duration_p50_seconds": p50_task,
            "duration_p95_seconds": p95_task,
            "eta": eta,
        },
        "candidate": (
            {
                "hash": best["candidate_hash"],
                "artifact_path": best["artifact_path"],
                "dev_score": best["dev_score"],
                "metrics": best["metrics"],
                "immutable": bool(best["immutable"]),
            }
            if best
            else None
        ),
        "budget": snapshot.as_dict(),
        "burn_rate": burn_rate,
        "provider": {
            "requests": len(request_rows),
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "cache_hits": cache_hits,
            "error_categories": error_counts,
        },
        "ledger": {
            "last_sequence": events[-1]["seq"] if events else 0,
            "checksum_chain_valid": store.verify_event_chain(),
        },
        "development": store.dev_iteration_state(),
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _status_markdown(status: dict[str, Any]) -> str:
    run = status["run"]
    tasks = status["tasks"]
    budget = status["budget"]
    candidate = status["candidate"]
    provider = status["provider"]
    lines = [
        "# AGENTS V3 Status",
        "",
        f"Generated: `{status['generated_at']}`",
        "",
        "## Run",
        "",
        f"- Run: `{run['run_id']}`",
        f"- Phase: `{run['phase']}`",
        f"- Terminal: `{run['terminal_status'] or 'ACTIVE'}`",
        f"- Final test: `{run['final_test_status']}`",
        f"- Closeout: `{'ON' if run['closeout_mode'] else 'OFF'}`",
        f"- Reason: {run['status_reason'] or '—'}",
        f"- Persisted state age: `{run['state_age_seconds']}s`; stale: `{run['status_stale']}`",
        "",
        "## Verifiable progress",
        "",
        f"- Known tasks: `{tasks['known']}`",
        f"- Counts: `{json.dumps(tasks['counts'], sort_keys=True)}`",
        f"- Bottleneck: {tasks['bottleneck']}",
        f"- Last effective progress: `{json.dumps(tasks['last_effective_progress'], sort_keys=True)}`",
        f"- ETA: `{json.dumps(tasks['eta'], sort_keys=True)}`",
        "",
        "## Best-so-far",
        "",
        (
            f"- Candidate: `{candidate['hash']}` at `{candidate['artifact_path']}`\n"
            f"- Dev score: `{candidate['dev_score']}`; immutable: `{candidate['immutable']}`"
            if candidate
            else "- No candidate has passed the artifact gate yet."
        ),
        "",
        "## Budget",
        "",
        f"- Committed: `{json.dumps(budget['committed'], sort_keys=True)}`",
        f"- In flight: `{json.dumps(budget['reserved'], sort_keys=True)}`",
        f"- Remaining: `{json.dumps(budget['remaining'], sort_keys=True)}`",
        f"- Closeout reserve: `{json.dumps(budget['closeout_reserve'], sort_keys=True)}`",
        f"- Soft-waterline reasons: `{json.dumps(budget['closeout_reasons'])}`",
        "",
        "## Provider telemetry",
        "",
        f"- Requests: `{provider['requests']}`; cache hits: `{provider['cache_hits']}`",
        f"- Latency P50/P95 ms: `{provider['latency_p50_ms']}` / `{provider['latency_p95_ms']}`",
        f"- Error categories: `{json.dumps(provider['error_categories'], sort_keys=True)}`",
        "",
        "## Integrity",
        "",
        f"- Last event sequence: `{status['ledger']['last_sequence']}`",
        f"- Event checksum chain: `{'VALID' if status['ledger']['checksum_chain_valid'] else 'INVALID'}`",
        "",
    ]
    return "\n".join(lines)


def render_status(
    store: SQLiteStore,
    config: RunConfig,
    path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    status = build_status(store, config, now=now)
    _atomic_write(Path(path), _status_markdown(status))
    return status


def render_anytime_report(
    store: SQLiteStore,
    config: RunConfig,
    path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Render a truthful deliverable even when test or budget is incomplete."""

    status = build_status(store, config, now=now)
    run = status["run"]
    candidate = status["candidate"]
    verdict = run["terminal_status"] or "ACTIVE_INTERMEDIATE"
    tested = run["final_test_status"] in {
        "SUCCESS",
        "PASSED",
        "VALID_TEST_FAILED",
        "INCONCLUSIVE",
    }
    text = "\n".join(
        [
            "# AGENTS V3 Anytime Report",
            "",
            f"Status: `{verdict}`",
            f"Final test executed to a valid conclusion: `{'yes' if tested else 'no'}`",
            "",
            "## Best verified intermediate",
            "",
            (
                f"Candidate `{candidate['hash']}` at `{candidate['artifact_path']}` "
                f"with dev score `{candidate['dev_score']}`."
                if candidate
                else "No candidate has passed the artifact gate."
            ),
            "",
            "## Machine-readable status",
            "",
            "```json",
            json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    _atomic_write(Path(path), text)
    return status
