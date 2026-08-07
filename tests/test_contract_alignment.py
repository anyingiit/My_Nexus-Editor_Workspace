from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from agents_v3.dataset import canonical_sample
from agents_v3.evaluation import (
    evaluate_three_arm,
    paired_bootstrap_difference,
    percentage_points_to_proportion,
)
from agents_v3.final_test import FinalTestBarrier, FinalTestStatus, ProtocolViolation
from agents_v3.profiles import SchemaValidationError, freeze_profile, load_profile


PROJECT = Path(__file__).resolve().parents[1]
HASH = "a" * 64


def resolve_profile_fixture(value: dict) -> None:
    def visit(item):
        if isinstance(item, dict):
            for key, child in list(item.items()):
                if key.endswith("_sha256") and child == "0" * 64:
                    item[key] = HASH
                elif key == "harness_commit" and child == "0" * 40:
                    item[key] = "a" * 40
                elif key == "sandbox_image_digest" and child == "sha256:" + "0" * 64:
                    item[key] = "sha256:" + HASH
                elif isinstance(child, str) and (
                    "REPLACE" in child.upper() or child.upper().startswith("EXAMPLE-")
                ):
                    item[key] = "fixture-v1"
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)


def profile() -> dict:
    value = load_profile(PROJECT / "config" / "evaluation_profile.example.yaml")
    value["run_id"] = "contract-run"
    value["profile_id"] = "contract-profile"
    resolve_profile_fixture(value)
    value["analysis"].update(
        decision_accuracy_floor=0.9,
        reject_recall_floor=0.9,
        balanced_accuracy_floor=0.9,
        required_effective_groups=10,
        required_sample_count=10,
        required_reject_count=5,
        required_approve_count=5,
    )
    return freeze_profile(value, frozen_at="2026-08-07T00:00:00Z")


def result(profile_value: dict, sample: str, arm: str, decision: str) -> dict:
    return {
        "schema_version": "3.0.0",
        "run_id": profile_value["run_id"],
        "profile_sha256": profile_value["profile_sha256"],
        "sample_id": sample,
        "split_group_id": f"g-{sample}",
        "arm": arm,
        "repeat_index": 0,
        "cache_key": f"{sample}-{arm}",
        "packed_input_sha256": HASH,
        "decision": decision,
        "reasons": [],
        "schema_valid": True,
        "truncated": False,
        "provider_request_id": "mock",
        "usage": {"input_tokens": 1, "output_tokens": 1, "cash_usd": 0.0, "provider_quota": 0.0},
        "latency_ms": 1,
        "attempt": 1,
        "canonical": True,
        "created_at": "2026-08-07T00:00:00Z",
    }


class ContractAlignmentTests(unittest.TestCase):
    def test_sample_schema_rejects_legacy_alias(self) -> None:
        with self.assertRaises(SchemaValidationError):
            canonical_sample({"schema_version": "3.0.0", "cohort": "decision"})

    def test_frozen_profile_detects_post_freeze_mutation(self) -> None:
        frozen = profile()
        tampered = deepcopy(frozen)
        tampered["judge"]["model"] = "changed"
        with self.assertRaisesRegex(ValueError, "profile_sha256"):
            FinalTestBarrier.start(
                Path(tempfile.gettempdir()) / "never-created-contract-test.json",
                profile=tampered,
                expected_sample_ids=["s1"],
            )

    def test_cluster_bootstrap_reports_group_effective_n(self) -> None:
        interval = paired_bootstrap_difference(
            [1, 1, 1, 1],
            [0, 0, 0, 0],
            group_ids=["g1", "g1", "g2", "g2"],
            resamples=200,
            seed=7,
        )
        self.assertEqual(interval.effective_groups, 2)
        self.assertTrue(interval.cluster_resampled)
        self.assertEqual(percentage_points_to_proportion(5), 0.05)

    def test_class_and_group_power_gates_and_ci_pass_subset(self) -> None:
        truth = [
            {
                "sample_id": f"s{i}",
                "label": "APPROVE" if i % 2 == 0 else "REQUEST_CHANGES",
                "split_group_id": f"g{i // 2}",
                "aggregate_ci_status": "CI_PASS" if i < 4 else "CI_FAIL",
            }
            for i in range(10)
        ]
        candidate = [{"sample_id": item["sample_id"], "prediction": item["label"]} for item in truth]
        inverted = [
            {
                "sample_id": item["sample_id"],
                "prediction": "REQUEST_CHANGES" if item["label"] == "APPROVE" else "APPROVE",
            }
            for item in truth
        ]
        report = evaluate_three_arm(
            truth,
            {"CANDIDATE": candidate, "EMPTY": inverted, "REPO_BASELINE": inverted},
            thresholds={
                "alpha": 0.05,
                "min_effect_pp": 5,
                "decision_accuracy_floor": 0.9,
                "reject_recall_floor": 0.9,
                "balanced_accuracy_floor": 0.9,
                "require_adjusted_significance": False,
                "ci_pass_subset_role": "DIAGNOSTIC",
            },
            power_plan={
                "required_sample_count": 10,
                "required_effective_groups": 6,
                "required_reject_count": 5,
                "required_approve_count": 5,
            },
            bootstrap_resamples=200,
        )
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertFalse(report["power_gates"]["effective_groups"])
        self.assertEqual(report["subsets"]["CI_PASS"]["sample_count"], 4)

    def test_final_outcome_is_computed_by_frozen_scorer(self) -> None:
        frozen = profile()
        samples = [f"s{i}" for i in range(10)]
        truth = [
            {
                "sample_id": sample,
                "label": "APPROVE" if i % 2 == 0 else "REQUEST_CHANGES",
                "split_group_id": f"g-{sample}",
                "aggregate_ci_status": "CI_PASS",
            }
            for i, sample in enumerate(samples)
        ]
        with tempfile.TemporaryDirectory() as directory:
            barrier = FinalTestBarrier.start(
                Path(directory) / "state.json", profile=frozen, expected_sample_ids=samples
            )
            for item in truth:
                for arm in frozen["execution"]["arms"]:
                    decision = item["label"] if arm == "CANDIDATE" else (
                        "REQUEST_CHANGES" if item["label"] == "APPROVE" else "APPROVE"
                    )
                    barrier.checkpoint_result(result(frozen, item["sample_id"], arm, decision))
            report = barrier.finalize(truth=truth)
            self.assertEqual(report["status"], "SUCCESS")
            self.assertEqual(barrier.status, FinalTestStatus.SUCCESS)

    def test_malformed_result_terminally_invalidates_barrier(self) -> None:
        frozen = profile()
        with tempfile.TemporaryDirectory() as directory:
            barrier = FinalTestBarrier.start(
                Path(directory) / "state.json", profile=frozen, expected_sample_ids=["s1"]
            )
            malformed = result(frozen, "s1", "EMPTY", "APPROVE")
            malformed["caller_claimed_outcome"] = "SUCCESS"
            with self.assertRaises(ProtocolViolation):
                barrier.checkpoint_result(malformed)
            self.assertEqual(barrier.status, FinalTestStatus.PROTOCOL_INVALID)


if __name__ == "__main__":
    unittest.main()
