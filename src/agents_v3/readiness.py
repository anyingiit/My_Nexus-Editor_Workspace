"""Cross-document gate for an immutable final-test profile.

Individual schemas establish shape.  This module establishes identity: every
security/runtime/config value that the profile claims to freeze must describe
the exact manifests presented to the controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from .config import RunConfig
from .profiles import validate_resolved_frozen_profile
from .runtime import (
    RuntimeCapabilityError,
    runtime_report_hash,
    validate_runtime_capability_report,
    verify_launcher_attestation,
)
from .security import capability_manifest_hash, validate_capability_manifest


class FinalReadinessError(ValueError):
    pass


REQUIRED_RESOLVED_ARTIFACT_PATHS = (
    "preregistration_sha256",
    "protocol_manifest_sha256",
    "schema_manifest_sha256",
    "candidate.core_sha256",
    "candidate.annex_manifest_sha256",
    "candidate.artifact_manifest_sha256",
    "baselines.empty_sha256",
    "baselines.repository_sha256",
    "knowledge.source_manifest_sha256",
    "knowledge.direct_commit_manifest_sha256",
    "knowledge.third_party_manifest_sha256",
    "dataset.eligibility_manifest_sha256",
    "dataset.split_manifest_sha256",
    "dataset.grouping_manifest_sha256",
    "dataset.snapshot_manifest_sha256",
    "dataset.ci_manifest_sha256",
    "dataset.rubric_manifest_sha256",
    "judge.system_prompt_sha256",
    "judge.user_prompt_template_sha256",
    "judge.output_schema_sha256",
    "packing.implementation_sha256",
    "retrieval.corpus_sha256",
    "retrieval.algorithm_sha256",
    "retrieval.selection_manifest_sha256",
    "analysis.implementation_sha256",
    "runtime.harness_tree_sha256",
    "runtime.truth_vault_version_sha256",
)


@dataclass(frozen=True)
class ReadinessFinding:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class FinalReadinessValidation:
    errors: tuple[ReadinessFinding, ...]
    profile: Mapping[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise FinalReadinessError(
                "; ".join(
                    f"{item.path}: {item.code}: {item.message}"
                    for item in self.errors
                )
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.__dict__ for item in self.errors],
            "profile_sha256": self.profile.get("profile_sha256") if self.profile else None,
        }


def _utc(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_sha256(value: Any) -> str:
    def plain(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [plain(child) for child in item]
        return item

    return sha256(
        json.dumps(
            plain(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _path_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for component in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(component)
    return current


def validate_final_readiness(
    *,
    profile: Mapping[str, Any],
    config: RunConfig,
    runtime_report: Mapping[str, Any],
    capability_manifest: Mapping[str, Any],
    dependency_lock_bytes: bytes,
    launcher_attestation_key: bytes | None,
    resolved_artifacts: Mapping[str, bytes] | None,
    now: datetime | None = None,
) -> FinalReadinessValidation:
    errors: list[ReadinessFinding] = []

    def finding(path: str, code: str, message: str) -> None:
        errors.append(ReadinessFinding(path, code, message))

    runtime_validation = validate_runtime_capability_report(runtime_report)
    for item in runtime_validation.errors:
        finding(f"runtime_report.{item.path}", item.code, item.message)
    security_validation = validate_capability_manifest(capability_manifest)
    for item in security_validation.errors:
        finding(f"capability_manifest.{item.path}", item.code, item.message)

    if not isinstance(dependency_lock_bytes, bytes) or not dependency_lock_bytes:
        dependency_lock_sha256 = None
        finding("dependency_lock", "missing_bytes", "controller must receive non-empty lock bytes")
    else:
        dependency_lock_sha256 = sha256(dependency_lock_bytes).hexdigest()
        if dependency_lock_sha256 != config.raw["dependency_lock_sha256"]:
            finding(
                "dependency_lock",
                "approved_lock_mismatch",
                "actual lock bytes do not match the run manifest approved before binding",
            )

    approved_launcher_key_sha256 = config.raw["sandbox"]["launcher_attestation_key_sha256"]
    if launcher_attestation_key is None:
        finding(
            "runtime_report.launcher_attestation",
            "missing_trust_root",
            "final readiness requires the protected launcher attestation key",
        )
    else:
        try:
            verify_launcher_attestation(
                runtime_report,
                launcher_attestation_key,
                approved_launcher_key_sha256,
            )
        except RuntimeCapabilityError as exc:
            finding("runtime_report.launcher_attestation", "invalid_attestation", str(exc))

    if config.mode != "final":
        finding("run_config.mode", "not_final", "must be final")
    try:
        frozen = validate_resolved_frozen_profile(profile)
    except (TypeError, ValueError) as exc:
        finding("profile", "invalid_frozen_profile", str(exc))
        return FinalReadinessValidation(tuple(errors), None)

    evidence = resolved_artifacts if isinstance(resolved_artifacts, Mapping) else {}
    missing_artifacts = sorted(set(REQUIRED_RESOLVED_ARTIFACT_PATHS) - set(evidence))
    extra_artifacts = sorted(set(evidence) - set(REQUIRED_RESOLVED_ARTIFACT_PATHS))
    if missing_artifacts:
        finding(
            "resolved_artifacts",
            "missing_artifact_bytes",
            f"missing profile paths: {missing_artifacts}",
        )
    if extra_artifacts:
        finding(
            "resolved_artifacts",
            "unreviewed_artifact_paths",
            f"unexpected profile paths: {extra_artifacts}",
        )
    for path in REQUIRED_RESOLVED_ARTIFACT_PATHS:
        content = evidence.get(path)
        if not isinstance(content, bytes):
            continue
        actual = sha256(content).hexdigest()
        expected = _path_value(frozen, path)
        if actual != expected:
            finding(
                f"profile.{path}",
                "artifact_bytes_mismatch",
                f"resolved bytes hash to {actual}, profile declares {expected}",
            )

    try:
        runtime_digest = runtime_report_hash(runtime_report)
    except (TypeError, ValueError) as exc:
        runtime_digest = None
        finding("runtime_report", "noncanonical_hash_input", str(exc))
    try:
        capability_digest = capability_manifest_hash(capability_manifest)
    except (TypeError, ValueError) as exc:
        capability_digest = None
        finding("capability_manifest", "noncanonical_hash_input", str(exc))

    for path, actual, expected in (
        ("profile.schema_version", frozen["schema_version"], config.schema_version),
        ("profile.run_id", frozen["run_id"], config.run_id),
        ("runtime_report.run_id", runtime_report.get("run_id"), config.run_id),
        ("runtime_report.run_config_sha256", runtime_report.get("run_config_sha256"), config.fingerprint()),
        ("profile.runtime.run_config_sha256", frozen["runtime"]["run_config_sha256"], config.fingerprint()),
        (
            "profile.runtime.runtime_capability_report_sha256",
            frozen["runtime"]["runtime_capability_report_sha256"],
            runtime_digest,
        ),
        (
            "profile.runtime.capability_manifest_sha256",
            frozen["runtime"]["capability_manifest_sha256"],
            capability_digest,
        ),
        (
            "runtime_report.capability_manifest_sha256",
            runtime_report.get("capability_manifest_sha256"),
            capability_digest,
        ),
        (
            "profile.runtime.dependency_lock_sha256",
            frozen["runtime"]["dependency_lock_sha256"],
            dependency_lock_sha256,
        ),
        ("profile.judge.provider", frozen["judge"]["provider"], config.evaluation_limit.provider),
        ("profile.judge.model", frozen["judge"]["model"], config.evaluation_limit.model),
        (
            "profile.judge.max_output_tokens",
            frozen["judge"]["max_output_tokens"],
            config.evaluation_limit.max_output_tokens,
        ),
        (
            "profile.packing.max_input_tokens",
            frozen["packing"]["max_input_tokens"],
            config.evaluation_limit.max_input_tokens,
        ),
        (
            "profile.execution.retry_policy_sha256",
            frozen["execution"]["retry_policy_sha256"],
            _canonical_sha256(config.raw["retry"]),
        ),
    ):
        if actual != expected:
            finding(path, "binding_mismatch", f"expected {expected!r}, got {actual!r}")

    cutoff = config.raw["dataset"]["knowledge_cutoff"]
    if _utc(frozen["knowledge"]["cutoff"]) != _utc(cutoff):
        finding(
            "profile.knowledge.cutoff",
            "binding_mismatch",
            "must equal run_config.dataset.knowledge_cutoff",
        )

    generated_at = _utc(runtime_report.get("generated_at"))
    frozen_at = _utc(frozen.get("frozen_at"))
    approved_at = _utc(config.raw["approval"]["confirmed_at"])
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    deadline_at = config.deadline_at.astimezone(timezone.utc).isoformat()
    if approved_at is None or generated_at is None or approved_at > generated_at:
        finding(
            "runtime_report.generated_at",
            "before_approval",
            "runtime attestation must be generated after the approved run manifest",
        )
    if generated_at is None or frozen_at is None or generated_at > frozen_at:
        finding(
            "runtime_report.generated_at",
            "invalid_attestation_order",
            "runtime report must be generated no later than profile freeze",
        )
    if frozen_at is None or frozen_at > deadline_at:
        finding(
            "profile.frozen_at",
            "after_deadline",
            "profile must be frozen no later than the approved deadline",
        )
    if frozen_at is None or frozen_at > instant:
        finding(
            "profile.frozen_at",
            "future_freeze",
            "profile freeze time cannot be in the future",
        )
    if instant > deadline_at:
        finding("run_config.time.deadline_at", "deadline_elapsed", "final readiness expired")

    metrics = config.raw["metrics"]
    statistics = config.raw["statistics"]
    expected_analysis = {
        "alpha": statistics["alpha"],
        "target_power": statistics["target_power"],
        "bootstrap_resamples": statistics["bootstrap_resamples"],
        "bootstrap_seed": statistics["bootstrap_seed"],
        "min_effect_pp": metrics["min_effect_pp"],
        "decision_accuracy_floor": metrics["decision_accuracy_floor"],
        "reject_recall_floor": metrics["reject_recall_floor"],
        "balanced_accuracy_floor": metrics["balanced_accuracy_floor"],
        "multiplicity": statistics["multiplicity"],
        "ci_pass_subset_role": (
            "CO_PRIMARY" if metrics["ci_pass_subset_is_coprimary"] else "DIAGNOSTIC"
        ),
    }
    for key, expected in expected_analysis.items():
        actual = frozen["analysis"][key]
        if actual != expected:
            finding(
                f"profile.analysis.{key}",
                "binding_mismatch",
                f"expected {expected!r}, got {actual!r}",
            )

    runner = next(
        (
            role
            for role in runtime_report.get("roles", [])
            if isinstance(role, Mapping) and role.get("role") == "untrusted_code_runner"
        ),
        None,
    )
    if runner is None:
        finding("runtime_report.roles", "missing_runner", "untrusted_code_runner is required")
    elif runner.get("image_digest") != frozen["runtime"]["sandbox_image_digest"]:
        finding(
            "profile.runtime.sandbox_image_digest",
            "binding_mismatch",
            "must equal the measured untrusted_code_runner image digest",
        )
    else:
        limits = runner.get("resource_limits", {})
        sandbox = config.raw["sandbox"]
        expected_limits = {
            "cpus": sandbox["max_cpus"],
            "memory_mb": sandbox["max_memory_mb"],
            "pids": sandbox["max_pids"],
            "disk_mb": sandbox["max_disk_mb"],
            "output_mb": sandbox["max_output_mb"],
            "wall_clock_seconds": sandbox["max_wall_clock_seconds_per_job"],
        }
        for key, ceiling in expected_limits.items():
            actual = limits.get(key)
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or actual > ceiling:
                finding(
                    f"runtime_report.roles.untrusted_code_runner.resource_limits.{key}",
                    "limit_not_enforced",
                    f"measured limit must be numeric and <= {ceiling!r}",
                )

    runtime_roles = {
        str(role.get("role")): role
        for role in runtime_report.get("roles", [])
        if isinstance(role, Mapping) and isinstance(role.get("role"), str)
    }
    static_roles = capability_manifest.get("roles", {})
    if isinstance(static_roles, Mapping):
        for role_name, static_role in static_roles.items():
            measured = runtime_roles.get(str(role_name))
            if not isinstance(static_role, Mapping) or not isinstance(measured, Mapping):
                continue
            static_network = static_role.get("network", {})
            measured_network = measured.get("network", {})
            expected_network = {
                "mode": static_network.get("mode"),
                "allowed_hosts": static_network.get("hosts", []),
                "allowed_methods": static_network.get("methods", []),
            }
            if measured_network != expected_network:
                finding(
                    f"runtime_report.roles.{role_name}.network",
                    "capability_policy_mismatch",
                    "measured network must equal the frozen static role policy",
                )
            static_mounts = {
                (item.get("source_id"), item.get("class"), item.get("target"), item.get("access"))
                for item in static_role.get("mounts", [])
                if isinstance(item, Mapping)
            }
            measured_mounts = {
                (item.get("source_id"), item.get("class"), item.get("target"), item.get("access"))
                for item in measured.get("mounts", [])
                if isinstance(item, Mapping)
            }
            if measured_mounts != static_mounts:
                finding(
                    f"runtime_report.roles.{role_name}.mounts",
                    "capability_policy_mismatch",
                    "measured mount classes/targets/access must equal the frozen policy",
                )
            expected_writable = sorted(
                target
                for _, _, target, access in static_mounts
                if access in {"rw", "wo"} and isinstance(target, str)
            )
            if sorted(measured.get("writable_paths", [])) != expected_writable:
                finding(
                    f"runtime_report.roles.{role_name}.writable_paths",
                    "capability_policy_mismatch",
                    "writable paths must be exactly the policy's rw/wo mount targets",
                )

    if isinstance(runner, Mapping):
        static_runner = static_roles.get("untrusted_code_runner", {}) if isinstance(static_roles, Mapping) else {}
        static_limits = static_runner.get("controls", {}).get("resources", {}) if isinstance(static_runner, Mapping) else {}
        measured_limits = runner.get("resource_limits", {})
        limit_names = {
            "cpus": "cpus",
            "memory_mb": "memory_mb",
            "pids": "pids",
            "disk_mb": "disk_mb",
            "wall_clock_seconds": "wall_seconds",
        }
        for measured_key, static_key in limit_names.items():
            actual = measured_limits.get(measured_key)
            ceiling = static_limits.get(static_key)
            if (
                not isinstance(actual, (int, float))
                or isinstance(actual, bool)
                or not isinstance(ceiling, (int, float))
                or isinstance(ceiling, bool)
                or actual > ceiling
            ):
                finding(
                    f"runtime_report.roles.untrusted_code_runner.resource_limits.{measured_key}",
                    "static_limit_mismatch",
                    f"measured limit must be <= static capability limit {ceiling!r}",
                )

    return FinalReadinessValidation(tuple(errors), frozen)
