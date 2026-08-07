"""Small deterministic validator for the JSON-Schema subset shipped by V3.

The project deliberately avoids a network-fetched runtime dependency. Unknown
keywords are annotations, while every assertion keyword used under `schemas/`
is implemented here. External `$ref` values are rejected; local `$defs` refs
are resolved against the exact schema bytes loaded by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SchemaFinding:
    path: str
    keyword: str
    message: str


class SchemaValidationError(ValueError):
    def __init__(self, findings: Sequence[SchemaFinding]):
        self.findings = tuple(findings)
        super().__init__(
            "; ".join(f"{item.path}: {item.keyword}: {item.message}" for item in findings)
        )


def _json_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _type_matches(instance: Any, expected: str) -> bool:
    return {
        "null": instance is None,
        "boolean": isinstance(instance, bool),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
        "string": isinstance(instance, str),
        "array": isinstance(instance, list),
        "object": isinstance(instance, Mapping),
    }.get(expected, False)


def _resolve_ref(root: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ValueError(f"external or non-pointer $ref is forbidden: {reference}")
    value: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or part not in value:
            raise ValueError(f"unresolvable local $ref: {reference}")
        value = value[part]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_instance(instance: Any, schema: Mapping[str, Any]) -> tuple[SchemaFinding, ...]:
    findings: list[SchemaFinding] = []

    def add(path: str, keyword: str, message: str) -> None:
        findings.append(SchemaFinding(path, keyword, message))

    def matches(value: Any, subschema: Any) -> bool:
        before = len(findings)
        check(value, subschema, "$.__probe__")
        passed = len(findings) == before
        del findings[before:]
        return passed

    def check(value: Any, current: Any, path: str) -> None:
        if current is True:
            return
        if current is False:
            add(path, "falseSchema", "value is forbidden")
            return
        if not isinstance(current, Mapping):
            add(path, "schema", "subschema must be an object or boolean")
            return
        if "$ref" in current:
            try:
                target = _resolve_ref(schema, str(current["$ref"]))
            except ValueError as exc:
                add(path, "$ref", str(exc))
            else:
                check(value, target, path)

        for subschema in current.get("allOf", []):
            check(value, subschema, path)
        if "anyOf" in current and not any(matches(value, item) for item in current["anyOf"]):
            add(path, "anyOf", "no branch matched")
        if "oneOf" in current:
            count = sum(matches(value, item) for item in current["oneOf"])
            if count != 1:
                add(path, "oneOf", f"expected exactly one matching branch, found {count}")
        if "not" in current and matches(value, current["not"]):
            add(path, "not", "forbidden branch matched")
        if "if" in current:
            branch = "then" if matches(value, current["if"]) else "else"
            if branch in current:
                check(value, current[branch], path)

        expected_type = current.get("type")
        if expected_type is not None:
            expected_values = [expected_type] if isinstance(expected_type, str) else list(expected_type)
            if not any(_type_matches(value, item) for item in expected_values):
                add(path, "type", f"expected {expected_values}, got {type(value).__name__}")
                return
        if "const" in current and not _json_equal(value, current["const"]):
            add(path, "const", f"expected {current['const']!r}")
        if "enum" in current and not any(_json_equal(value, option) for option in current["enum"]):
            add(path, "enum", f"value is not in {current['enum']!r}")

        if isinstance(value, Mapping):
            required = current.get("required", [])
            for key in required:
                if key not in value:
                    add(path, "required", f"missing property {key!r}")
            properties = current.get("properties", {})
            if isinstance(properties, Mapping):
                for key, item in value.items():
                    child_path = f"{path}.{key}"
                    if key in properties:
                        check(item, properties[key], child_path)
                    elif current.get("additionalProperties") is False:
                        add(child_path, "additionalProperties", "property is not allowed")
                    elif isinstance(current.get("additionalProperties"), Mapping):
                        check(item, current["additionalProperties"], child_path)
            if "minProperties" in current and len(value) < int(current["minProperties"]):
                add(path, "minProperties", "too few properties")
            if "maxProperties" in current and len(value) > int(current["maxProperties"]):
                add(path, "maxProperties", "too many properties")

        if isinstance(value, list):
            if "minItems" in current and len(value) < int(current["minItems"]):
                add(path, "minItems", "too few items")
            if "maxItems" in current and len(value) > int(current["maxItems"]):
                add(path, "maxItems", "too many items")
            if current.get("uniqueItems") is True:
                encoded = [_canonical(item) for item in value]
                if len(encoded) != len(set(encoded)):
                    add(path, "uniqueItems", "items are not unique")
            if "items" in current:
                for index, item in enumerate(value):
                    check(item, current["items"], f"{path}[{index}]")
            if "contains" in current:
                count = sum(matches(item, current["contains"]) for item in value)
                minimum = int(current.get("minContains", 1))
                maximum = current.get("maxContains")
                if count < minimum or (maximum is not None and count > int(maximum)):
                    add(path, "contains", f"matching item count {count} is outside bounds")

        if isinstance(value, str):
            if "minLength" in current and len(value) < int(current["minLength"]):
                add(path, "minLength", "string is too short")
            if "maxLength" in current and len(value) > int(current["maxLength"]):
                add(path, "maxLength", "string is too long")
            if "pattern" in current and re.search(str(current["pattern"]), value) is None:
                add(path, "pattern", f"does not match {current['pattern']!r}")
            if current.get("format") == "date-time":
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    parsed = None
                if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
                    add(path, "format", "must be an ISO-8601 date-time with timezone")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in current and value < current["minimum"]:
                add(path, "minimum", f"must be >= {current['minimum']}")
            if "maximum" in current and value > current["maximum"]:
                add(path, "maximum", f"must be <= {current['maximum']}")
            if "exclusiveMinimum" in current and value <= current["exclusiveMinimum"]:
                add(path, "exclusiveMinimum", f"must be > {current['exclusiveMinimum']}")
            if "exclusiveMaximum" in current and value >= current["exclusiveMaximum"]:
                add(path, "exclusiveMaximum", f"must be < {current['exclusiveMaximum']}")

    check(instance, schema, "$")
    return tuple(findings)


def assert_schema(instance: Any, schema: Mapping[str, Any]) -> None:
    findings = validate_instance(instance, schema)
    if findings:
        raise SchemaValidationError(findings)


def load_published_schema(name: str) -> dict[str, Any]:
    """Load one distribution-controlled schema by basename."""

    from .resources import schema_path

    path = schema_path(name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaValidationError(
            (SchemaFinding("$", "publishedSchema", f"cannot load {Path(path).name}: {exc}"),)
        ) from exc
    if not isinstance(value, dict):
        raise SchemaValidationError(
            (SchemaFinding("$", "publishedSchema", f"{name} is not an object"),)
        )
    return value


def assert_published_schema(instance: Any, name: str) -> Any:
    """Canonicalize JSON, reject NaN/Infinity, and apply a shipped schema."""

    try:
        canonical = json.loads(
            json.dumps(
                instance,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(
            (SchemaFinding("$", "canonicalJSON", str(exc)),)
        ) from exc
    assert_schema(canonical, load_published_schema(name))
    return canonical
