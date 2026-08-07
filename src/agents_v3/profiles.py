"""Schema-backed canonical contracts and immutable evaluation profiles.

The JSON files under ``schemas/`` are the published contracts.  This module
implements the deliberately small Draft 2020-12 subset used by those files so
the harness can fail closed without relying on an optional validation package.
It does not keep a second, hand-written list of profile fields.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from .resources import asset_root
from .schema_gate import SchemaValidationError as GateValidationError, assert_schema


SCHEMA_NAMES = frozenset(
    {
        "sample.schema.json",
        "artifact_manifest.schema.json",
        "ledger_event.schema.json",
        "evaluation_profile.schema.json",
        "evaluation_result.schema.json",
    }
)


class SchemaValidationError(ValueError):
    """Raised with all deterministic contract violations."""

    def __init__(self, schema_name: str, errors: Sequence[str]):
        self.schema_name = schema_name
        self.errors = tuple(errors)
        super().__init__(f"{schema_name}: " + "; ".join(self.errors))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def schema_directory() -> Path:
    return asset_root() / "schemas"


def load_schema(schema_name: str) -> dict[str, Any]:
    if schema_name not in SCHEMA_NAMES:
        raise ValueError(f"schema is not an allowlisted V3 contract: {schema_name!r}")
    path = schema_directory() / schema_name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load published schema {schema_name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"published schema {schema_name} is not an object")
    return value


def _resolve_ref(root: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"only local schema references are supported: {reference!r}")
    current: Any = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or token not in current:
            raise ValueError(f"unresolvable schema reference: {reference!r}")
        current = current[token]
    if not isinstance(current, Mapping):
        raise ValueError(f"schema reference is not an object: {reference!r}")
    return current


def _is_type(instance: Any, name: str) -> bool:
    if name == "null":
        return instance is None
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if name == "string":
        return isinstance(instance, str)
    if name == "array":
        return isinstance(instance, Sequence) and not isinstance(instance, (str, bytes, bytearray))
    if name == "object":
        return isinstance(instance, Mapping)
    raise ValueError(f"unsupported schema type: {name!r}")


def _valid_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validate_node(
    instance: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if "$ref" in schema:
        _validate_node(instance, _resolve_ref(root, str(schema["$ref"])), root, path, errors)
        return

    if "allOf" in schema:
        for branch in schema["allOf"]:
            _validate_node(instance, branch, root, path, errors)
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not any(_branch_valid(instance, branch, root, path) for branch in branches):
            errors.append(f"{path}: does not satisfy any allowed schema branch")
            return
    if "oneOf" in schema:
        matches = sum(_branch_valid(instance, branch, root, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: must satisfy exactly one schema branch (matched {matches})")
            return

    if "if" in schema:
        branch = schema.get("then") if _branch_valid(instance, schema["if"], root, path) else schema.get("else")
        if branch is not None:
            _validate_node(instance, branch, root, path, errors)

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")

    declared_type = schema.get("type")
    if declared_type is not None:
        names = [declared_type] if isinstance(declared_type, str) else list(declared_type)
        if not any(_is_type(instance, name) for name in names):
            errors.append(f"{path}: must have type {names!r}")
            return

    if isinstance(instance, Mapping):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}.{key}: required property is missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property is forbidden")
        for key, subschema in properties.items():
            if key in instance:
                _validate_node(instance[key], subschema, root, f"{path}.{key}", errors)
        if "minProperties" in schema and len(instance) < int(schema["minProperties"]):
            errors.append(f"{path}: must contain at least {schema['minProperties']} properties")

    if isinstance(instance, Sequence) and not isinstance(instance, (str, bytes, bytearray)):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append(f"{path}: must contain at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append(f"{path}: must contain no more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [canonical_bytes(item) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                _validate_node(item, schema["items"], root, f"{path}[{index}]", errors)

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
            errors.append(f"{path}: does not match required pattern")
        if schema.get("format") == "date-time" and not _valid_datetime(instance):
            errors.append(f"{path}: must be an RFC 3339 timestamp with timezone")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")


def _branch_valid(instance: Any, branch: Mapping[str, Any], root: Mapping[str, Any], path: str) -> bool:
    branch_errors: list[str] = []
    _validate_node(instance, branch, root, path, branch_errors)
    return not branch_errors


def validate_contract(instance: Any, schema_name: str) -> Any:
    """Validate and return a canonical JSON copy of an instance."""

    try:
        canonical = json.loads(canonical_bytes(instance))
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(schema_name, (f"$: not canonical JSON: {exc}",)) from exc
    schema = load_schema(schema_name)
    try:
        assert_schema(canonical, schema)
    except GateValidationError as exc:
        errors = tuple(
            f"{finding.path}: {finding.keyword}: {finding.message}"
            for finding in exc.findings
        )
        raise SchemaValidationError(schema_name, errors) from exc
    return canonical


def load_profile(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        value: Any = dict(source)
    else:
        path = Path(source)
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"cannot load evaluation profile: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("evaluation profile must be an object")
    return validate_contract(value, "evaluation_profile.schema.json")


def profile_hash(profile: Mapping[str, Any]) -> str:
    """Hash a profile with its self-referential hash slot normalized to null."""

    normalized = deepcopy(dict(profile))
    normalized["profile_sha256"] = None
    return canonical_sha256(normalized)


def validate_frozen_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    canonical = load_profile(profile)
    if canonical.get("frozen") is not True:
        raise ValueError("evaluation profile must be frozen before final test")
    if not isinstance(canonical.get("frozen_at"), str):
        raise ValueError("frozen profile requires frozen_at")
    expected = profile_hash(canonical)
    if canonical.get("profile_sha256") != expected:
        raise ValueError("profile_sha256 does not match canonical profile contents")
    return canonical


def validate_resolved_frozen_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Require a frozen profile whose content references are not templates.

    JSON Schema can prove shape, and ``validate_frozen_profile`` can prove
    immutability, but neither makes an all-zero digest or ``REPLACE_*`` token a
    real artifact.  Final-test entry points use this stricter semantic gate.
    """

    canonical = validate_frozen_profile(profile)
    unresolved: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if key.endswith("_sha256") and item == "0" * 64:
                    unresolved.append(child)
                if key == "harness_commit" and item == "0" * 40:
                    unresolved.append(child)
                if key == "sandbox_image_digest" and item == "sha256:" + "0" * 64:
                    unresolved.append(child)
                visit(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif isinstance(value, str):
            upper = value.upper()
            if "REPLACE" in upper or upper.startswith("EXAMPLE-"):
                unresolved.append(path)

    visit(canonical, "")
    if unresolved:
        raise ValueError(
            "frozen profile contains unresolved template values: "
            + ", ".join(sorted(set(unresolved)))
        )
    return canonical


def freeze_profile(profile: Mapping[str, Any], *, frozen_at: str | None = None) -> dict[str, Any]:
    """Return a new atomically hashable profile; never mutate the caller value."""

    # Validate the mutable source first.  The hash slot is intentionally null
    # there; after the frozen fields are populated we hash that normalized
    # representation and only then validate the frozen branch of the schema.
    candidate = load_profile(profile)
    candidate["frozen"] = True
    candidate["frozen_at"] = frozen_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    candidate["profile_sha256"] = None
    candidate["profile_sha256"] = profile_hash(candidate)
    return validate_frozen_profile(candidate)
