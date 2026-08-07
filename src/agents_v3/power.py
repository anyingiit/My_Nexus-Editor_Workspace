"""Preregistered sample-size planning for paired three-arm evaluations.

The calculation is an explicit planning approximation, not a post-hoc test.
Its assumptions are written into the returned object so the census can either
confirm them or force a new protocol/profile before dev or test is touched.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, sqrt
from statistics import NormalDist
from typing import Mapping

from .evaluation import wilson_interval


@dataclass(frozen=True)
class PowerPlan:
    method: str
    required_sample_size: int
    paired_required_by_baseline: dict[str, int]
    required_reject_count: int
    required_approve_count: int
    class_prevalence_required_n: int
    assumptions: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def paired_accuracy_required_n(
    *,
    discordance: float,
    target_delta: float,
    familywise_alpha: float = 0.05,
    power: float = 0.8,
    comparisons: int = 2,
) -> int:
    """Approximate paired sample size for a positive McNemar difference.

    ``discordance`` is P(candidate correct, baseline wrong) plus P(candidate
    wrong, baseline correct). ``target_delta`` is their difference. The formula
    uses a two-sided normal approximation with Bonferroni family-wise alpha and
    the paired-difference variance ``discordance - delta**2``.
    """

    if not 0.0 < discordance <= 1.0:
        raise ValueError("discordance must be in (0, 1]")
    if not 0.0 < target_delta <= discordance:
        raise ValueError("target_delta must be in (0, discordance]")
    if not 0.0 < familywise_alpha < 1.0:
        raise ValueError("familywise_alpha must be in (0, 1)")
    if not 0.5 < power < 1.0:
        raise ValueError("power must be in (0.5, 1)")
    if isinstance(comparisons, bool) or not isinstance(comparisons, int) or comparisons < 1:
        raise ValueError("comparisons must be a positive integer")

    z_alpha = NormalDist().inv_cdf(1.0 - familywise_alpha / (2.0 * comparisons))
    z_power = NormalDist().inv_cdf(power)
    alternative_variance = max(0.0, discordance - target_delta * target_delta)
    numerator = z_alpha * sqrt(discordance) + z_power * sqrt(alternative_variance)
    return max(2, ceil((numerator / target_delta) ** 2))


def class_recall_required_n(
    *,
    expected_recall: float,
    minimum_recall: float,
    confidence: float = 0.95,
    search_limit: int = 1_000_000,
) -> int:
    """Find a class count whose projected Wilson lower bound clears a gate."""

    if not 0.0 <= minimum_recall < expected_recall <= 1.0:
        raise ValueError("require 0 <= minimum_recall < expected_recall <= 1")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if search_limit < 2:
        raise ValueError("search_limit must be at least 2")
    for total in range(2, search_limit + 1):
        # Floor is conservative relative to the expected proportion.
        successes = int(expected_recall * total)
        lower, _ = wilson_interval(successes, total, confidence=confidence)
        if lower >= minimum_recall:
            return total
    raise ValueError("no class count met the recall precision target within search_limit")


def plan_three_arm_sample_size(
    *,
    reject_prevalence: float,
    discordance_by_baseline: Mapping[str, float],
    target_delta: float,
    expected_reject_recall: float,
    min_reject_recall: float,
    expected_approve_recall: float,
    min_approve_recall: float,
    familywise_alpha: float = 0.05,
    power: float = 0.8,
    confidence: float = 0.95,
) -> PowerPlan:
    """Plan N from paired power and both class-specific precision constraints."""

    if not 0.0 < reject_prevalence < 1.0:
        raise ValueError("reject_prevalence must be in (0, 1)")
    if len(discordance_by_baseline) != 2:
        raise ValueError("a three-arm plan requires exactly two baseline discordance estimates")
    if any(not name for name in discordance_by_baseline):
        raise ValueError("baseline names must be non-empty")

    paired = {
        str(name): paired_accuracy_required_n(
            discordance=float(discordance),
            target_delta=target_delta,
            familywise_alpha=familywise_alpha,
            power=power,
            comparisons=len(discordance_by_baseline),
        )
        for name, discordance in discordance_by_baseline.items()
    }
    reject_n = class_recall_required_n(
        expected_recall=expected_reject_recall,
        minimum_recall=min_reject_recall,
        confidence=confidence,
    )
    approve_n = class_recall_required_n(
        expected_recall=expected_approve_recall,
        minimum_recall=min_approve_recall,
        confidence=confidence,
    )
    class_total = max(
        ceil(reject_n / reject_prevalence),
        ceil(approve_n / (1.0 - reject_prevalence)),
    )
    required = max(max(paired.values()), class_total)
    return PowerPlan(
        method="paired-normal-McNemar planning + projected Wilson class gates",
        required_sample_size=required,
        paired_required_by_baseline=paired,
        required_reject_count=reject_n,
        required_approve_count=approve_n,
        class_prevalence_required_n=class_total,
        assumptions={
            "reject_prevalence": reject_prevalence,
            "discordance_by_baseline": dict(discordance_by_baseline),
            "target_delta": target_delta,
            "expected_reject_recall": expected_reject_recall,
            "min_reject_recall": min_reject_recall,
            "expected_approve_recall": expected_approve_recall,
            "min_approve_recall": min_approve_recall,
            "familywise_alpha": familywise_alpha,
            "power": power,
            "confidence": confidence,
        },
    )
