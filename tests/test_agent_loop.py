import unittest

from agents_v3.agent_loop import (
    AgentLoopProtocolError,
    create_agent_loop_plan,
    score_agent_loop_results,
    validate_agent_loop_plan,
)


def plan() -> dict:
    return create_agent_loop_plan(
        experiment_id="paired-v3",
        issues=[
            {"issue_id": "i1", "issue_prompt_hash": "a" * 64, "base_commit": "b" * 40},
            {"issue_id": "i2", "issue_prompt_hash": "c" * 64, "base_commit": "d" * 40},
        ],
        coding_model={"provider": "fixture", "model": "coding-v1", "temperature": 0},
        common_budget={"tokens": 1000, "wall_seconds": 60, "cash_microusd": 1000},
        reviewer_profile_hash="e" * 64,
        seed=7,
    )


class AgentLoopTests(unittest.TestCase):
    def test_plan_has_equal_model_base_and_budget(self) -> None:
        value = plan()
        validate_agent_loop_plan(value)
        for issue in {item["issue_id"] for item in value["assignments"]}:
            rows = [item for item in value["assignments"] if item["issue_id"] == issue]
            self.assertEqual({item["arm"] for item in rows}, {"empty", "repository", "candidate"})
            self.assertEqual(len({item["base_commit"] for item in rows}), 1)
            self.assertEqual(len({tuple(item["budget"].items()) for item in rows}), 1)

    def test_blinded_complete_results_score_paired(self) -> None:
        value = plan()
        results = []
        for item in value["assignments"]:
            candidate = item["arm"] == "candidate"
            results.append(
                {
                    "blind_task_id": item["blind_task_id"],
                    "coding_model_hash": value["coding_model_hash"],
                    "patch_sha256": "f" * 64,
                    "completion_status": "COMPLETE",
                    "ci_passed": candidate,
                    "blind_problem_count": 0 if candidate else 2,
                    "blind_severity_score": 0.0 if candidate else 2.0,
                    "blind_completion_score": 1.0 if candidate else 0.5,
                    "tokens_used": 500,
                    "wall_seconds": 20,
                    "cash_microusd": 500,
                }
            )
        report = score_agent_loop_results(value, results)
        self.assertEqual(report["construct"], "agent_in_the_loop_contribution_quality")
        self.assertGreater(
            report["candidate_paired_differences"]["repository"]["completion_score_delta"],
            0,
        )

    def test_model_change_or_budget_overrun_is_rejected(self) -> None:
        value = plan()
        item = value["assignments"][0]
        bad = {
            "blind_task_id": item["blind_task_id"],
            "coding_model_hash": "0" * 64,
            "patch_sha256": "f" * 64,
            "completion_status": "FAILED",
            "ci_passed": False,
            "blind_problem_count": 0,
            "blind_severity_score": 0.0,
            "blind_completion_score": 0.0,
            "tokens_used": 1001,
            "wall_seconds": 0,
            "cash_microusd": 0,
        }
        with self.assertRaises(AgentLoopProtocolError):
            score_agent_loop_results(value, [bad])

    def test_direct_plan_entry_enforces_published_schema(self) -> None:
        value = plan()
        del value["reviewer_profile_hash"]
        value["unblinded_arm_map"] = {"x": "candidate"}
        value["assignments"][0]["posthoc_note"] = "leak"
        with self.assertRaisesRegex(AgentLoopProtocolError, "published schema"):
            validate_agent_loop_plan(value)

    def test_result_nan_is_not_canonical_json(self) -> None:
        value = plan()
        results = []
        for item in value["assignments"]:
            results.append(
                {
                    "blind_task_id": item["blind_task_id"],
                    "coding_model_hash": value["coding_model_hash"],
                    "patch_sha256": "f" * 64,
                    "completion_status": "COMPLETE",
                    "ci_passed": True,
                    "blind_problem_count": 0,
                    "blind_severity_score": float("nan") if not results else 0.0,
                    "blind_completion_score": 1.0,
                    "tokens_used": 1,
                    "wall_seconds": 1,
                    "cash_microusd": 0,
                }
            )
        with self.assertRaisesRegex(AgentLoopProtocolError, "published result schema"):
            score_agent_loop_results(value, results)


if __name__ == "__main__":
    unittest.main()
