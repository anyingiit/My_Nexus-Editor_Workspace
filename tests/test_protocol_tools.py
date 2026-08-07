from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents_v3.dataset import primary_sample_ids, validate_eligibility_manifest
from agents_v3.evaluation import (
    evaluate_three_arm,
    paired_bootstrap_difference,
    paired_mcnemar_exact,
    stability_summary,
    validate_three_arm_results,
    wilson_interval,
)
from agents_v3.final_test import (
    FinalTestBarrier,
    FinalTestStateError,
    FinalTestStatus,
    ProtocolViolation,
    profile_fingerprint,
)
from agents_v3.profiles import freeze_profile, load_profile
from agents_v3.security import assert_capability_manifest, validate_capability_manifest


SHA_A = "a" * 40
SHA_B = "b" * 40
HASH_A = "a" * 64
HASH_B = "b" * 64


def valid_dataset_manifest() -> dict:
    return {
        "schema_version": "3.0.0",
        "dataset_id": "nexus-pr-census-v3",
        "counts": {
            "discovered": 3,
            "eligible": 1,
            "rationale": 1,
            "administrative": 1,
            "excluded": 1,
            "unknown": 0,
        },
        "samples": [
            {
                "schema_version": "3.0.0",
                "sample_id": "pr-1-review-1",
                "repository_id": "repo-opaque",
                "pull_request_group_id": "pr-group-1",
                "eligibility_cohort": "decision",
                "rationale_eligible": True,
                "observed_review_issue_count": 2,
                "label_event_id": "review-100",
                "label_event_type": "REQUEST_CHANGES",
                "label_timestamp": "2026-01-01T12:00:00Z",
                "head_sha_at_event": SHA_A,
                "base_sha_at_event": SHA_B,
                "snapshot_head_sha": SHA_A,
                "snapshot_base_sha": SHA_B,
                "snapshot_manifest_sha256": HASH_A,
                "maintainer_identity_basis": "repository_permission",
                "maintainer_evidence_as_of": "2026-01-01T11:59:00Z",
                "eligibility_reason": "QUALIFIED_MAINTAINER_REVIEW",
                "exclusion_reason": None,
                "split_group_id": "patch-family-1",
                "grouping_evidence_sha256": HASH_B,
                "split": "test",
                "ground_truth_manifest_sha256": "c" * 64,
                "aggregate_ci_status": "CI_PASS",
            },
            {
                "schema_version": "3.0.0",
                "sample_id": "pr-2-admin",
                "repository_id": "repo-opaque",
                "pull_request_group_id": "pr-group-2",
                "eligibility_cohort": "administrative",
                "rationale_eligible": False,
                "observed_review_issue_count": None,
                "label_event_id": "close-200",
                "label_event_type": "DUPLICATE",
                "label_timestamp": "2026-01-02T12:00:00+00:00",
                "head_sha_at_event": SHA_A,
                "base_sha_at_event": SHA_B,
                "snapshot_head_sha": None,
                "snapshot_base_sha": None,
                "snapshot_manifest_sha256": None,
                "maintainer_identity_basis": None,
                "maintainer_evidence_as_of": None,
                "eligibility_reason": "ADMINISTRATIVE_DUPLICATE",
                "exclusion_reason": None,
                "split_group_id": "patch-family-2",
                "grouping_evidence_sha256": HASH_B,
                "split": "unassigned",
                "ground_truth_manifest_sha256": None,
                "aggregate_ci_status": "CI_UNKNOWN",
            },
            {
                "schema_version": "3.0.0",
                "sample_id": "pr-3-excluded",
                "repository_id": "repo-opaque",
                "pull_request_group_id": "pr-group-3",
                "eligibility_cohort": "excluded",
                "rationale_eligible": False,
                "observed_review_issue_count": None,
                "label_event_id": "close-300",
                "label_event_type": "AUTHOR_CLOSED",
                "label_timestamp": "2026-01-03T12:00:00Z",
                "head_sha_at_event": SHA_A,
                "base_sha_at_event": SHA_B,
                "snapshot_head_sha": None,
                "snapshot_base_sha": None,
                "snapshot_manifest_sha256": None,
                "maintainer_identity_basis": None,
                "maintainer_evidence_as_of": None,
                "eligibility_reason": None,
                "exclusion_reason": "AUTHOR_CLOSED_WITHOUT_MAINTAINER_DECISION",
                "split_group_id": "patch-family-3",
                "grouping_evidence_sha256": HASH_B,
                "split": "unassigned",
                "ground_truth_manifest_sha256": None,
                "aggregate_ci_status": "CI_UNKNOWN",
            },
        ],
    }


def frozen_profile(*, model: str = "mock-v1", run_id: str = "run-001") -> dict:
    profile = load_profile(PROJECT_ROOT / "config" / "evaluation_profile.example.yaml")
    profile["profile_id"] = "test-profile"
    profile["run_id"] = run_id
    def resolve(item):
        if isinstance(item, dict):
            for key, child in list(item.items()):
                if key.endswith("_sha256") and child == "0" * 64:
                    item[key] = HASH_A
                elif key == "harness_commit" and child == "0" * 40:
                    item[key] = "a" * 40
                elif key == "sandbox_image_digest" and child == "sha256:" + "0" * 64:
                    item[key] = "sha256:" + HASH_A
                elif isinstance(child, str) and (
                    "REPLACE" in child.upper() or child.upper().startswith("EXAMPLE-")
                ):
                    item[key] = "fixture-v1"
                else:
                    resolve(child)
        elif isinstance(item, list):
            for child in item:
                resolve(child)

    resolve(profile)
    profile["judge"]["model"] = model
    profile["analysis"].update(
        decision_accuracy_floor=0.0,
        reject_recall_floor=0.0,
        balanced_accuracy_floor=0.0,
        required_effective_groups=1,
        required_sample_count=1,
        required_reject_count=1,
        required_approve_count=1,
    )
    return freeze_profile(profile, frozen_at="2026-08-07T00:00:00Z")


def result(profile: dict, sample_id: str, arm: str, decision: str) -> dict:
    return {
        "schema_version": "3.0.0",
        "run_id": profile["run_id"],
        "profile_sha256": profile["profile_sha256"],
        "sample_id": sample_id,
        "split_group_id": f"group-{sample_id}",
        "arm": arm,
        "repeat_index": 0,
        "cache_key": f"cache-{sample_id}-{arm}",
        "packed_input_sha256": HASH_A,
        "decision": decision,
        "reasons": [],
        "schema_valid": True,
        "truncated": False,
        "provider_request_id": "mock-request",
        "usage": {"input_tokens": 10, "output_tokens": 2, "cash_usd": 0.0, "provider_quota": 0.0},
        "latency_ms": 1,
        "attempt": 1,
        "canonical": True,
        "created_at": "2026-08-07T00:00:00Z",
    }


class DatasetProtocolTests(unittest.TestCase):
    def test_event_aligned_manifest_is_valid(self) -> None:
        manifest = valid_dataset_manifest()
        validation = validate_eligibility_manifest(manifest)
        self.assertTrue(validation.ok, validation.to_dict())
        self.assertEqual(primary_sample_ids(manifest, split="test"), ("pr-1-review-1",))

    def test_snapshot_and_event_mismatch_is_rejected(self) -> None:
        manifest = valid_dataset_manifest()
        manifest["samples"][0]["snapshot_head_sha"] = "9" * 40
        validation = validate_eligibility_manifest(manifest)
        self.assertFalse(validation.ok)
        self.assertIn("snapshot_event_mismatch", {error.code for error in validation.errors})

    def test_related_patch_group_cannot_cross_splits(self) -> None:
        manifest = valid_dataset_manifest()
        second = deepcopy(manifest["samples"][0])
        second.update(
            sample_id="pr-4-review-1",
            label_event_id="review-400",
            pull_request_group_id="pr-group-4",
            split="dev",
        )
        manifest["samples"].append(second)
        manifest["counts"].update(discovered=4, eligible=2, rationale=2)
        validation = validate_eligibility_manifest(manifest)
        self.assertIn("related_group_split_leakage", {error.code for error in validation.errors})


class EvaluationTests(unittest.TestCase):
    def test_wilson_interval_matches_reference_example(self) -> None:
        lower, upper = wilson_interval(26, 30)
        self.assertAlmostEqual(lower, 0.7032, places=3)
        self.assertAlmostEqual(upper, 0.9467, places=3)

    def test_exact_mcnemar(self) -> None:
        result = paired_mcnemar_exact(
            [True, True, True, True, True],
            [False, False, False, False, True],
        )
        self.assertEqual(result.candidate_only_correct, 4)
        self.assertEqual(result.baseline_only_correct, 0)
        self.assertEqual(result.exact_two_sided_p, 0.125)

    def test_paired_bootstrap_is_seeded_and_reproducible(self) -> None:
        one = paired_bootstrap_difference(
            [1, 1, 0, 1, 0], [0, 1, 0, 0, 0], resamples=500, seed=123
        )
        two = paired_bootstrap_difference(
            [1, 1, 0, 1, 0], [0, 1, 0, 0, 0], resamples=500, seed=123
        )
        self.assertEqual(one, two)
        self.assertAlmostEqual(one.estimate, 0.4)

    def test_three_arm_gate_uses_paired_effect_and_hard_class_gates(self) -> None:
        truth = {
            f"sample-{index}": "APPROVE" if index % 2 == 0 else "REQUEST_CHANGES"
            for index in range(10)
        }
        candidate = [
            {"sample_id": sample_id, "prediction": label, "reason_codes": []}
            for sample_id, label in truth.items()
        ]
        inverted = [
            {
                "sample_id": sample_id,
                "prediction": "REQUEST_CHANGES" if label == "APPROVE" else "APPROVE",
                "reason_codes": [],
            }
            for sample_id, label in truth.items()
        ]
        arms = {"candidate": candidate, "baseline_repo": inverted, "baseline_third_party": inverted}
        normalized_truth, normalized_arms = validate_three_arm_results(truth, arms)
        self.assertEqual(set(normalized_truth), set(normalized_arms["candidate"]))
        report = evaluate_three_arm(
            truth,
            arms,
            thresholds={
                "required_sample_size": 10,
                "min_accuracy": 0.85,
                "min_reject_recall": 0.70,
                "min_balanced_accuracy": 0.75,
                "min_delta": 0.05,
                "alpha": 0.05,
                "require_adjusted_significance": False,
            },
            bootstrap_resamples=500,
            seed=9,
        )
        self.assertEqual(report["status"], "SUCCESS")
        self.assertTrue(all(item["effect_and_ci_gate"] for item in report["comparisons"].values()))

    def test_stability_is_diagnostic_not_accuracy_ceiling(self) -> None:
        stability = stability_summary(
            {
                "s1": ["APPROVE", "APPROVE", "APPROVE"],
                "s2": ["REQUEST_CHANGES", "APPROVE", "REQUEST_CHANGES"],
            }
        )
        self.assertEqual(stability["metric_family"], "stability_repeatability_only")
        self.assertFalse(stability["affects_accuracy_threshold"])
        self.assertNotIn("accuracy", stability)


class FinalTestBarrierTests(unittest.TestCase):
    def test_infra_resume_keeps_same_run_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "final-test-state.json"
            profile = frozen_profile()
            barrier = FinalTestBarrier.start(
                state_path,
                profile=profile,
                expected_sample_ids=["s1", "s2"],
            )
            barrier.checkpoint_result(result(profile, "s1", "EMPTY", "APPROVE"))
            barrier.mark_infra_incomplete(category="timeout", message="provider timed out")
            self.assertEqual(barrier.status, FinalTestStatus.INFRA_INCOMPLETE)

            resumed = FinalTestBarrier.open(
                state_path,
                profile=profile,
                expected_sample_ids=["s2", "s1"],
            )
            resumed.resume()
            self.assertEqual(resumed.pending_sample_ids, ("s1", "s2"))
            self.assertEqual(resumed.state["resume_count"], 1)

    def test_profile_change_is_terminal_protocol_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "final-test-state.json"
            profile = frozen_profile()
            FinalTestBarrier.start(
                state_path,
                profile=profile,
                expected_sample_ids=["s1"],
            )
            changed = frozen_profile(model="different-model")
            with self.assertRaises(ProtocolViolation):
                FinalTestBarrier.open(state_path, profile=changed)
            invalid = FinalTestBarrier.open(state_path)
            self.assertEqual(invalid.status, FinalTestStatus.PROTOCOL_INVALID)

    def test_incomplete_finalize_becomes_infra_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            barrier = FinalTestBarrier.start(
                Path(directory) / "state.json",
                profile=frozen_profile(run_id="run-002"),
                expected_sample_ids=["s1"],
            )
            with self.assertRaises(FinalTestStateError):
                barrier.finalize(truth={"s1": "APPROVE"})
            self.assertEqual(barrier.status, FinalTestStatus.INFRA_INCOMPLETE)

    def test_profile_fingerprint_is_key_order_independent(self) -> None:
        profile = frozen_profile()
        self.assertEqual(profile_fingerprint(profile), profile_fingerprint(dict(reversed(list(profile.items())))))


class SecurityManifestTests(unittest.TestCase):
    def test_example_manifest_passes(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "sandbox" / "capability_manifest.example.json").read_text(
                encoding="utf-8"
            )
        )
        validation = validate_capability_manifest(manifest)
        self.assertTrue(validation.ok, validation.to_dict())
        self.assertEqual(len(assert_capability_manifest(manifest)), 64)

    def test_candidate_network_or_test_label_access_fails_closed(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "sandbox" / "capability_manifest.example.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["roles"]["candidate_worker"]["network"] = {
            "mode": "allowlist",
            "hosts": ["api.github.com"],
            "methods": ["GET"],
        }
        manifest["roles"]["candidate_worker"]["ground_truth_access"]["test"] = "read"
        validation = validate_capability_manifest(manifest)
        codes = {error.code for error in validation.errors}
        self.assertIn("network_enabled", codes)
        self.assertIn("access_matrix_mismatch", codes)

    def test_runner_must_be_rootless_and_networkless(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "sandbox" / "capability_manifest.example.json").read_text(
                encoding="utf-8"
            )
        )
        runner = manifest["roles"]["untrusted_code_runner"]
        runner["controls"]["rootless"] = False
        runner["network"]["mode"] = "host"
        validation = validate_capability_manifest(manifest)
        codes = {error.code for error in validation.errors}
        self.assertIn("unsafe_control", codes)
        self.assertIn("network_enabled", codes)


if __name__ == "__main__":
    unittest.main()
