import unittest

from agents_v3.power import (
    class_recall_required_n,
    paired_accuracy_required_n,
    plan_three_arm_sample_size,
)


class PowerPlanningTests(unittest.TestCase):
    def test_paired_n_increases_for_smaller_effect(self):
        large_effect = paired_accuracy_required_n(discordance=0.25, target_delta=0.10)
        small_effect = paired_accuracy_required_n(discordance=0.25, target_delta=0.05)
        self.assertGreater(small_effect, large_effect)

    def test_class_count_has_required_projected_lower_bound(self):
        count = class_recall_required_n(expected_recall=0.85, minimum_recall=0.70)
        self.assertGreater(count, 1)

    def test_three_arm_plan_uses_maximum_constraint(self):
        plan = plan_three_arm_sample_size(
            reject_prevalence=0.35,
            discordance_by_baseline={"repo": 0.20, "third_party": 0.30},
            target_delta=0.05,
            expected_reject_recall=0.85,
            min_reject_recall=0.70,
            expected_approve_recall=0.90,
            min_approve_recall=0.75,
        )
        constraints = [
            *plan.paired_required_by_baseline.values(),
            plan.class_prevalence_required_n,
        ]
        self.assertEqual(plan.required_sample_size, max(constraints))
        self.assertEqual(set(plan.paired_required_by_baseline), {"repo", "third_party"})


if __name__ == "__main__":
    unittest.main()
