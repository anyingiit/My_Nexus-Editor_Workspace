"""Cluster-paired three-arm statistics and preregistered scoring gates."""

from __future__ import annotations

from dataclasses import dataclass
from math import comb, sqrt
from random import Random
from statistics import NormalDist
from typing import Any, Mapping, Sequence


LABELS = frozenset({"APPROVE", "REQUEST_CHANGES"})


class EvaluationProtocolError(ValueError):
    """Raised when data cannot support the frozen analysis."""


@dataclass(frozen=True)
class McNemarResult:
    candidate_only_correct: int
    baseline_only_correct: int
    discordant_pairs: int
    exact_two_sided_p: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class BootstrapDifference:
    estimate: float
    lower: float
    upper: float
    confidence: float
    resamples: int
    seed: int
    effective_groups: int
    cluster_resampled: bool

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def percentage_points_to_proportion(value: float | int) -> float:
    """Explicitly adapt preregistered percentage points to [0, 1] effects."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("percentage-point value must be numeric")
    if not 0.0 <= float(value) <= 100.0:
        raise ValueError("percentage-point value must be between 0 and 100")
    return float(value) / 100.0


def wilson_interval(successes: int, total: int, *, confidence: float = 0.95) -> tuple[float, float]:
    if (
        isinstance(successes, bool)
        or isinstance(total, bool)
        or not isinstance(successes, int)
        or not isinstance(total, int)
    ):
        raise ValueError("successes and total must be integers")
    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("require 0 <= successes <= total and total > 0")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    margin = z * sqrt(
        proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def paired_mcnemar_exact(
    candidate_correct: Sequence[bool], baseline_correct: Sequence[bool]
) -> McNemarResult:
    if len(candidate_correct) != len(baseline_correct) or not candidate_correct:
        raise ValueError("paired correctness vectors must be non-empty and equal length")
    candidate_only = baseline_only = 0
    for candidate, baseline in zip(candidate_correct, baseline_correct, strict=True):
        if not isinstance(candidate, bool) or not isinstance(baseline, bool):
            raise ValueError("correctness vectors must contain booleans")
        candidate_only += int(candidate and not baseline)
        baseline_only += int(baseline and not candidate)
    discordant = candidate_only + baseline_only
    if not discordant:
        p_value = 1.0
    else:
        tail = sum(comb(discordant, k) for k in range(min(candidate_only, baseline_only) + 1))
        p_value = min(1.0, 2.0 * tail / (2**discordant))
    return McNemarResult(candidate_only, baseline_only, discordant, p_value)


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot take quantile of empty values")
    if probability <= 0:
        return float(values[0])
    if probability >= 1:
        return float(values[-1])
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(len(values) - 1, lower + 1)
    fraction = position - lower
    return float(values[lower] + (values[upper] - values[lower]) * fraction)


def paired_bootstrap_difference(
    candidate_scores: Sequence[float],
    baseline_scores: Sequence[float],
    *,
    group_ids: Sequence[str] | None = None,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
) -> BootstrapDifference:
    """Paired percentile CI, resampling whole split groups when provided."""

    if len(candidate_scores) != len(baseline_scores) or not candidate_scores:
        raise ValueError("paired score vectors must be non-empty and equal length")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or resamples < 100:
        raise ValueError("resamples must be an integer of at least 100")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    differences = [
        float(candidate) - float(baseline)
        for candidate, baseline in zip(candidate_scores, baseline_scores, strict=True)
    ]
    if any(value != value for value in differences):
        raise ValueError("scores must not contain NaN")
    estimate = sum(differences) / len(differences)
    if group_ids is None:
        groups = [str(index) for index in range(len(differences))]
        cluster_resampled = False
    else:
        if len(group_ids) != len(differences) or any(not str(group) for group in group_ids):
            raise ValueError("group_ids must align one-for-one with score vectors")
        groups = [str(group) for group in group_ids]
        cluster_resampled = True
    members: dict[str, list[int]] = {}
    for index, group in enumerate(groups):
        members.setdefault(group, []).append(index)
    group_names = sorted(members)
    generator = Random(seed)
    bootstrap: list[float] = []
    for _ in range(resamples):
        selected = [group_names[generator.randrange(len(group_names))] for _ in group_names]
        indices = [index for group in selected for index in members[group]]
        bootstrap.append(sum(differences[index] for index in indices) / len(indices))
    bootstrap.sort()
    tail = (1.0 - confidence) / 2.0
    return BootstrapDifference(
        estimate=estimate,
        lower=_quantile(bootstrap, tail),
        upper=_quantile(bootstrap, 1.0 - tail),
        confidence=confidence,
        resamples=resamples,
        seed=seed,
        effective_groups=len(group_names),
        cluster_resampled=cluster_resampled,
    )


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    if not p_values:
        raise ValueError("at least one p-value is required")
    ordered = sorted((float(value), str(name)) for name, value in p_values.items())
    if any(not 0.0 <= value <= 1.0 for value, _ in ordered):
        raise ValueError("p-values must be in [0, 1]")
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (value, name) in enumerate(ordered):
        running = max(running, min(1.0, value * (len(ordered) - rank)))
        adjusted[name] = running
    return adjusted


def _normalize_truth_records(
    truth: Mapping[str, str] | Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    if isinstance(truth, Mapping):
        records = {
            str(sample_id): {
                "label": str(label),
                "split_group_id": str(sample_id),
                "aggregate_ci_status": "CI_UNKNOWN",
            }
            for sample_id, label in truth.items()
        }
    else:
        records: dict[str, dict[str, str]] = {}
        for index, raw in enumerate(truth):
            if not isinstance(raw, Mapping):
                raise EvaluationProtocolError(f"truth[{index}] must be an object")
            sample_id = str(raw.get("sample_id", ""))
            label = str(raw.get("label", raw.get("label_event_type", "")))
            group = str(raw.get("split_group_id", ""))
            ci = str(raw.get("aggregate_ci_status", "CI_UNKNOWN"))
            if not sample_id or sample_id in records:
                raise EvaluationProtocolError(f"truth[{index}] has missing/duplicate sample_id")
            if not group:
                raise EvaluationProtocolError(f"truth[{index}] requires split_group_id")
            if ci not in {"CI_PASS", "CI_FAIL", "CI_UNKNOWN"}:
                raise EvaluationProtocolError(f"truth[{index}] has invalid aggregate_ci_status")
            records[sample_id] = {
                "label": label,
                "split_group_id": group,
                "aggregate_ci_status": ci,
            }
    if not records:
        raise EvaluationProtocolError("truth is empty")
    invalid = {key: value["label"] for key, value in records.items() if value["label"] not in LABELS}
    if invalid:
        raise EvaluationProtocolError(f"truth contains non-decision labels: {invalid}")
    return records


def _normalize_arm(records: Sequence[Mapping[str, Any]], *, arm_name: str) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise EvaluationProtocolError(f"arms.{arm_name}[{index}] must be an object")
        sample_id = str(record.get("sample_id", ""))
        prediction = str(record.get("prediction", record.get("decision", "")))
        if not sample_id or sample_id in normalized:
            raise EvaluationProtocolError(f"arms.{arm_name}[{index}] has missing/duplicate sample_id")
        if prediction not in LABELS:
            raise EvaluationProtocolError(f"arms.{arm_name}[{index}] has invalid decision")
        reason_codes = record.get("reason_codes")
        if reason_codes is None:
            reasons = record.get("reasons", [])
            reason_codes = [item.get("reason_id") for item in reasons] if isinstance(reasons, Sequence) else []
        if not isinstance(reason_codes, Sequence) or isinstance(reason_codes, (str, bytes)):
            raise EvaluationProtocolError(f"arms.{arm_name}[{index}].reason_codes must be an array")
        if any(not isinstance(code, str) or not code for code in reason_codes):
            raise EvaluationProtocolError(f"arms.{arm_name}[{index}] has invalid reason codes")
        normalized[sample_id] = {
            "prediction": prediction,
            "reason_codes": tuple(dict.fromkeys(reason_codes)),
        }
    return normalized


def validate_three_arm_results(
    truth: Mapping[str, str] | Sequence[Mapping[str, Any]],
    arms: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    candidate_arm: str = "CANDIDATE",
) -> tuple[dict[str, str], dict[str, dict[str, dict[str, Any]]]]:
    truth_records = _normalize_truth_records(truth)
    if not isinstance(arms, Mapping) or len(arms) != 3:
        raise EvaluationProtocolError("exactly three arms are required")
    if candidate_arm not in arms and candidate_arm == "CANDIDATE" and "candidate" in arms:
        candidate_arm = "candidate"
    if candidate_arm not in arms:
        raise EvaluationProtocolError(f"candidate arm {candidate_arm!r} is missing")
    normalized = {str(name): _normalize_arm(value, arm_name=str(name)) for name, value in arms.items()}
    expected = set(truth_records)
    for name, records in normalized.items():
        missing = sorted(expected - set(records))
        extra = sorted(set(records) - expected)
        if missing or extra:
            raise EvaluationProtocolError(
                f"arm {name!r} is not paired to truth; missing={missing}, extra={extra}"
            )
    return {key: value["label"] for key, value in truth_records.items()}, normalized


def _arm_metrics(
    truth_records: Mapping[str, Mapping[str, str]], arm: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[bool]]:
    sample_ids = sorted(truth_records)
    correct = [arm[item]["prediction"] == truth_records[item]["label"] for item in sample_ids]
    reject = [item for item in sample_ids if truth_records[item]["label"] == "REQUEST_CHANGES"]
    approve = [item for item in sample_ids if truth_records[item]["label"] == "APPROVE"]
    reject_correct = sum(arm[item]["prediction"] == "REQUEST_CHANGES" for item in reject)
    approve_correct = sum(arm[item]["prediction"] == "APPROVE" for item in approve)
    reject_recall = reject_correct / len(reject) if reject else None
    approve_recall = approve_correct / len(approve) if approve else None
    balanced = (
        (reject_recall + approve_recall) / 2.0
        if reject_recall is not None and approve_recall is not None
        else None
    )
    correct_count = sum(correct)
    return {
        "n": len(sample_ids),
        "effective_groups": len({truth_records[item]["split_group_id"] for item in sample_ids}),
        "correct": correct_count,
        "accuracy": correct_count / len(sample_ids),
        "accuracy_wilson_95": list(wilson_interval(correct_count, len(sample_ids))),
        "reject_n": len(reject),
        "reject_recall": reject_recall,
        "approve_n": len(approve),
        "approve_recall": approve_recall,
        "balanced_accuracy": balanced,
    }, correct


def observed_review_agreement_precision(
    predicted: Mapping[str, Sequence[str]], observed_review: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    hits = proposed = observed_total = 0
    for sample_id, values in predicted.items():
        predicted_set = set(values)
        observed_set = set(observed_review.get(sample_id, ()))
        hits += len(predicted_set & observed_set)
        proposed += len(predicted_set)
        observed_total += len(observed_set)
    return {
        "name": "observed_review_agreement_precision",
        "matched_observed_reasons": hits,
        "predicted_reasons": proposed,
        "precision": hits / proposed if proposed else None,
        "observed_reason_coverage": hits / observed_total if observed_total else None,
        "hallucination_rate": None,
        "note": "Unmatched reasons require independent adjudication.",
    }


def stability_summary(repeated_predictions: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    if not repeated_predictions:
        raise ValueError("repeated_predictions must not be empty")
    repeat_counts = {len(values) for values in repeated_predictions.values()}
    if len(repeat_counts) != 1 or next(iter(repeat_counts)) < 2:
        raise ValueError("all samples require the same number of at least two repetitions")
    repetitions = next(iter(repeat_counts))
    if any(value not in LABELS for values in repeated_predictions.values() for value in values):
        raise ValueError("repeated predictions contain an invalid label")
    sample_count = len(repeated_predictions)
    complete = sum(len(set(values)) == 1 for values in repeated_predictions.values()) / sample_count
    agreeing_pairs = 0
    category_totals = {label: 0 for label in LABELS}
    observed_total = 0.0
    for values in repeated_predictions.values():
        counts = {label: values.count(label) for label in LABELS}
        for label, count in counts.items():
            category_totals[label] += count
            agreeing_pairs += comb(count, 2)
        observed_total += sum(count * (count - 1) for count in counts.values()) / (
            repetitions * (repetitions - 1)
        )
    pairwise = agreeing_pairs / (sample_count * comb(repetitions, 2))
    observed = observed_total / sample_count
    ratings = sample_count * repetitions
    expected = sum((count / ratings) ** 2 for count in category_totals.values())
    return {
        "metric_family": "stability_repeatability_only",
        "sample_count": sample_count,
        "repetitions": repetitions,
        "complete_agreement_rate": complete,
        "pairwise_agreement": pairwise,
        "fleiss_kappa": (observed - expected) / (1.0 - expected) if expected < 1.0 else 1.0,
        "affects_accuracy_threshold": False,
    }


def _plan_value(plan: Any, name: str, fallback: int) -> int:
    if plan is None:
        return fallback
    if isinstance(plan, Mapping):
        value = plan.get(name, plan.get("required_sample_size", fallback))
    else:
        value = getattr(plan, name, getattr(plan, "required_sample_size", fallback))
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvaluationProtocolError(f"power plan {name} must be a positive integer")
    return value


def _score_subset(
    truth_records: Mapping[str, Mapping[str, str]],
    arms: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    thresholds: Mapping[str, Any],
    power_plan: Any,
    candidate_arm: str,
    bootstrap_resamples: int,
    seed: int,
) -> dict[str, Any]:
    sample_ids = sorted(truth_records)
    metrics: dict[str, dict[str, Any]] = {}
    correctness: dict[str, list[bool]] = {}
    for name, arm in arms.items():
        metrics[name], correctness[name] = _arm_metrics(truth_records, arm)
    baselines = sorted(name for name in arms if name != candidate_arm)
    alpha = float(thresholds["alpha"])
    simultaneous_confidence = 1.0 - alpha / len(baselines)
    groups = [truth_records[item]["split_group_id"] for item in sample_ids]
    comparisons: dict[str, dict[str, Any]] = {}
    raw_p: dict[str, float] = {}
    for offset, baseline in enumerate(baselines):
        bootstrap = paired_bootstrap_difference(
            [float(value) for value in correctness[candidate_arm]],
            [float(value) for value in correctness[baseline]],
            group_ids=groups,
            confidence=simultaneous_confidence,
            resamples=bootstrap_resamples,
            seed=seed + offset,
        )
        mcnemar = paired_mcnemar_exact(correctness[candidate_arm], correctness[baseline])
        raw_p[baseline] = mcnemar.exact_two_sided_p
        comparisons[baseline] = {
            "accuracy_delta": bootstrap.estimate,
            "cluster_paired_bootstrap_simultaneous_ci": bootstrap.to_dict(),
            "paired_bootstrap_simultaneous_ci": bootstrap.to_dict(),
            "mcnemar": mcnemar.to_dict(),
        }
    adjusted = holm_adjust(raw_p)
    min_delta = (
        percentage_points_to_proportion(thresholds["min_effect_pp"])
        if "min_effect_pp" in thresholds
        else float(thresholds["min_delta"])
    )
    comparison_passes: list[bool] = []
    for baseline in baselines:
        item = comparisons[baseline]
        item["holm_adjusted_p"] = adjusted[baseline]
        item["effect_and_ci_gate"] = (
            item["accuracy_delta"] >= min_delta
            and item["cluster_paired_bootstrap_simultaneous_ci"]["lower"] > 0.0
        )
        item["adjusted_significance_gate"] = (
            adjusted[baseline] <= alpha
            if bool(thresholds.get("require_adjusted_significance", True))
            else True
        )
        item["passes"] = item["effect_and_ci_gate"] and item["adjusted_significance_gate"]
        comparison_passes.append(item["passes"])

    candidate_metrics = metrics[candidate_arm]
    min_accuracy = float(thresholds.get("min_accuracy", thresholds.get("decision_accuracy_floor")))
    min_reject = float(thresholds.get("min_reject_recall", thresholds.get("reject_recall_floor")))
    min_balanced = float(thresholds.get("min_balanced_accuracy", thresholds.get("balanced_accuracy_floor")))
    quality = {
        "accuracy": candidate_metrics["accuracy"] >= min_accuracy,
        "reject_recall": candidate_metrics["reject_recall"] is not None
        and candidate_metrics["reject_recall"] >= min_reject,
        "balanced_accuracy": candidate_metrics["balanced_accuracy"] is not None
        and candidate_metrics["balanced_accuracy"] >= min_balanced,
    }
    legacy_required = int(thresholds.get("required_sample_size", 1))
    power_gates = {
        "sample_count": len(sample_ids) >= _plan_value(power_plan, "required_sample_count", legacy_required),
        "effective_groups": candidate_metrics["effective_groups"]
        >= _plan_value(power_plan, "required_effective_groups", legacy_required),
        "reject_count": candidate_metrics["reject_n"]
        >= _plan_value(power_plan, "required_reject_count", 1),
        "approve_count": candidate_metrics["approve_n"]
        >= _plan_value(power_plan, "required_approve_count", 1),
    }
    if not all(power_gates.values()):
        status = "INCONCLUSIVE"
    elif all(quality.values()) and all(comparison_passes):
        status = "SUCCESS"
    else:
        status = "VALID_TEST_FAILED"
    return {
        "status": status,
        "sample_count": len(sample_ids),
        "effective_group_count": candidate_metrics["effective_groups"],
        "class_counts": {
            "REQUEST_CHANGES": candidate_metrics["reject_n"],
            "APPROVE": candidate_metrics["approve_n"],
        },
        "arm_metrics": metrics,
        "comparisons": comparisons,
        "hard_quality_gates": quality,
        "power_gates": power_gates,
        "min_effect_proportion": min_delta,
    }


def evaluate_three_arm(
    truth: Mapping[str, str] | Sequence[Mapping[str, Any]],
    arms: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    thresholds: Mapping[str, Any],
    power_plan: Any = None,
    candidate_arm: str = "CANDIDATE",
    bootstrap_resamples: int = 10_000,
    seed: int = 0,
    observed_review_reasons: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Score full and CI_PASS cohorts; only the scorer derives the outcome."""

    required_any = [
        ("min_accuracy", "decision_accuracy_floor"),
        ("min_reject_recall", "reject_recall_floor"),
        ("min_balanced_accuracy", "balanced_accuracy_floor"),
        ("min_delta", "min_effect_pp"),
    ]
    missing = [f"{left}|{right}" for left, right in required_any if left not in thresholds and right not in thresholds]
    if "alpha" not in thresholds:
        missing.append("alpha")
    if missing:
        raise EvaluationProtocolError(f"missing preregistered thresholds: {missing}")
    alpha = float(thresholds["alpha"])
    if not 0.0 < alpha < 1.0:
        raise EvaluationProtocolError("alpha must be in (0, 1)")
    truth_records = _normalize_truth_records(truth)
    _, normalized_arms = validate_three_arm_results(truth, arms, candidate_arm=candidate_arm)
    if candidate_arm not in normalized_arms and candidate_arm == "CANDIDATE":
        candidate_arm = "candidate"
    full = _score_subset(
        truth_records,
        normalized_arms,
        thresholds=thresholds,
        power_plan=power_plan,
        candidate_arm=candidate_arm,
        bootstrap_resamples=bootstrap_resamples,
        seed=seed,
    )
    ci_ids = sorted(
        sample_id
        for sample_id, record in truth_records.items()
        if record["aggregate_ci_status"] == "CI_PASS"
    )
    if ci_ids:
        ci_truth = {sample_id: truth_records[sample_id] for sample_id in ci_ids}
        ci_arms = {
            name: {sample_id: records[sample_id] for sample_id in ci_ids}
            for name, records in normalized_arms.items()
        }
        ci_pass = _score_subset(
            ci_truth,
            ci_arms,
            thresholds=thresholds,
            power_plan=power_plan,
            candidate_arm=candidate_arm,
            bootstrap_resamples=bootstrap_resamples,
            seed=seed + 10_000,
        )
        ci_pass["available"] = True
    else:
        ci_pass = {"available": False, "status": "INCONCLUSIVE", "sample_count": 0}
    ci_role = str(thresholds.get("ci_pass_subset_role", "DIAGNOSTIC"))
    status = full["status"]
    if status == "SUCCESS" and ci_role == "CO_PRIMARY" and ci_pass["status"] != "SUCCESS":
        status = "INCONCLUSIVE" if ci_pass["status"] == "INCONCLUSIVE" else "VALID_TEST_FAILED"
    result: dict[str, Any] = {
        "status": status,
        "candidate_arm": candidate_arm,
        "subsets": {"ALL": full, "CI_PASS": ci_pass},
        "ci_pass_subset_role": ci_role,
        "thresholds": dict(thresholds),
        "multiple_comparison_method": "Holm exact-McNemar plus cluster-paired Bonferroni simultaneous bootstrap CIs",
        # Compatibility/reporting conveniences derived from the primary subset.
        "sample_count": full["sample_count"],
        "required_sample_size": _plan_value(power_plan, "required_sample_count", int(thresholds.get("required_sample_size", 1))),
        "sample_sufficient": full["power_gates"]["sample_count"],
        "has_both_classes": full["class_counts"]["REQUEST_CHANGES"] > 0 and full["class_counts"]["APPROVE"] > 0,
        "arm_metrics": full["arm_metrics"],
        "comparisons": full["comparisons"],
        "hard_quality_gates": full["hard_quality_gates"],
        "power_gates": full["power_gates"],
    }
    if observed_review_reasons is not None:
        predicted = {
            sample_id: normalized_arms[candidate_arm][sample_id]["reason_codes"]
            for sample_id in truth_records
            if normalized_arms[candidate_arm][sample_id]["prediction"] == "REQUEST_CHANGES"
        }
        result["rationale_metric"] = observed_review_agreement_precision(
            predicted, observed_review_reasons
        )
    return result

