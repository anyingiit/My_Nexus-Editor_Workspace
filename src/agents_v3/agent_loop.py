"""Deterministic planning and scoring for the contribution-quality construct.

Live coding agents and blind reviewers remain external, least-privilege
processes. This module creates the equal-model/equal-budget paired assignment
manifest and refuses incomplete, unblinded, over-budget, or profile-mismatched
result bundles.
"""

from __future__ import annotations

from hashlib import sha256
import json
from random import Random
import re
from typing import Any, Mapping, Sequence

from .schema_gate import SchemaValidationError, assert_published_schema


ARMS = ("empty", "repository", "candidate")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AgentLoopProtocolError(ValueError):
    pass


def _canonical_hash(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _budget(value: Mapping[str, Any]) -> dict[str, int]:
    required = ("tokens", "wall_seconds", "cash_microusd")
    result: dict[str, int] = {}
    for field in required:
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise AgentLoopProtocolError(f"common_budget.{field} must be a non-negative integer")
        if field != "cash_microusd" and item == 0:
            raise AgentLoopProtocolError(f"common_budget.{field} must be positive")
        result[field] = item
    if set(value) != set(required):
        raise AgentLoopProtocolError("common_budget contains unknown or missing fields")
    return result


def create_agent_loop_plan(
    *,
    experiment_id: str,
    issues: Sequence[Mapping[str, Any]],
    coding_model: Mapping[str, Any],
    common_budget: Mapping[str, Any],
    reviewer_profile_hash: str,
    seed: int,
) -> dict[str, Any]:
    if not experiment_id:
        raise AgentLoopProtocolError("experiment_id is required")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise AgentLoopProtocolError("seed must be an integer")
    if not isinstance(coding_model, Mapping) or not coding_model:
        raise AgentLoopProtocolError("one frozen coding_model is required")
    if not isinstance(reviewer_profile_hash, str) or _HASH_RE.fullmatch(reviewer_profile_hash) is None:
        raise AgentLoopProtocolError("reviewer_profile_hash must be SHA-256")
    frozen_budget = _budget(common_budget)
    model_hash = _canonical_hash(dict(coding_model))
    issue_map: dict[str, Mapping[str, Any]] = {}
    for index, issue in enumerate(issues):
        if not isinstance(issue, Mapping):
            raise AgentLoopProtocolError(f"issues[{index}] must be an object")
        issue_id = issue.get("issue_id")
        prompt_hash = issue.get("issue_prompt_hash")
        base_commit = issue.get("base_commit")
        if not isinstance(issue_id, str) or not issue_id or issue_id in issue_map:
            raise AgentLoopProtocolError(f"issues[{index}] needs a unique issue_id")
        if not isinstance(prompt_hash, str) or _HASH_RE.fullmatch(prompt_hash) is None:
            raise AgentLoopProtocolError(f"issues[{index}].issue_prompt_hash must be SHA-256")
        if not isinstance(base_commit, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_commit) is None:
            raise AgentLoopProtocolError(f"issues[{index}].base_commit must be a full commit")
        issue_map[issue_id] = issue
    if not issue_map:
        raise AgentLoopProtocolError("at least one issue is required")

    assignments: list[dict[str, Any]] = []
    for issue_id in sorted(issue_map):
        issue = issue_map[issue_id]
        local_seed = int(_canonical_hash([experiment_id, issue_id, seed])[:16], 16)
        ordered_arms = list(ARMS)
        Random(local_seed).shuffle(ordered_arms)
        for order, arm in enumerate(ordered_arms):
            blind_id = sha256(
                f"{experiment_id}\x00{issue_id}\x00{arm}\x00{seed}".encode("utf-8")
            ).hexdigest()[:24]
            assignments.append(
                {
                    "blind_task_id": blind_id,
                    "issue_id": issue_id,
                    "issue_prompt_hash": issue["issue_prompt_hash"],
                    "base_commit": issue["base_commit"],
                    "arm": arm,
                    "order": order,
                    "coding_model_hash": model_hash,
                    "budget": dict(frozen_budget),
                }
            )
    plan = {
        "schema_version": "3.0.0",
        "experiment_id": experiment_id,
        "seed": seed,
        "coding_model": dict(coding_model),
        "coding_model_hash": model_hash,
        "common_budget": dict(frozen_budget),
        "arms": list(ARMS),
        "assignments": assignments,
        "reviewer_profile_hash": reviewer_profile_hash,
        "blinded": True,
    }
    try:
        return assert_published_schema(plan, "agent_in_loop_plan.schema.json")
    except SchemaValidationError as exc:
        raise AgentLoopProtocolError(f"generated plan violates published schema: {exc}") from exc


def validate_agent_loop_plan(plan: Mapping[str, Any]) -> None:
    try:
        plan = assert_published_schema(plan, "agent_in_loop_plan.schema.json")
    except SchemaValidationError as exc:
        raise AgentLoopProtocolError(f"plan violates published schema: {exc}") from exc
    if plan.get("schema_version") != "3.0.0" or plan.get("arms") != list(ARMS) or plan.get("blinded") is not True:
        raise AgentLoopProtocolError("plan version, arms, and blinded flag are immutable")
    if plan.get("coding_model_hash") != _canonical_hash(plan.get("coding_model")):
        raise AgentLoopProtocolError("coding model hash mismatch")
    common = _budget(plan.get("common_budget", {}))
    assignments = plan.get("assignments")
    if not isinstance(assignments, Sequence) or isinstance(assignments, (str, bytes)):
        raise AgentLoopProtocolError("assignments must be an array")
    by_issue: dict[str, list[Mapping[str, Any]]] = {}
    blind_ids: set[str] = set()
    for item in assignments:
        if not isinstance(item, Mapping):
            raise AgentLoopProtocolError("assignment must be an object")
        blind_id = item.get("blind_task_id")
        if not isinstance(blind_id, str) or re.fullmatch(r"[0-9a-f]{24}", blind_id) is None or blind_id in blind_ids:
            raise AgentLoopProtocolError("blind task IDs must be unique 24-hex values")
        blind_ids.add(blind_id)
        if item.get("coding_model_hash") != plan.get("coding_model_hash") or item.get("budget") != common:
            raise AgentLoopProtocolError("every assignment must use the same model and budget")
        by_issue.setdefault(str(item.get("issue_id")), []).append(item)
    for issue_id, items in by_issue.items():
        if {str(item.get("arm")) for item in items} != set(ARMS) or {int(item.get("order", -1)) for item in items} != {0, 1, 2}:
            raise AgentLoopProtocolError(f"issue {issue_id!r} does not have one randomized assignment per arm")
        if len({item.get("base_commit") for item in items}) != 1 or len({item.get("issue_prompt_hash") for item in items}) != 1:
            raise AgentLoopProtocolError(f"issue {issue_id!r} differs across arms")


def score_agent_loop_results(
    plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    validate_agent_loop_plan(plan)
    expected = {item["blind_task_id"]: item for item in plan["assignments"]}
    observed: dict[str, Mapping[str, Any]] = {}
    required = {
        "blind_task_id",
        "coding_model_hash",
        "patch_sha256",
        "completion_status",
        "ci_passed",
        "blind_problem_count",
        "blind_severity_score",
        "blind_completion_score",
        "tokens_used",
        "wall_seconds",
        "cash_microusd",
    }
    for index, result in enumerate(results):
        try:
            result = assert_published_schema(result, "agent_in_loop_result.schema.json")
        except SchemaValidationError as exc:
            raise AgentLoopProtocolError(
                f"results[{index}] violates the published result schema: {exc}"
            ) from exc
        if not isinstance(result, Mapping) or set(result) != required:
            raise AgentLoopProtocolError(f"results[{index}] does not match the frozen result schema")
        blind_id = result.get("blind_task_id")
        if blind_id not in expected or blind_id in observed:
            raise AgentLoopProtocolError(f"results[{index}] has an unknown or duplicate blind_task_id")
        assignment = expected[str(blind_id)]
        if result.get("coding_model_hash") != assignment["coding_model_hash"]:
            raise AgentLoopProtocolError("result coding model changed")
        if not isinstance(result.get("patch_sha256"), str) or _HASH_RE.fullmatch(str(result.get("patch_sha256"))) is None:
            raise AgentLoopProtocolError("result patch_sha256 is invalid")
        if result.get("completion_status") not in {"COMPLETE", "PARTIAL", "FAILED"} or not isinstance(result.get("ci_passed"), bool):
            raise AgentLoopProtocolError("result completion/CI status is invalid")
        for field in ("blind_problem_count", "tokens_used", "wall_seconds", "cash_microusd"):
            value = result.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AgentLoopProtocolError(f"result {field} must be a non-negative integer")
        for field in ("blind_severity_score", "blind_completion_score"):
            value = result.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise AgentLoopProtocolError(f"result {field} must be non-negative")
        if float(result["blind_completion_score"]) > 1.0:
            raise AgentLoopProtocolError("blind_completion_score must be at most 1")
        budget = assignment["budget"]
        if result["tokens_used"] > budget["tokens"] or result["wall_seconds"] > budget["wall_seconds"] or result["cash_microusd"] > budget["cash_microusd"]:
            raise AgentLoopProtocolError("result exceeded the equal per-arm budget")
        observed[str(blind_id)] = result
    missing = sorted(set(expected) - set(observed))
    if missing:
        raise AgentLoopProtocolError(f"missing paired results: {missing}")

    rows: dict[str, dict[str, Mapping[str, Any]]] = {}
    for blind_id, result in observed.items():
        assignment = expected[blind_id]
        rows.setdefault(assignment["issue_id"], {})[assignment["arm"]] = result
    aggregates: dict[str, dict[str, float]] = {}
    for arm in ARMS:
        values = [items[arm] for items in rows.values()]
        aggregates[arm] = {
            "n": float(len(values)),
            "ci_pass_rate": sum(bool(item["ci_passed"]) for item in values) / len(values),
            "completion_rate": sum(item["completion_status"] == "COMPLETE" for item in values) / len(values),
            "mean_blind_problem_count": sum(int(item["blind_problem_count"]) for item in values) / len(values),
            "mean_blind_severity_score": sum(float(item["blind_severity_score"]) for item in values) / len(values),
            "mean_blind_completion_score": sum(float(item["blind_completion_score"]) for item in values) / len(values),
            "mean_tokens": sum(int(item["tokens_used"]) for item in values) / len(values),
            "mean_wall_seconds": sum(int(item["wall_seconds"]) for item in values) / len(values),
            "mean_cash_microusd": sum(int(item["cash_microusd"]) for item in values) / len(values),
        }
    paired: dict[str, dict[str, float]] = {}
    for baseline in ("empty", "repository"):
        paired[baseline] = {
            "completion_score_delta": sum(
                float(items["candidate"]["blind_completion_score"])
                - float(items[baseline]["blind_completion_score"])
                for items in rows.values()
            )
            / len(rows),
            "problem_count_delta": sum(
                int(items["candidate"]["blind_problem_count"])
                - int(items[baseline]["blind_problem_count"])
                for items in rows.values()
            )
            / len(rows),
        }
    return {
        "construct": "agent_in_the_loop_contribution_quality",
        "experiment_id": plan["experiment_id"],
        "issue_count": len(rows),
        "coding_model_hash": plan["coding_model_hash"],
        "reviewer_profile_hash": plan["reviewer_profile_hash"],
        "arm_metrics": aggregates,
        "candidate_paired_differences": paired,
        "inference": "DESCRIPTIVE_PAIRED" if len(rows) < 20 else "READY_FOR_PREREGISTERED_PAIRED_INFERENCE",
    }
