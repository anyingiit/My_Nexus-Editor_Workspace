from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents_v3.artifacts import ArtifactRegistry, REQUIRED_DETERMINISTIC_GATES


HASH = "a" * 64


def provenance() -> dict[str, object]:
    return {
        "source_type": "GENERATED",
        "producer_role": "candidate_worker",
        "source_revision": None,
        "source_sha256": None,
        "observed_at": "2026-08-07T00:00:00Z",
        "knowledge_available_at": "2026-08-07T00:00:00Z",
        "derivation_sha256": HASH,
    }


class ArtifactTests(unittest.TestCase):
    def test_register_validate_and_promote(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "staging" / "candidate.md"
            source.parent.mkdir()
            source.write_text("candidate\n", encoding="utf-8")
            registry = ArtifactRegistry(root / "state" / "artifacts.json", root)
            registry.register(
                artifact_id="candidate-v1",
                path=source,
                producer_task_id="draft",
                artifact_type="candidate",
                provenance=provenance(),
            )
            for gate in REQUIRED_DETERMINISTIC_GATES:
                registry.record_check(
                    "candidate-v1", gate, "PASSED", details_sha256=HASH
                )
            registry.transition("candidate-v1", "VALIDATED")
            target = registry.promote_file("candidate-v1", root / "canonical" / "AGENTS.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "candidate\n")
            self.assertEqual(registry.get("candidate-v1").state, "CANONICAL")
            self.assertEqual(registry.get("candidate-v1").to_manifest()["promotion_status"], "PROMOTED")

    def test_promotion_requires_provenance_and_every_deterministic_gate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "staging" / "candidate.md"
            source.parent.mkdir()
            source.write_text("candidate\n", encoding="utf-8")
            registry = ArtifactRegistry(root / "state" / "artifacts.json", root)
            registry.register(
                artifact_id="candidate-v1",
                path=source,
                producer_task_id="draft",
                provenance=provenance(),
            )
            with self.assertRaisesRegex(ValueError, "missing"):
                registry.promote_file("candidate-v1", root / "canonical" / "AGENTS.md")

    def test_rejects_paths_outside_root(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as outside:
            root = Path(directory)
            external = Path(outside) / "x"
            external.write_text("x", encoding="utf-8")
            registry = ArtifactRegistry(root / "state" / "artifacts.json", root)
            with self.assertRaises(ValueError):
                registry.register(
                    artifact_id="x",
                    path=external,
                    producer_task_id="task",
                    provenance=provenance(),
                )


if __name__ == "__main__":
    unittest.main()
