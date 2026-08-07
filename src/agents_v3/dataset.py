"""Schema-backed event-aligned dataset gates.

Administrative dispositions and excluded/unknown records remain census facts;
only qualified, snapshot-aligned review events enter decision denominators.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .profiles import SchemaValidationError, validate_contract


DECISION_LABELS = frozenset({"APPROVE", "REQUEST_CHANGES"})
PRIMARY_SPLITS = frozenset({"train", "dev", "test"})


class DatasetProtocolError(ValueError):
    """Raised when a census or eligibility manifest fails closed."""


@dataclass(frozen=True)
class ManifestFinding:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ManifestValidation:
    errors: tuple[ManifestFinding, ...] = ()
    warnings: tuple[ManifestFinding, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise DatasetProtocolError(
                "; ".join(f"{item.path}: {item.code}: {item.message}" for item in self.errors)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.__dict__ for item in self.errors],
            "warnings": [item.__dict__ for item in self.warnings],
            "summary": dict(self.summary),
        }


def canonical_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one record against the sole published sample contract."""

    return validate_contract(sample, "sample.schema.json")


def _schema_findings(index: int, exc: SchemaValidationError) -> list[ManifestFinding]:
    return [
        ManifestFinding(
            f"samples[{index}]",
            "sample_schema",
            message.replace("$", f"samples[{index}]", 1),
        )
        for message in exc.errors
    ]


def validate_eligibility_manifest(manifest: Mapping[str, Any]) -> ManifestValidation:
    """Validate schema conformance, event/snapshot identity, and split grouping."""

    if not isinstance(manifest, Mapping):
        return ManifestValidation(
            errors=(ManifestFinding("$", "invalid_manifest", "must be an object"),)
        )
    errors: list[ManifestFinding] = []
    warnings: list[ManifestFinding] = []
    if manifest.get("schema_version") != "3.0.0":
        errors.append(
            ManifestFinding("schema_version", "invalid_schema_version", "must equal 3.0.0")
        )
    if not isinstance(manifest.get("dataset_id"), str) or not manifest["dataset_id"].strip():
        errors.append(ManifestFinding("dataset_id", "missing_dataset_id", "is required"))

    raw_samples = manifest.get("samples")
    if not isinstance(raw_samples, Sequence) or isinstance(raw_samples, (str, bytes)):
        errors.append(ManifestFinding("samples", "invalid_samples", "must be an array"))
        raw_samples = []

    samples: list[dict[str, Any]] = []
    sample_ids: dict[str, int] = {}
    event_ids: dict[str, int] = {}
    group_splits: defaultdict[str, set[str]] = defaultdict(set)
    pr_groups: defaultdict[str, set[str]] = defaultdict(set)

    for index, raw in enumerate(raw_samples):
        if not isinstance(raw, Mapping):
            errors.append(
                ManifestFinding(f"samples[{index}]", "invalid_sample", "must be an object")
            )
            continue
        try:
            sample = canonical_sample(raw)
        except SchemaValidationError as exc:
            errors.extend(_schema_findings(index, exc))
            continue
        samples.append(sample)
        sample_id = sample["sample_id"]
        if sample_id in sample_ids:
            errors.append(
                ManifestFinding(
                    f"samples[{index}].sample_id",
                    "duplicate_sample_id",
                    f"also used by samples[{sample_ids[sample_id]}]",
                )
            )
        else:
            sample_ids[sample_id] = index
        event_id = sample["label_event_id"]
        if event_id is not None:
            if event_id in event_ids:
                errors.append(
                    ManifestFinding(
                        f"samples[{index}].label_event_id",
                        "duplicate_label_event",
                        f"also used by samples[{event_ids[event_id]}]",
                    )
                )
            else:
                event_ids[event_id] = index

        split = sample["split"]
        group = sample["split_group_id"]
        if split in PRIMARY_SPLITS:
            group_splits[group].add(split)
            pr_groups[sample["pull_request_group_id"]].add(group)

        if sample["eligibility_cohort"] == "decision":
            if sample["snapshot_head_sha"] != sample["head_sha_at_event"]:
                errors.append(
                    ManifestFinding(
                        f"samples[{index}].snapshot_head_sha",
                        "snapshot_event_mismatch",
                        "must equal head_sha_at_event",
                    )
                )
            if sample["snapshot_base_sha"] != sample["base_sha_at_event"]:
                errors.append(
                    ManifestFinding(
                        f"samples[{index}].snapshot_base_sha",
                        "snapshot_event_mismatch",
                        "must equal base_sha_at_event",
                    )
                )

    for group, splits in sorted(group_splits.items()):
        if len(splits) > 1:
            errors.append(
                ManifestFinding(
                    "samples",
                    "related_group_split_leakage",
                    f"split_group_id {group!r} spans {sorted(splits)}",
                )
            )
    for pr_group, groups in sorted(pr_groups.items()):
        if len(groups) > 1:
            errors.append(
                ManifestFinding(
                    "samples",
                    "pr_group_fragmented",
                    f"pull_request_group_id {pr_group!r} maps to multiple split groups",
                )
            )

    cohorts = Counter(sample["eligibility_cohort"] for sample in samples)
    rationale_count = sum(bool(sample["rationale_eligible"]) for sample in samples)
    observed = {
        "eligible": cohorts["decision"],
        "rationale": rationale_count,
        "administrative": cohorts["administrative"],
        "excluded": cohorts["excluded"],
        "unknown": cohorts["unknown"],
    }
    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        errors.append(ManifestFinding("counts", "invalid_counts", "must be an object"))
    else:
        for name, actual in observed.items():
            if counts.get(name) != actual:
                errors.append(
                    ManifestFinding(
                        f"counts.{name}",
                        "count_mismatch",
                        f"declares {counts.get(name)!r}, observed {actual}",
                    )
                )
        discovered = counts.get("discovered")
        if (
            isinstance(discovered, bool)
            or not isinstance(discovered, int)
            or discovered < len(raw_samples)
        ):
            errors.append(
                ManifestFinding(
                    "counts.discovered",
                    "invalid_discovered_count",
                    "must be an integer at least as large as samples",
                )
            )

    summary = {
        "manifest_samples": len(samples),
        **observed,
        "effective_split_groups": len(group_splits),
        "by_cohort": dict(sorted(cohorts.items())),
    }
    return ManifestValidation(tuple(errors), tuple(warnings), summary)


def primary_sample_ids(manifest: Mapping[str, Any], *, split: str | None = None) -> tuple[str, ...]:
    validation = validate_eligibility_manifest(manifest)
    validation.raise_for_errors()
    if split is not None and split not in PRIMARY_SPLITS:
        raise DatasetProtocolError("split must be train, dev, or test")
    return tuple(
        sorted(
            str(sample["sample_id"])
            for sample in manifest["samples"]
            if sample["eligibility_cohort"] == "decision"
            and (split is None or sample["split"] == split)
        )
    )


def assert_no_group_leakage(samples: Iterable[Mapping[str, Any]]) -> None:
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for raw in samples:
        sample = canonical_sample(raw)
        if sample["split"] in PRIMARY_SPLITS:
            groups[sample["split_group_id"]].add(sample["split"])
    leaking = {group: sorted(splits) for group, splits in groups.items() if len(splits) > 1}
    if leaking:
        raise DatasetProtocolError(f"split groups cross dataset splits: {leaking}")

