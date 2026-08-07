import unittest

from agents_v3.knowledge import (
    group_near_duplicate_patches,
    knowledge_manifest_hash,
    validate_knowledge_manifest,
)


def manifest() -> dict:
    value = {
        "schema_version": "3.0.0",
        "manifest_id": "knowledge-v3",
        "knowledge_cutoff": "2026-01-31T00:00:00Z",
        "manifest_sha256": None,
        "sources": [
            {
                "source_id": "repo-agents",
                "kind": "repository_file",
                "origin": "Nexus-Editor/AGENTS.md",
                "revision": "a" * 40,
                "commit_sha": "a" * 40,
                "sha256": "b" * 64,
                "published_at": "2026-01-01T00:00:00Z",
                "available_at": "2026-01-01T00:00:00Z",
                "candidate_visible": True,
            }
        ],
        "sample_groups": [
            {
                "group_id": "g1",
                "sample_ids": ["s1", "s2"],
                "split": "train",
                "max_label_event_at": "2026-01-20T00:00:00Z",
                "basis": ["patch_similarity"],
            }
        ],
        "candidate_source_ids": ["repo-agents"],
        "baseline_source_ids": ["repo-agents"],
        "memory_contamination_risk": "UNKNOWN",
        "memory_probe_report_sha256": None,
    }
    value["manifest_sha256"] = knowledge_manifest_hash(value)
    return value


class KnowledgeTests(unittest.TestCase):
    def test_valid_cutoff_manifest(self) -> None:
        validation = validate_knowledge_manifest(manifest())
        self.assertTrue(validation.ok, validation.to_dict())
        self.assertEqual({item.code for item in validation.warnings}, {"contamination_risk"})

    def test_post_cutoff_candidate_source_is_rejected(self) -> None:
        value = manifest()
        value["sources"][0]["available_at"] = "2026-02-01T00:00:00Z"
        value["manifest_sha256"] = knowledge_manifest_hash(value)
        validation = validate_knowledge_manifest(value)
        self.assertIn("post_cutoff_source", {item.code for item in validation.errors})

    def test_same_sample_cannot_cross_group_split(self) -> None:
        value = manifest()
        value["sample_groups"].append(
            {
                "group_id": "g2",
                "sample_ids": ["s2"],
                "split": "test",
                "max_label_event_at": "2026-02-10T00:00:00Z",
                "basis": ["manual"],
            }
        )
        value["manifest_sha256"] = knowledge_manifest_hash(value)
        validation = validate_knowledge_manifest(value)
        self.assertIn("sample_group_leakage", {item.code for item in validation.errors})

    def test_near_duplicate_grouping_is_deterministic(self) -> None:
        patches = [
            {"sample_id": "a", "diff": "+ const answer = 41;"},
            {"sample_id": "b", "diff": "+ const answer = 42;"},
            {"sample_id": "c", "diff": "+ function unrelated() { return false; }"},
        ]
        one = group_near_duplicate_patches(patches, threshold=0.70)
        two = group_near_duplicate_patches(list(reversed(patches)), threshold=0.70)
        self.assertEqual(one, two)
        self.assertEqual(one["a"], one["b"])
        self.assertNotEqual(one["a"], one["c"])

    def test_direct_entry_enforces_published_schema(self) -> None:
        value = manifest()
        del value["manifest_id"]
        del value["sources"][0]["origin"]
        del value["sample_groups"][0]["basis"]
        value["allow_future_override"] = True
        value["manifest_sha256"] = knowledge_manifest_hash(value)
        validation = validate_knowledge_manifest(value)
        self.assertFalse(validation.ok)
        self.assertTrue(any(item.code.startswith("schema_") for item in validation.errors))


if __name__ == "__main__":
    unittest.main()
