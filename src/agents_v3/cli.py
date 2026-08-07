"""Command-line entry points for validation, offline demo, status, and power."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence

import yaml

from .agent_loop import create_agent_loop_plan, score_agent_loop_results
from .budget import BudgetManager
from .config import approval_payload_sha256, load_config
from .dataset import validate_eligibility_manifest
from .evaluation import evaluate_three_arm
from .knowledge import knowledge_manifest_hash, validate_knowledge_manifest
from .models import BudgetVector, RunPhase, TaskSpec, TerminalStatus
from .power import plan_three_arm_sample_size
from .profiles import load_profile
from .readiness import validate_final_readiness
from .reporting import build_status, render_anytime_report, render_status
from .resources import asset_root
from .runtime import validate_runtime_capability_report
from .scheduler import Scheduler
from .schema_gate import assert_schema
from .security import validate_capability_manifest
from .store import SQLiteStore


class CLIError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path: Path, value: Any) -> None:
    _atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError(f"cannot read JSON {path}: {exc}") from exc


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CLIError(f"cannot read YAML {path}: {exc}") from exc


def _schema(root: Path, name: str) -> Mapping[str, Any]:
    value = _read_json(root / "schemas" / name)
    if not isinstance(value, Mapping):
        raise CLIError(f"schema {name} is not an object")
    return value


def _project_validation(
    root: Path,
    *,
    launcher_attestation_key: bytes | None = None,
    artifact_resolution_manifest: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, action) -> Any:
        try:
            detail = action()
        except Exception as exc:  # each gate is reported independently
            checks.append({"name": name, "ok": False, "detail": str(exc)})
            return None
        checks.append({"name": name, "ok": True, "detail": detail or "passed"})
        return detail

    schema_files = sorted((root / "schemas").glob("*.schema.json"))

    def schemas_parse() -> str:
        if not schema_files:
            raise CLIError("no published schemas found")
        identifiers: set[str] = set()
        for path in schema_files:
            value = _read_json(path)
            if not isinstance(value, Mapping) or value.get("$schema") is None:
                raise CLIError(f"{path.name} is not a JSON Schema object")
            identifier = str(value.get("$id", path.name))
            if identifier in identifiers:
                raise CLIError(f"duplicate schema identifier: {identifier}")
            identifiers.add(identifier)
        return f"{len(schema_files)} schemas parsed with unique IDs"

    check("published_schemas", schemas_parse)
    config = None

    def load_run_manifest() -> str:
        nonlocal config
        config = load_config(root / "config" / "run_config.example.yaml")
        return config.fingerprint()

    check("run_config", load_run_manifest)
    profile = None

    def load_evaluation_profile() -> dict[str, Any]:
        nonlocal profile
        profile = load_profile(root / "config" / "evaluation_profile.example.yaml")
        return {"profile_id": profile["profile_id"], "frozen": profile["frozen"]}

    check("evaluation_profile", load_evaluation_profile)

    runtime_report = _read_yaml(root / "config" / "runtime_capability_report.example.yaml")
    check(
        "runtime_report_schema",
        lambda: assert_schema(runtime_report, _schema(root, "runtime_capability_report.schema.json")),
    )
    runtime_validation = validate_runtime_capability_report(runtime_report)
    checks.append(
        {
            "name": "runtime_negative_probes",
            "ok": True,
            "detail": {
                "final_test_ready": runtime_validation.ok,
                "expected_template_is_unpassed": not runtime_validation.ok,
                "findings": len(runtime_validation.errors),
            },
        }
    )

    capability = _read_json(root / "sandbox" / "capability_manifest.example.json")
    check(
        "capability_manifest_schema",
        lambda: assert_schema(capability, _schema(root, "capability_manifest.schema.json")),
    )
    security_validation = validate_capability_manifest(capability)
    check(
        "capability_manifest_semantics",
        lambda: security_validation.raise_for_errors() or "default-deny role policy accepted",
    )

    resolved_artifacts: dict[str, bytes] | None = None

    def load_artifact_resolution() -> str:
        nonlocal resolved_artifacts
        assert artifact_resolution_manifest is not None
        raw_manifest = artifact_resolution_manifest.expanduser()
        if raw_manifest.is_symlink():
            raise CLIError("artifact resolution manifest must not be a symlink")
        manifest_path = raw_manifest.resolve()
        if not manifest_path.is_file():
            raise CLIError("artifact resolution manifest must be an existing file")
        value = _read_json(manifest_path)
        assert_schema(value, _schema(root, "artifact_resolution.schema.json"))
        canonical = value
        if config is None or canonical["run_id"] != config.run_id:
            raise CLIError("artifact resolution run_id does not match run config")
        base = manifest_path.parent.resolve()
        loaded: dict[str, bytes] = {}
        for item in canonical["artifacts"]:
            profile_path = item["profile_path"]
            if profile_path in loaded:
                raise CLIError(f"duplicate artifact resolution path: {profile_path}")
            relative = Path(item["relative_path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise CLIError(f"unsafe artifact relative path: {relative}")
            candidate = base / relative
            cursor = base
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise CLIError(f"artifact resolution may not traverse symlinks: {relative}")
            resolved = candidate.resolve()
            try:
                resolved.relative_to(base)
            except ValueError as exc:
                raise CLIError(f"artifact path escapes manifest directory: {relative}") from exc
            if not resolved.is_file():
                raise CLIError(f"resolved artifact is not a file: {relative}")
            loaded[profile_path] = resolved.read_bytes()
        resolved_artifacts = loaded
        return f"{len(loaded)} artifact byte streams resolved"

    if artifact_resolution_manifest is not None:
        check("artifact_resolution", load_artifact_resolution)

    def constitution_check() -> str:
        text = (root / "AGENTS.md").read_text(encoding="utf-8")
        if text.lstrip().startswith("```") or text.lstrip().startswith("````"):
            raise CLIError("AGENTS.md is wrapped in an outer code fence")
        if "1 master + 1 subagent" in text.lower():
            raise CLIError("legacy V1 title remains")
        if len(text.splitlines()) > 260:
            raise CLIError("constitution is no longer short; move mechanisms into code/protocol")
        return f"{len(text.splitlines())} lines, no legacy wrapper/title"

    check("constitution_shape", constitution_check)
    dependency_lock_bytes: bytes | None = None

    def load_dependency_lock() -> str:
        nonlocal dependency_lock_bytes
        dependency_lock_bytes = (root / "requirements.lock").read_bytes()
        if not dependency_lock_bytes:
            raise CLIError("requirements.lock is empty")
        return sha256(dependency_lock_bytes).hexdigest()

    check("dependency_lock", load_dependency_lock)
    structural_ok = all(item["ok"] for item in checks)
    if (
        isinstance(profile, Mapping)
        and config is not None
        and isinstance(runtime_report, Mapping)
        and isinstance(capability, Mapping)
        and isinstance(dependency_lock_bytes, bytes)
    ):
        readiness = validate_final_readiness(
            profile=profile,
            config=config,
            runtime_report=runtime_report,
            capability_manifest=capability,
            dependency_lock_bytes=dependency_lock_bytes,
            launcher_attestation_key=launcher_attestation_key,
            resolved_artifacts=resolved_artifacts,
        )
    else:
        readiness = None
    final_ready = bool(structural_ok and readiness is not None and readiness.ok)
    manifest = {
        "schema_version": "3.0.0",
        "asset_root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "structural_ok": structural_ok,
        "final_test_ready": final_ready,
        "checks": checks,
        "final_readiness": readiness.to_dict() if readiness is not None else None,
        "readiness_blockers": (
            [
                f"{item.path}: {item.code}: {item.message}"
                for item in readiness.errors
            ]
            if readiness is not None
            else ["one or more structural inputs could not be loaded"]
        ),
    }
    manifest["validation_sha256"] = sha256(_canonical_bytes(manifest)).hexdigest()
    return manifest


def _demo_dataset_manifest() -> dict[str, Any]:
    sha_a, sha_b = "a" * 40, "b" * 40
    hash_a, hash_b = "a" * 64, "b" * 64
    return {
        "schema_version": "3.0.0",
        "dataset_id": "offline-demo-census-v3",
        "counts": {
            "discovered": 3,
            "eligible": 1,
            "rationale": 1,
            "administrative": 1,
            "excluded": 1,
            "unknown": 0,
        },
        "samples": [
            {
                "schema_version": "3.0.0",
                "sample_id": "opaque-review-1",
                "repository_id": "offline-opaque",
                "pull_request_group_id": "opaque-pr-group-1",
                "eligibility_cohort": "decision",
                "rationale_eligible": True,
                "observed_review_issue_count": 2,
                "label_event_id": "qualified-review-1",
                "label_event_type": "REQUEST_CHANGES",
                "label_timestamp": "2026-01-20T12:00:00Z",
                "head_sha_at_event": sha_a,
                "base_sha_at_event": sha_b,
                "snapshot_head_sha": sha_a,
                "snapshot_base_sha": sha_b,
                "snapshot_manifest_sha256": hash_a,
                "maintainer_identity_basis": "frozen_allowlist",
                "maintainer_evidence_as_of": "2026-01-20T11:59:00Z",
                "eligibility_reason": "QUALIFIED_MAINTAINER_REVIEW",
                "exclusion_reason": None,
                "split_group_id": "demo-patch-family-1",
                "grouping_evidence_sha256": hash_b,
                "split": "test",
                "ground_truth_manifest_sha256": "c" * 64,
                "aggregate_ci_status": "CI_PASS",
            },
            {
                "schema_version": "3.0.0",
                "sample_id": "opaque-admin-2",
                "repository_id": "offline-opaque",
                "pull_request_group_id": "opaque-pr-group-2",
                "eligibility_cohort": "administrative",
                "rationale_eligible": False,
                "observed_review_issue_count": None,
                "label_event_id": "close-2",
                "label_event_type": "DUPLICATE",
                "label_timestamp": "2026-01-21T12:00:00Z",
                "head_sha_at_event": sha_a,
                "base_sha_at_event": sha_b,
                "snapshot_head_sha": None,
                "snapshot_base_sha": None,
                "snapshot_manifest_sha256": None,
                "maintainer_identity_basis": None,
                "maintainer_evidence_as_of": None,
                "eligibility_reason": "ADMINISTRATIVE_DUPLICATE",
                "exclusion_reason": None,
                "split_group_id": "demo-patch-family-2",
                "grouping_evidence_sha256": hash_b,
                "split": "unassigned",
                "ground_truth_manifest_sha256": None,
                "aggregate_ci_status": "CI_UNKNOWN",
            },
            {
                "schema_version": "3.0.0",
                "sample_id": "opaque-excluded-3",
                "repository_id": "offline-opaque",
                "pull_request_group_id": "opaque-pr-group-3",
                "eligibility_cohort": "excluded",
                "rationale_eligible": False,
                "observed_review_issue_count": None,
                "label_event_id": "close-3",
                "label_event_type": "AUTHOR_CLOSED",
                "label_timestamp": "2026-01-22T12:00:00Z",
                "head_sha_at_event": sha_a,
                "base_sha_at_event": sha_b,
                "snapshot_head_sha": None,
                "snapshot_base_sha": None,
                "snapshot_manifest_sha256": None,
                "maintainer_identity_basis": None,
                "maintainer_evidence_as_of": None,
                "eligibility_reason": None,
                "exclusion_reason": "AUTHOR_CLOSED_WITHOUT_MAINTAINER_DECISION",
                "split_group_id": "demo-patch-family-3",
                "grouping_evidence_sha256": hash_b,
                "split": "unassigned",
                "ground_truth_manifest_sha256": None,
                "aggregate_ci_status": "CI_UNKNOWN",
            },
        ],
    }


def _demo_knowledge_manifest(root: Path) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "3.0.0",
        "manifest_id": "offline-demo-knowledge-v3",
        "knowledge_cutoff": "2026-01-31T00:00:00Z",
        "manifest_sha256": None,
        "sources": [
            {
                "source_id": "demo-constitution",
                "kind": "repository_file",
                "origin": "offline-demo/AGENTS.md",
                "revision": "offline-demo-v3",
                "commit_sha": "d" * 40,
                "sha256": sha256((root / "AGENTS.md").read_bytes()).hexdigest(),
                "published_at": "2026-01-01T00:00:00Z",
                "available_at": "2026-01-01T00:00:00Z",
                "candidate_visible": True,
            }
        ],
        "sample_groups": [
            {
                "group_id": "demo-patch-family-1",
                "sample_ids": ["opaque-review-1"],
                "split": "test",
                "max_label_event_at": "2026-01-20T12:00:00Z",
                "basis": ["patch_similarity"],
            }
        ],
        "candidate_source_ids": ["demo-constitution"],
        "baseline_source_ids": ["demo-constitution"],
        "memory_contamination_risk": "UNKNOWN",
        "memory_probe_report_sha256": None,
    }
    value["manifest_sha256"] = knowledge_manifest_hash(value)
    return value


def _run_demo_task(
    scheduler: Scheduler,
    budget: BudgetManager,
    task: TaskSpec,
    *,
    worst_case: BudgetVector,
    actual: BudgetVector,
    result: Mapping[str, Any],
) -> None:
    scheduler.add_tasks([task])
    worker = f"offline-{task.task_id}"
    lease = scheduler.admit_and_claim(
        task_id=task.task_id,
        worker_id=worker,
        provider="deterministic_stub",
        model="fixture-worker-v1",
        worst_case=worst_case,
        input_tokens=max(0, worst_case.tokens - 50),
        output_tokens=min(50, worst_case.tokens),
        idempotency_key=f"demo:{task.task_id}:1",
        purpose="exploration",
    )
    budget.commit(lease.reservation_id, actual)
    scheduler.complete(task.task_id, worker, result)


def _demo(root: Path, output: Path, config_path: Path) -> dict[str, Any]:
    if output.exists():
        raise CLIError(f"refusing to overwrite existing demo directory: {output}")
    output.mkdir(parents=True)
    validation = _project_validation(root)
    _write_json(output / "VALIDATION.json", validation)
    if not validation["structural_ok"]:
        raise CLIError("project validation failed; see VALIDATION.json")
    config = load_config(config_path)
    if config.mode != "demo":
        raise CLIError("demo requires a mode=demo run manifest")

    state_path = output / "state.sqlite3"
    store = SQLiteStore(state_path)
    try:
        budget = BudgetManager(store, config)
        scheduler = Scheduler(
            store,
            config=config,
            budget_manager=budget,
            lease_seconds=config.lease_seconds,
            max_parallel_tasks=config.max_parallel_tasks,
        )

        store.transition_run_phase(RunPhase.PROTOCOL, reason="offline protocol gate")
        _run_demo_task(
            scheduler,
            budget,
            TaskSpec("protocol-gate", RunPhase.PROTOCOL, packet={"network": "none"}),
            worst_case=BudgetVector(tokens=300, wall_seconds=2, tool_calls=1),
            actual=BudgetVector(tokens=180, wall_seconds=1, tool_calls=1),
            result={"structural_ok": True, "final_test_ready": False},
        )

        dataset_manifest = _demo_dataset_manifest()
        dataset_validation = validate_eligibility_manifest(dataset_manifest)
        dataset_validation.raise_for_errors()
        _write_json(output / "manifests" / "eligibility_manifest.json", dataset_manifest)

        knowledge_manifest = _demo_knowledge_manifest(root)
        assert_schema(knowledge_manifest, _schema(root, "knowledge_manifest.schema.json"))
        knowledge_validation = validate_knowledge_manifest(knowledge_manifest)
        knowledge_validation.raise_for_errors()
        _write_json(output / "manifests" / "knowledge_manifest.json", knowledge_manifest)

        store.transition_run_phase(RunPhase.CENSUS, reason="event-aligned synthetic census")
        _run_demo_task(
            scheduler,
            budget,
            TaskSpec("cheap-census", RunPhase.CENSUS, packet={"metadata_only": True}),
            worst_case=BudgetVector(wall_seconds=2, tool_calls=1),
            actual=BudgetVector(wall_seconds=1, tool_calls=1),
            result=dataset_validation.summary,
        )

        plan = create_agent_loop_plan(
            experiment_id="offline-paired-v3",
            issues=[
                {"issue_id": "opaque-i1", "issue_prompt_hash": "1" * 64, "base_commit": "1" * 40},
                {"issue_id": "opaque-i2", "issue_prompt_hash": "2" * 64, "base_commit": "2" * 40},
            ],
            coding_model={"provider": "deterministic_stub", "model": "fixture-coder-v1", "temperature": 0},
            common_budget={"tokens": 1000, "wall_seconds": 60, "cash_microusd": 0},
            reviewer_profile_hash="e" * 64,
            seed=20260807,
        )
        assert_schema(plan, _schema(root, "agent_in_loop_plan.schema.json"))
        loop_results: list[dict[str, Any]] = []
        for assignment in plan["assignments"]:
            candidate = assignment["arm"] == "candidate"
            row = {
                "blind_task_id": assignment["blind_task_id"],
                "coding_model_hash": plan["coding_model_hash"],
                "patch_sha256": sha256(assignment["blind_task_id"].encode()).hexdigest(),
                "completion_status": "COMPLETE",
                "ci_passed": candidate,
                "blind_problem_count": 0 if candidate else 2,
                "blind_severity_score": 0.0 if candidate else 2.0,
                "blind_completion_score": 1.0 if candidate else 0.5,
                "tokens_used": 500,
                "wall_seconds": 20,
                "cash_microusd": 0,
            }
            assert_schema(row, _schema(root, "agent_in_loop_result.schema.json"))
            loop_results.append(row)
        loop_report = score_agent_loop_results(plan, loop_results)
        _write_json(output / "vertical_slice" / "agent_loop_plan.json", plan)
        _write_json(output / "vertical_slice" / "agent_loop_results.json", loop_results)
        _write_json(output / "vertical_slice" / "agent_loop_report.json", loop_report)

        truth = {
            f"s{index}": "APPROVE" if index % 2 == 0 else "REQUEST_CHANGES"
            for index in range(4)
        }
        candidate_rows = [
            {"sample_id": sample, "prediction": label, "reason_codes": []}
            for sample, label in truth.items()
        ]
        inverted_rows = [
            {
                "sample_id": sample,
                "prediction": "REQUEST_CHANGES" if label == "APPROVE" else "APPROVE",
                "reason_codes": [],
            }
            for sample, label in truth.items()
        ]
        proxy_report = evaluate_three_arm(
            truth,
            {"EMPTY": inverted_rows, "REPO_BASELINE": inverted_rows, "CANDIDATE": candidate_rows},
            thresholds={
                "decision_accuracy_floor": 0.85,
                "reject_recall_floor": 0.70,
                "balanced_accuracy_floor": 0.75,
                "min_effect_pp": 5.0,
                "alpha": 0.05,
                "require_adjusted_significance": True,
                "ci_pass_subset_role": "DIAGNOSTIC",
            },
            power_plan={
                "required_sample_count": 80,
                "required_effective_groups": 60,
                "required_reject_count": 24,
                "required_approve_count": 40,
            },
            bootstrap_resamples=1000,
            seed=20260807,
        )
        if proxy_report["status"] != "INCONCLUSIVE":
            raise CLIError("the tiny demo proxy must remain statistically INCONCLUSIVE")
        _write_json(output / "vertical_slice" / "offline_proxy_report.json", proxy_report)

        store.transition_run_phase(RunPhase.VERTICAL_SLICE, reason="safe synthetic three-arm slice")
        _run_demo_task(
            scheduler,
            budget,
            TaskSpec("vertical-slice", RunPhase.VERTICAL_SLICE, packet={"arms": 3}),
            worst_case=BudgetVector(tokens=1500, wall_seconds=3, tool_calls=3),
            actual=BudgetVector(tokens=900, wall_seconds=2, tool_calls=3),
            result={"proxy_status": proxy_report["status"], "construct": loop_report["construct"]},
        )

        store.transition_run_phase(RunPhase.DEVELOPMENT, reason="materialize best-so-far fixture")
        candidate_path = output / "candidate" / "AGENTS.md"
        candidate_bytes = (root / "AGENTS.md").read_bytes()
        _atomic_write(candidate_path, candidate_bytes)
        candidate_hash = sha256(candidate_bytes).hexdigest()
        _run_demo_task(
            scheduler,
            budget,
            TaskSpec("candidate-v0", RunPhase.DEVELOPMENT, packet={"artifact": "candidate/AGENTS.md"}),
            worst_case=BudgetVector(tokens=800, wall_seconds=2, tool_calls=1),
            actual=BudgetVector(tokens=500, wall_seconds=1, tool_calls=1),
            result={"candidate_hash": candidate_hash},
        )
        store.register_candidate(
            candidate_hash,
            str(candidate_path.relative_to(output)),
            0.80,
            {"offline_proxy": "INCONCLUSIVE", "agent_loop": "DESCRIPTIVE_PAIRED"},
        )
        store.promote_best_candidate(candidate_hash)
        store.record_dev_iteration(
            candidate_hash=candidate_hash,
            hypothesis="offline fixture proves the control path only",
            gain_pp=0.0,
            cash_microusd=0,
            elapsed_seconds=1,
        )

        runtime_report = _read_yaml(root / "config" / "runtime_capability_report.example.yaml")
        runtime_validation = validate_runtime_capability_report(runtime_report)
        if runtime_validation.ok:
            raise CLIError("example runtime report unexpectedly passed trusted capability gates")
        store.set_closeout_mode("trusted runtime capability probes are deliberately absent in demo")
        store.transition_run_phase(RunPhase.CLOSEOUT, reason="freeze best-so-far without crossing final barrier")
        store.mark_terminal(
            TerminalStatus.BLOCKED,
            reason="offline demo complete; real final test requires trusted runtime probes and protected data",
        )

        status = render_status(store, config, output / "STATUS.md")
        render_anytime_report(store, config, output / "ANYTIME_REPORT.md")
        summary = {
            "schema_version": "3.0.0",
            "run_id": config.run_id,
            "terminal_status": status["run"]["terminal_status"],
            "final_test_executed": False,
            "final_test_ready": False,
            "candidate_sha256": candidate_hash,
            "event_chain_valid": status["ledger"]["checksum_chain_valid"],
            "offline_proxy_status": proxy_report["status"],
            "agent_loop_inference": loop_report["inference"],
            "runtime_blocker_count": len(runtime_validation.errors),
            "network_used": False,
            "untrusted_code_executed": False,
        }
        summary["summary_sha256"] = sha256(_canonical_bytes(summary)).hexdigest()
        _write_json(output / "DEMO_SUMMARY.json", summary)
        return summary
    finally:
        store.close()


def _command_validate(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve() if args.project_root else asset_root()
    launcher_key: bytes | None = None
    if args.launcher_key_file:
        raw_key_path = Path(args.launcher_key_file).expanduser()
        if raw_key_path.is_symlink():
            raise CLIError("launcher key file must not be a symlink")
        key_path = raw_key_path.resolve()
        if not key_path.is_file():
            raise CLIError("launcher key file must be an existing regular file")
        launcher_key = key_path.read_bytes()
        if not 16 <= len(launcher_key) <= 4096:
            raise CLIError("launcher key file must contain 16..4096 raw bytes")
    resolution_path = (
        Path(args.artifact_resolution_manifest)
        if args.artifact_resolution_manifest
        else None
    )
    report = _project_validation(
        root,
        launcher_attestation_key=launcher_key,
        artifact_resolution_manifest=resolution_path,
    )
    if args.output:
        _write_json(Path(args.output).resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report["structural_ok"]:
        return 2
    if args.require_final_ready and not report["final_test_ready"]:
        return 3
    return 0


def _command_demo(args: argparse.Namespace) -> int:
    root = asset_root()
    config_path = Path(args.config).resolve() if args.config else root / "config" / "run_config.example.yaml"
    summary = _demo(root, Path(args.output).resolve(), config_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _command_status(args: argparse.Namespace) -> int:
    root = asset_root()
    config_path = Path(args.config).resolve() if args.config else root / "config" / "run_config.example.yaml"
    config = load_config(config_path)
    raw_state_path = Path(args.state).expanduser()
    if raw_state_path.is_symlink():
        raise CLIError(f"status requires an existing non-symlink SQLite file: {raw_state_path}")
    state_path = raw_state_path.resolve()
    if not state_path.is_file():
        raise CLIError(f"status requires an existing non-symlink SQLite file: {state_path}")
    with SQLiteStore(state_path, read_only=True) as store:
        status = build_status(store, config)
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _command_power(args: argparse.Namespace) -> int:
    plan = plan_three_arm_sample_size(
        reject_prevalence=args.reject_prevalence,
        discordance_by_baseline={"EMPTY": args.empty_discordance, "REPO_BASELINE": args.repo_discordance},
        target_delta=args.target_delta,
        expected_reject_recall=args.expected_reject_recall,
        min_reject_recall=args.min_reject_recall,
        expected_approve_recall=args.expected_approve_recall,
        min_approve_recall=args.min_approve_recall,
        familywise_alpha=args.alpha,
        power=args.power,
        confidence=args.confidence,
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _command_approval_hash(args: argparse.Namespace) -> int:
    value = _read_yaml(Path(args.config).resolve())
    if not isinstance(value, Mapping):
        raise CLIError("run config must be a mapping")
    print(approval_payload_sha256(value))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agents-v3",
        description="Fail-closed AGENTS.md orchestration and evaluation harness",
    )
    parser.add_argument("--version", action="version", version="agents-v3 3.0.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate published contracts and readiness")
    validate.add_argument("--project-root", help="source-tree asset root; installed assets are the default")
    validate.add_argument("--output", help="also write the JSON report atomically")
    validate.add_argument(
        "--require-final-ready",
        action="store_true",
        help="return non-zero unless trusted runtime probes and a final profile are ready",
    )
    validate.add_argument(
        "--launcher-key-file",
        help="protected raw HMAC key used only to verify the launcher attestation",
    )
    validate.add_argument(
        "--artifact-resolution-manifest",
        help="JSON map from frozen profile hash fields to actual files",
    )
    validate.set_defaults(handler=_command_validate)

    demo = subparsers.add_parser("demo", help="run the deterministic no-network vertical slice")
    demo.add_argument("--output", required=True, help="new, non-existing output directory")
    demo.add_argument("--config", help="mode=demo config; bundled example is the default")
    demo.set_defaults(handler=_command_demo)

    status = subparsers.add_parser("status", help="render machine-readable status from SQLite")
    status.add_argument("--state", required=True, help="SQLite state path")
    status.add_argument("--config", help="the exact run manifest; bundled demo is the default")
    status.set_defaults(handler=_command_status)

    power = subparsers.add_parser("power", help="plan a paired three-arm sample size")
    power.add_argument("--reject-prevalence", type=float, default=0.40)
    power.add_argument("--empty-discordance", type=float, default=0.20)
    power.add_argument("--repo-discordance", type=float, default=0.15)
    power.add_argument("--target-delta", type=float, default=0.05)
    power.add_argument("--expected-reject-recall", type=float, default=0.85)
    power.add_argument("--min-reject-recall", type=float, default=0.70)
    power.add_argument("--expected-approve-recall", type=float, default=0.90)
    power.add_argument("--min-approve-recall", type=float, default=0.75)
    power.add_argument("--alpha", type=float, default=0.05)
    power.add_argument("--power", type=float, default=0.80)
    power.add_argument("--confidence", type=float, default=0.95)
    power.set_defaults(handler=_command_power)

    approval = subparsers.add_parser("approval-hash", help="hash the canonical approval payload")
    approval.add_argument("config", help="run manifest YAML")
    approval.set_defaults(handler=_command_approval_hash)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CLIError, ValueError, RuntimeError, OSError) as exc:
        print(f"agents-v3: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
