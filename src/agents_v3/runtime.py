"""Validation gate for measured runtime capabilities.

This module does not turn declarations into proof.  A trusted launcher must
perform the negative probes and sign/content-address the resulting report.  The
controller calls :func:`assert_runtime_capability_report` before collection,
model access, protected evaluation, or untrusted-code execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping, Sequence

from .schema_gate import SchemaValidationError, assert_published_schema


MANDATORY_RUNTIME_ROLES = frozenset(
    {
        "collector",
        "train_evidence_miner",
        "candidate_worker",
        "judge",
        "artifact_evaluator",
        "dev_evaluator",
        "final_test_evaluator",
        "untrusted_code_runner",
    }
)
REQUIRED_NEGATIVE_PROBES = (
    "github_read_only",
    "github_write_denied",
    "unauthorized_truth_paths_absent",
    "host_home_absent",
    "runtime_socket_absent",
    "path_escape_denied",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RuntimeCapabilityError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeFinding:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class RuntimeValidation:
    errors: tuple[RuntimeFinding, ...]
    computed_all_probes_passed: bool

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise RuntimeCapabilityError(
                "; ".join(f"{item.path}: {item.code}: {item.message}" for item in self.errors)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "computed_all_probes_passed": self.computed_all_probes_passed,
            "errors": [item.__dict__ for item in self.errors],
        }


def _canonical_report_bytes(report: Mapping[str, Any]) -> bytes:
    content = {key: value for key, value in report.items() if key != "manifest_sha256"}
    return json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def runtime_report_hash(report: Mapping[str, Any]) -> str:
    return sha256(_canonical_report_bytes(report)).hexdigest()


def _launcher_attestation_payload(report: Mapping[str, Any]) -> bytes:
    content = deepcopy(dict(report))
    content.pop("manifest_sha256", None)
    attestation = content.get("launcher_attestation")
    if not isinstance(attestation, dict):
        raise RuntimeCapabilityError("launcher_attestation must be an object")
    attestation.pop("report_hmac_sha256", None)
    return json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sign_runtime_capability_report(
    report: Mapping[str, Any], launcher_key: bytes, *, nonce: str
) -> dict[str, Any]:
    """Return a copied report signed by the protected outer launcher key."""

    if not isinstance(launcher_key, bytes) or len(launcher_key) < 16:
        raise RuntimeCapabilityError("launcher attestation key must contain at least 16 bytes")
    if not isinstance(nonce, str) or not 16 <= len(nonce) <= 256 or "REPLACE" in nonce.upper():
        raise RuntimeCapabilityError("launcher nonce must be a resolved 16..256 character string")
    signed = deepcopy(dict(report))
    signed["launcher_attestation"] = {
        "algorithm": "HMAC-SHA256",
        "key_sha256": sha256(launcher_key).hexdigest(),
        "nonce": nonce,
        "report_hmac_sha256": "0" * 64,
    }
    signed["launcher_attestation"]["report_hmac_sha256"] = hmac.new(
        launcher_key,
        _launcher_attestation_payload(signed),
        sha256,
    ).hexdigest()
    signed["manifest_sha256"] = runtime_report_hash(signed)
    return signed


def verify_launcher_attestation(
    report: Mapping[str, Any], launcher_key: bytes, expected_key_sha256: str
) -> None:
    if not isinstance(launcher_key, bytes) or len(launcher_key) < 16:
        raise RuntimeCapabilityError("launcher attestation key must contain at least 16 bytes")
    actual_key_sha256 = sha256(launcher_key).hexdigest()
    if not hmac.compare_digest(actual_key_sha256, expected_key_sha256):
        raise RuntimeCapabilityError("launcher key does not match the approved fingerprint")
    attestation = report.get("launcher_attestation")
    if not isinstance(attestation, Mapping):
        raise RuntimeCapabilityError("launcher attestation is missing")
    if attestation.get("algorithm") != "HMAC-SHA256":
        raise RuntimeCapabilityError("launcher attestation algorithm is not HMAC-SHA256")
    if not hmac.compare_digest(str(attestation.get("key_sha256", "")), actual_key_sha256):
        raise RuntimeCapabilityError("launcher attestation key fingerprint mismatch")
    expected_hmac = hmac.new(
        launcher_key,
        _launcher_attestation_payload(report),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(str(attestation.get("report_hmac_sha256", "")), expected_hmac):
        raise RuntimeCapabilityError("launcher attestation HMAC mismatch")


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_runtime_capability_report(report: Mapping[str, Any]) -> RuntimeValidation:
    errors: list[RuntimeFinding] = []
    if not isinstance(report, Mapping):
        return RuntimeValidation((RuntimeFinding("$", "invalid_report", "must be an object"),), False)
    schema_ok = True
    try:
        report = assert_published_schema(report, "runtime_capability_report.schema.json")
    except SchemaValidationError as exc:
        schema_ok = False
        errors.extend(
            RuntimeFinding(item.path, f"schema_{item.keyword}", item.message)
            for item in exc.findings
        )
    if report.get("schema_version") != "3.0.0":
        errors.append(RuntimeFinding("schema_version", "invalid_version", "must be 3.0.0"))
    if not isinstance(report.get("run_id"), str) or not report.get("run_id"):
        errors.append(RuntimeFinding("run_id", "missing_run_id", "must be non-empty"))
    if not _timestamp(report.get("generated_at")):
        errors.append(RuntimeFinding("generated_at", "invalid_timestamp", "timezone is required"))
    generated_by = report.get("generated_by")
    if not isinstance(generated_by, str) or not generated_by:
        errors.append(RuntimeFinding("generated_by", "missing_identity", "probe identity is required"))
    elif "REPLACE" in generated_by.upper() or "REQUIRED" in generated_by.upper():
        errors.append(RuntimeFinding("generated_by", "placeholder_identity", "trusted launcher identity is unresolved"))

    attestation = report.get("launcher_attestation")
    if not isinstance(attestation, Mapping):
        errors.append(RuntimeFinding("launcher_attestation", "missing_attestation", "is required"))
    elif report.get("all_required_probes_passed") is True:
        if attestation.get("key_sha256") == "0" * 64:
            errors.append(RuntimeFinding("launcher_attestation.key_sha256", "placeholder_hash", "must bind an approved launcher key"))
        if attestation.get("report_hmac_sha256") == "0" * 64:
            errors.append(RuntimeFinding("launcher_attestation.report_hmac_sha256", "placeholder_hmac", "passed reports require a launcher HMAC"))
        nonce = attestation.get("nonce")
        if not isinstance(nonce, str) or "REPLACE" in nonce.upper():
            errors.append(RuntimeFinding("launcher_attestation.nonce", "placeholder_nonce", "passed reports require a resolved single-use nonce"))

    if report.get("all_required_probes_passed") is True and report.get("run_config_sha256") == "0" * 64:
        errors.append(RuntimeFinding("run_config_sha256", "placeholder_hash", "passed report must bind the approved run config"))

    roles = report.get("roles")
    role_map: dict[str, Mapping[str, Any]] = {}
    if not isinstance(roles, Sequence) or isinstance(roles, (str, bytes)):
        errors.append(RuntimeFinding("roles", "invalid_roles", "must be an array"))
        roles = []
    for index, role in enumerate(roles):
        if not isinstance(role, Mapping):
            errors.append(RuntimeFinding(f"roles[{index}]", "invalid_role", "must be an object"))
            continue
        name = role.get("role")
        if not isinstance(name, str) or not name:
            errors.append(RuntimeFinding(f"roles[{index}].role", "missing_role", "is required"))
            continue
        if name in role_map:
            errors.append(RuntimeFinding(f"roles[{index}].role", "duplicate_role", name))
            continue
        role_map[name] = role
    missing = sorted(MANDATORY_RUNTIME_ROLES - set(role_map))
    extra = sorted(set(role_map) - MANDATORY_RUNTIME_ROLES)
    if missing:
        errors.append(RuntimeFinding("roles", "missing_roles", str(missing)))
    if extra:
        errors.append(RuntimeFinding("roles", "unreviewed_roles", str(extra)))

    all_probes = schema_ok and not missing and not extra and len(role_map) == len(MANDATORY_RUNTIME_ROLES)
    for name in sorted(MANDATORY_RUNTIME_ROLES & set(role_map)):
        role = role_map[name]
        for field in ("process_identity", "executable"):
            value = role.get(field)
            if (
                not isinstance(value, str)
                or not value
                or "REPLACE" in value.upper()
                or "REQUIRED" in value.upper()
            ):
                errors.append(RuntimeFinding(f"roles.{name}.{field}", "placeholder_identity", "must be measured by the trusted launcher"))
                all_probes = False
        image_digest = role.get("image_digest")
        if image_digest is not None and image_digest == "sha256:" + "0" * 64:
            errors.append(RuntimeFinding(f"roles.{name}.image_digest", "placeholder_digest", "zero image digest is forbidden"))
            all_probes = False
        if name == "untrusted_code_runner" and not isinstance(image_digest, str):
            errors.append(RuntimeFinding(f"roles.{name}.image_digest", "missing_image_digest", "runner image must be digest-pinned"))
            all_probes = False
        probes = role.get("probes")
        if not isinstance(probes, Mapping):
            errors.append(RuntimeFinding(f"roles.{name}.probes", "missing_probes", "must be an object"))
            all_probes = False
            continue
        for probe in REQUIRED_NEGATIVE_PROBES:
            if probes.get(probe) is not True:
                errors.append(
                    RuntimeFinding(
                        f"roles.{name}.probes.{probe}",
                        "probe_not_passed",
                        "must be measured true by the trusted launcher",
                    )
                )
                all_probes = False
        network = role.get("network")
        if not isinstance(network, Mapping):
            errors.append(RuntimeFinding(f"roles.{name}.network", "missing_network", "is required"))
            all_probes = False
        elif name == "collector":
            if network.get("mode") != "allowlist" or network.get("allowed_hosts") != ["api.github.com"] or network.get("allowed_methods") != ["GET"]:
                errors.append(RuntimeFinding(f"roles.{name}.network", "unsafe_collector_network", "must be GET-only api.github.com"))
                all_probes = False
        elif network.get("mode") != "none" or network.get("allowed_hosts") not in ([], None) or network.get("allowed_methods") not in ([], None):
            errors.append(RuntimeFinding(f"roles.{name}.network", "network_not_denied", "must be none"))
            all_probes = False

        for field in ("secret_mounts", "sockets", "devices"):
            if role.get(field) != []:
                errors.append(RuntimeFinding(f"roles.{name}.{field}", "capability_present", "must be empty"))
                all_probes = False
        secret_names = {
            "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "DOCKER_HOST",
        }
        environment_names = role.get("environment_variable_names")
        if isinstance(environment_names, Sequence) and not isinstance(environment_names, (str, bytes)):
            inherited = sorted(secret_names & set(environment_names))
            if inherited:
                errors.append(RuntimeFinding(f"roles.{name}.environment_variable_names", "secret_environment_present", str(inherited)))
                all_probes = False
        limits = role.get("resource_limits")
        if not isinstance(limits, Mapping) or any(
            isinstance(limits.get(key), bool)
            or not isinstance(limits.get(key), (int, float))
            or limits.get(key) <= 0
            for key in ("cpus", "memory_mb", "pids", "disk_mb", "output_mb", "wall_clock_seconds")
        ):
            errors.append(RuntimeFinding(f"roles.{name}.resource_limits", "invalid_limits", "all hard limits must be positive"))
            all_probes = False

    declared = report.get("all_required_probes_passed")
    if not isinstance(declared, bool) or declared != all_probes:
        errors.append(
            RuntimeFinding(
                "all_required_probes_passed",
                "inconsistent_summary",
                f"must equal computed result {all_probes}",
            )
        )
    capability_hash = report.get("capability_manifest_sha256")
    if all_probes and capability_hash == "0" * 64:
        errors.append(RuntimeFinding("capability_manifest_sha256", "placeholder_hash", "passed report must bind the measured static policy"))
        all_probes = False
    manifest_hash = report.get("manifest_sha256")
    try:
        expected_hash = runtime_report_hash(report)
    except (TypeError, ValueError) as exc:
        errors.append(RuntimeFinding("$", "noncanonical_report", str(exc)))
        expected_hash = None
        all_probes = False
    if all_probes:
        if not isinstance(manifest_hash, str) or _SHA256_RE.fullmatch(manifest_hash) is None:
            errors.append(RuntimeFinding("manifest_sha256", "missing_hash", "passed reports must be content-addressed"))
        elif manifest_hash != expected_hash:
            errors.append(RuntimeFinding("manifest_sha256", "hash_mismatch", "does not match canonical report bytes"))
    elif expected_hash is not None and manifest_hash not in (None, expected_hash):
        errors.append(RuntimeFinding("manifest_sha256", "invalid_failed_report_hash", "must be null or the canonical hash"))
    return RuntimeValidation(tuple(errors), all_probes)


def assert_runtime_capability_report(report: Mapping[str, Any]) -> str:
    validation = validate_runtime_capability_report(report)
    validation.raise_for_errors()
    if not validation.computed_all_probes_passed:
        raise RuntimeCapabilityError("runtime probes did not pass; expensive or privileged work is forbidden")
    return runtime_report_hash(report)
