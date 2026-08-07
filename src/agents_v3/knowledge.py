"""Knowledge-cutoff enforcement and deterministic patch grouping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from .schema_gate import SchemaValidationError, assert_published_schema


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_KINDS = frozenset(
    {"repository_file", "repository_config", "contributing", "direct_commit"}
)
_KINDS = _REPO_KINDS | {"third_party", "derived_annex"}
_SPLITS = frozenset({"train", "dev", "test"})


class KnowledgeProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeFinding:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class KnowledgeValidation:
    errors: tuple[KnowledgeFinding, ...]
    warnings: tuple[KnowledgeFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise KnowledgeProtocolError(
                "; ".join(f"{item.path}: {item.code}: {item.message}" for item in self.errors)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.__dict__ for item in self.errors],
            "warnings": [item.__dict__ for item in self.warnings],
        }


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def knowledge_manifest_hash(manifest: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def validate_knowledge_manifest(manifest: Mapping[str, Any]) -> KnowledgeValidation:
    errors: list[KnowledgeFinding] = []
    warnings: list[KnowledgeFinding] = []
    if not isinstance(manifest, Mapping):
        return KnowledgeValidation((KnowledgeFinding("$", "invalid_manifest", "must be an object"),), ())
    try:
        manifest = assert_published_schema(manifest, "knowledge_manifest.schema.json")
    except SchemaValidationError as exc:
        errors.extend(
            KnowledgeFinding(item.path, f"schema_{item.keyword}", item.message)
            for item in exc.findings
        )
    if manifest.get("schema_version") != "3.0.0":
        errors.append(KnowledgeFinding("schema_version", "invalid_version", "must be 3.0.0"))
    cutoff = _time(manifest.get("knowledge_cutoff"))
    if cutoff is None:
        errors.append(KnowledgeFinding("knowledge_cutoff", "invalid_cutoff", "timezone is required"))

    sources = manifest.get("sources")
    source_map: dict[str, Mapping[str, Any]] = {}
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        errors.append(KnowledgeFinding("sources", "invalid_sources", "must be an array"))
        sources = []
    for index, source in enumerate(sources):
        base = f"sources[{index}]"
        if not isinstance(source, Mapping):
            errors.append(KnowledgeFinding(base, "invalid_source", "must be an object"))
            continue
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(KnowledgeFinding(f"{base}.source_id", "missing_id", "is required"))
            continue
        if source_id in source_map:
            errors.append(KnowledgeFinding(f"{base}.source_id", "duplicate_id", source_id))
            continue
        source_map[source_id] = source
        kind = source.get("kind")
        if kind not in _KINDS:
            errors.append(KnowledgeFinding(f"{base}.kind", "invalid_kind", str(kind)))
        if not isinstance(source.get("revision"), str) or not source.get("revision"):
            errors.append(KnowledgeFinding(f"{base}.revision", "unpinned_revision", "is required"))
        digest = source.get("sha256")
        if not isinstance(digest, str) or _HASH_RE.fullmatch(digest) is None:
            errors.append(KnowledgeFinding(f"{base}.sha256", "invalid_hash", "full lowercase SHA-256 required"))
        published = _time(source.get("published_at"))
        available = _time(source.get("available_at"))
        if published is None or available is None:
            errors.append(KnowledgeFinding(base, "invalid_source_time", "published_at and available_at need timezones"))
        elif cutoff is not None and source.get("candidate_visible") is True and (
            published > cutoff or available > cutoff
        ):
            errors.append(
                KnowledgeFinding(
                    base,
                    "post_cutoff_source",
                    "candidate-visible source was published or available after the cutoff",
                )
            )
        if kind in _REPO_KINDS:
            commit = source.get("commit_sha")
            if not isinstance(commit, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
                errors.append(KnowledgeFinding(f"{base}.commit_sha", "missing_commit", "repository inputs require a full commit"))

    for field in ("candidate_source_ids", "baseline_source_ids"):
        values = manifest.get(field)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            errors.append(KnowledgeFinding(field, "invalid_source_refs", "must be an array"))
            continue
        missing = sorted(set(values) - set(source_map))
        if missing:
            errors.append(KnowledgeFinding(field, "unknown_source_refs", str(missing)))
        invisible = sorted(
            source_id
            for source_id in values
            if source_id in source_map and source_map[source_id].get("candidate_visible") is not True
        )
        if invisible:
            errors.append(KnowledgeFinding(field, "nonvisible_source_refs", str(invisible)))

    groups = manifest.get("sample_groups")
    seen_samples: dict[str, tuple[str, str]] = {}
    seen_groups: set[str] = set()
    if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
        errors.append(KnowledgeFinding("sample_groups", "invalid_groups", "must be an array"))
        groups = []
    for index, group in enumerate(groups):
        base = f"sample_groups[{index}]"
        if not isinstance(group, Mapping):
            errors.append(KnowledgeFinding(base, "invalid_group", "must be an object"))
            continue
        group_id = group.get("group_id")
        split = group.get("split")
        if not isinstance(group_id, str) or not group_id:
            errors.append(KnowledgeFinding(f"{base}.group_id", "missing_group_id", "is required"))
            continue
        if group_id in seen_groups:
            errors.append(KnowledgeFinding(f"{base}.group_id", "duplicate_group", group_id))
        seen_groups.add(group_id)
        if split not in _SPLITS:
            errors.append(KnowledgeFinding(f"{base}.split", "invalid_split", str(split)))
        event_at = _time(group.get("max_label_event_at"))
        if event_at is None:
            errors.append(KnowledgeFinding(f"{base}.max_label_event_at", "invalid_event_time", "timezone required"))
        sample_ids = group.get("sample_ids")
        if not isinstance(sample_ids, Sequence) or isinstance(sample_ids, (str, bytes)) or not sample_ids:
            errors.append(KnowledgeFinding(f"{base}.sample_ids", "invalid_samples", "must be non-empty"))
            continue
        for sample_id in sample_ids:
            if sample_id in seen_samples:
                previous_group, previous_split = seen_samples[str(sample_id)]
                errors.append(
                    KnowledgeFinding(
                        f"{base}.sample_ids",
                        "sample_group_leakage",
                        f"{sample_id!r} already belongs to {previous_group}/{previous_split}",
                    )
                )
            else:
                seen_samples[str(sample_id)] = (group_id, str(split))

    risk = manifest.get("memory_contamination_risk")
    if risk not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}:
        errors.append(KnowledgeFinding("memory_contamination_risk", "missing_risk", "must be explicit"))
    if risk in {"HIGH", "UNKNOWN"}:
        warnings.append(KnowledgeFinding("memory_contamination_risk", "contamination_risk", "must be disclosed in the final report"))
    try:
        expected_manifest_hash = knowledge_manifest_hash(manifest)
    except (TypeError, ValueError) as exc:
        errors.append(KnowledgeFinding("$", "noncanonical_manifest", str(exc)))
    else:
        if manifest.get("manifest_sha256") not in (None, expected_manifest_hash):
            errors.append(KnowledgeFinding("manifest_sha256", "hash_mismatch", "does not match canonical content"))
    return KnowledgeValidation(tuple(errors), tuple(warnings))


def _patch_tokens(diff: str) -> frozenset[str]:
    # Ignore diff headers/line numbers and group on normalized code-like tokens.
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\w\s]", diff.lower())
    return frozenset(tokens)


def group_near_duplicate_patches(
    patches: Sequence[Mapping[str, Any]], *, threshold: float = 0.90
) -> dict[str, str]:
    """Return deterministic union-find group IDs from explicit links/Jaccard."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    ids: list[str] = []
    tokens: dict[str, frozenset[str]] = {}
    explicit: dict[str, str | None] = {}
    for index, item in enumerate(patches):
        sample_id = item.get("sample_id")
        diff = item.get("diff")
        if not isinstance(sample_id, str) or not sample_id or sample_id in tokens:
            raise ValueError(f"patch[{index}] needs a unique sample_id")
        if not isinstance(diff, str):
            raise ValueError(f"patch[{index}].diff must be text")
        ids.append(sample_id)
        tokens[sample_id] = _patch_tokens(diff)
        key = item.get("explicit_group_key")
        explicit[sample_id] = str(key) if key not in (None, "") else None

    parent = {sample_id: sample_id for sample_id in ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            first, second = sorted((root_left, root_right))
            parent[second] = first

    for left_index, left in enumerate(ids):
        for right in ids[left_index + 1 :]:
            linked = explicit[left] is not None and explicit[left] == explicit[right]
            union_tokens = tokens[left] | tokens[right]
            similarity = len(tokens[left] & tokens[right]) / len(union_tokens) if union_tokens else 1.0
            if linked or similarity >= threshold:
                union(left, right)
    members: dict[str, list[str]] = {}
    for sample_id in ids:
        members.setdefault(find(sample_id), []).append(sample_id)
    result: dict[str, str] = {}
    for values in members.values():
        stable = sha256("\x00".join(sorted(values)).encode("utf-8")).hexdigest()[:16]
        for sample_id in values:
            result[sample_id] = f"patch-group-{stable}"
    return result
