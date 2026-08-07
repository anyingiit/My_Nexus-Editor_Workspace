"""Fail-closed capability and access-manifest validation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import posixpath
from typing import Any, Mapping, Sequence

from .schema_gate import SchemaValidationError, assert_published_schema


REQUIRED_ROLES = frozenset(
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

ACCESS_MATRIX: Mapping[str, Mapping[str, str]] = {
    "collector": {
        "train": "isolated_write_only",
        "dev": "isolated_write_only",
        "test": "isolated_write_only",
    },
    "train_evidence_miner": {"train": "read", "dev": "none", "test": "none"},
    "candidate_worker": {"train": "derived_only", "dev": "none", "test": "none"},
    "judge": {"train": "none", "dev": "none", "test": "none"},
    "artifact_evaluator": {"train": "none", "dev": "none", "test": "none"},
    "dev_evaluator": {"train": "none", "dev": "read", "test": "none"},
    "final_test_evaluator": {"train": "none", "dev": "none", "test": "read"},
    "untrusted_code_runner": {"train": "none", "dev": "none", "test": "none"},
}

FORBIDDEN_MOUNT_CLASSES = frozenset(
    {
        "host_home",
        "host_root",
        "docker_socket",
        "model_credentials",
        "github_write_credentials",
    }
)
FORBIDDEN_EXTERNAL_TOOLS = frozenset(
    {"github_push", "github_merge", "github_comment", "git_push", "docker_socket"}
)

# A matching `ground_truth_access` declaration is not sufficient when a role
# can mount the underlying protected bytes under another path.  These data
# classes therefore have a single allowed reader and an exact access mode.
PROTECTED_MOUNT_POLICY: Mapping[str, tuple[str, str]] = {
    "train_ground_truth": ("train_evidence_miner", "ro"),
    "dev_ground_truth": ("dev_evaluator", "ro"),
    "test_ground_truth": ("final_test_evaluator", "ro"),
}


class SecurityManifestError(ValueError):
    """Raised when a capability manifest does not enforce the access contract."""


@dataclass(frozen=True)
class SecurityFinding:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class SecurityValidation:
    errors: tuple[SecurityFinding, ...]
    warnings: tuple[SecurityFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise SecurityManifestError(
                "; ".join(
                    f"{finding.path}: {finding.code}: {finding.message}"
                    for finding in self.errors
                )
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [finding.__dict__ for finding in self.errors],
            "warnings": [finding.__dict__ for finding in self.warnings],
        }


def capability_manifest_hash(manifest: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _finding(errors: list[SecurityFinding], path: str, code: str, message: str) -> None:
    errors.append(SecurityFinding(path, code, message))


def _sequence_of_strings(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, str) and item for item in value)
    )


def _validate_mounts(role: str, mounts: Any, errors: list[SecurityFinding]) -> None:
    path = f"roles.{role}.mounts"
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes)):
        _finding(errors, path, "invalid_mounts", "must be an array")
        return
    seen_targets: set[str] = set()
    classes: dict[str, list[str]] = {}
    for index, mount in enumerate(mounts):
        mount_path = f"{path}[{index}]"
        if not isinstance(mount, Mapping):
            _finding(errors, mount_path, "invalid_mount", "must be an object")
            continue
        target = mount.get("target")
        access = mount.get("access")
        data_class = mount.get("class")
        if not isinstance(target, str) or not target.startswith("/"):
            _finding(errors, f"{mount_path}.target", "invalid_target", "must be an absolute path")
            continue
        normalized = posixpath.normpath(target)
        if normalized in {"/", "/workspace", "/home", "/root", "/Users"}:
            _finding(errors, f"{mount_path}.target", "broad_mount", f"{normalized!r} is too broad")
        sensitive_fragments = (
            "/.ssh",
            "/.aws",
            "/.config/gcloud",
            "/docker.sock",
            "/.codex",
        )
        if any(fragment in normalized for fragment in sensitive_fragments):
            _finding(errors, f"{mount_path}.target", "sensitive_mount", "host-sensitive path is forbidden")
        if target in seen_targets:
            _finding(errors, f"{mount_path}.target", "duplicate_mount", "target is mounted more than once")
        seen_targets.add(target)
        if access not in {"ro", "rw", "wo"}:
            _finding(errors, f"{mount_path}.access", "invalid_access", "must be ro, rw, or wo")
        if not isinstance(data_class, str) or not data_class:
            _finding(errors, f"{mount_path}.class", "missing_class", "data classification is required")
        else:
            classes.setdefault(data_class, []).append(str(access))
            if data_class in FORBIDDEN_MOUNT_CLASSES:
                _finding(errors, f"{mount_path}.class", "forbidden_class", f"{data_class!r} is forbidden")
            protected_policy = PROTECTED_MOUNT_POLICY.get(data_class)
            if protected_policy is not None:
                allowed_role, allowed_access = protected_policy
                if role != allowed_role:
                    _finding(
                        errors,
                        f"{mount_path}.class",
                        "protected_mount_role_violation",
                        f"{data_class!r} may only be mounted by {allowed_role}",
                    )
                if access != allowed_access:
                    _finding(
                        errors,
                        f"{mount_path}.access",
                        "protected_mount_access_violation",
                        f"{data_class!r} must be mounted {allowed_access}",
                    )
            lowered = data_class.lower()
            if (
                any(marker in lowered for marker in ("ground_truth", "protected_label", "review_comment"))
                and data_class not in PROTECTED_MOUNT_POLICY
            ):
                _finding(
                    errors,
                    f"{mount_path}.class",
                    "unreviewed_protected_class",
                    "protected data classes must be registered in PROTECTED_MOUNT_POLICY",
                )

    if role == "untrusted_code_runner":
        if classes.get("untrusted_source") != ["ro"]:
            _finding(
                errors,
                path,
                "runner_source_not_read_only",
                "runner requires exactly one read-only untrusted_source mount",
            )
        output_access = classes.get("runner_output", [])
        if len(output_access) != 1 or output_access[0] not in {"rw", "wo"}:
            _finding(
                errors,
                path,
                "runner_output_missing",
                "runner requires one dedicated writable runner_output mount",
            )


def _validate_runner_controls(role: Mapping[str, Any], errors: list[SecurityFinding]) -> None:
    controls = role.get("controls")
    path = "roles.untrusted_code_runner.controls"
    if not isinstance(controls, Mapping):
        _finding(errors, path, "missing_controls", "runner controls are required")
        return
    required_true = ("rootless", "read_only_rootfs", "no_new_privileges")
    for key in required_true:
        if controls.get(key) is not True:
            _finding(errors, f"{path}.{key}", "unsafe_control", "must be true")
    cap_drop = controls.get("cap_drop")
    if not _sequence_of_strings(cap_drop) or set(cap_drop) != {"ALL"}:
        _finding(errors, f"{path}.cap_drop", "capabilities_not_dropped", "must be exactly ['ALL']")
    uid = controls.get("user_uid")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
        _finding(errors, f"{path}.user_uid", "root_user", "must be a positive non-root numeric UID")
    resources = controls.get("resources")
    if not isinstance(resources, Mapping):
        _finding(errors, f"{path}.resources", "missing_limits", "resource limits are required")
        return
    for key in ("cpus", "memory_mb", "pids", "disk_mb", "wall_seconds"):
        value = resources.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            _finding(errors, f"{path}.resources.{key}", "invalid_limit", "must be a positive number")


def validate_capability_manifest(manifest: Mapping[str, Any]) -> SecurityValidation:
    """Validate the Stage-0 role manifest against the mandatory access matrix."""

    errors: list[SecurityFinding] = []
    warnings: list[SecurityFinding] = []
    if not isinstance(manifest, Mapping):
        return SecurityValidation((SecurityFinding("$", "invalid_manifest", "must be an object"),))
    try:
        manifest = assert_published_schema(manifest, "capability_manifest.schema.json")
    except SchemaValidationError as exc:
        errors.extend(
            SecurityFinding(item.path, f"schema_{item.keyword}", item.message)
            for item in exc.findings
        )
    if not isinstance(manifest.get("schema_version"), str) or not manifest["schema_version"]:
        _finding(errors, "schema_version", "missing_schema_version", "is required")
    if manifest.get("default_deny") is not True:
        _finding(errors, "default_deny", "not_fail_closed", "must be true")
    roles = manifest.get("roles")
    if not isinstance(roles, Mapping):
        _finding(errors, "roles", "invalid_roles", "must be an object")
        return SecurityValidation(tuple(errors), tuple(warnings))
    missing_roles = sorted(REQUIRED_ROLES - set(roles))
    extra_roles = sorted(set(roles) - REQUIRED_ROLES)
    if missing_roles:
        _finding(errors, "roles", "missing_roles", f"missing mandatory roles: {missing_roles}")
    if extra_roles:
        _finding(
            errors,
            "roles",
            "unreviewed_roles",
            f"unreviewed roles must be added to the frozen matrix first: {extra_roles}",
        )

    for role_name in sorted(REQUIRED_ROLES & set(roles)):
        role = roles[role_name]
        base = f"roles.{role_name}"
        if not isinstance(role, Mapping):
            _finding(errors, base, "invalid_role", "must be an object")
            continue

        access = role.get("ground_truth_access")
        if access != ACCESS_MATRIX[role_name]:
            _finding(
                errors,
                f"{base}.ground_truth_access",
                "access_matrix_mismatch",
                f"must equal {dict(ACCESS_MATRIX[role_name])}",
            )

        network = role.get("network")
        if not isinstance(network, Mapping):
            _finding(errors, f"{base}.network", "missing_network_policy", "must be an object")
        elif role_name == "collector":
            if network.get("mode") != "allowlist":
                _finding(
                    errors,
                    f"{base}.network.mode",
                    "collector_network_not_allowlisted",
                    "must be allowlist",
                )
            hosts = network.get("hosts")
            if not _sequence_of_strings(hosts) or not hosts or set(hosts) - {"api.github.com"}:
                _finding(
                    errors,
                    f"{base}.network.hosts",
                    "unsafe_collector_hosts",
                    "only api.github.com is allowed",
                )
            methods = network.get("methods")
            if not _sequence_of_strings(methods) or set(methods) != {"GET"}:
                _finding(
                    errors,
                    f"{base}.network.methods",
                    "collector_not_read_only",
                    "must be exactly ['GET']",
                )
        else:
            if network.get("mode") != "none":
                _finding(errors, f"{base}.network.mode", "network_enabled", "must be none")
            if network.get("hosts") not in (None, []):
                _finding(errors, f"{base}.network.hosts", "network_hosts_present", "must be empty")
            if network.get("methods") not in (None, []):
                _finding(errors, f"{base}.network.methods", "network_methods_present", "must be empty")

        secrets = role.get("secrets")
        if not _sequence_of_strings(secrets):
            _finding(errors, f"{base}.secrets", "invalid_secrets", "must be an array of names")
        elif role_name == "collector":
            if set(secrets) - {"github_read_token"}:
                _finding(
                    errors,
                    f"{base}.secrets",
                    "collector_has_excess_secrets",
                    "collector may only receive github_read_token",
                )
        elif secrets:
            _finding(
                errors,
                f"{base}.secrets",
                "role_has_secrets",
                "provider calls must cross a narrow gateway; this role receives no secrets",
            )

        if role.get("external_write") is not False:
            _finding(
                errors,
                f"{base}.external_write",
                "external_write_enabled",
                "push, merge, comment, and other external writes are forbidden",
            )

        tools = role.get("tools")
        if not _sequence_of_strings(tools):
            _finding(errors, f"{base}.tools", "invalid_tools", "must be an array of tool names")
        elif set(tools) & FORBIDDEN_EXTERNAL_TOOLS:
            _finding(
                errors,
                f"{base}.tools",
                "forbidden_tool",
                f"contains {sorted(set(tools) & FORBIDDEN_EXTERNAL_TOOLS)}",
            )
        if role_name == "judge" and tools:
            _finding(errors, f"{base}.tools", "judge_has_tools", "judge must be pure text I/O")

        _validate_mounts(role_name, role.get("mounts"), errors)
        if role_name == "untrusted_code_runner":
            _validate_runner_controls(role, errors)

    return SecurityValidation(tuple(errors), tuple(warnings))


def assert_capability_manifest(manifest: Mapping[str, Any]) -> str:
    """Fail closed and return the immutable manifest hash when valid."""

    validation = validate_capability_manifest(manifest)
    validation.raise_for_errors()
    return capability_manifest_hash(manifest)
