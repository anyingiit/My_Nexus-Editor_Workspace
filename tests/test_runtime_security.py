from copy import deepcopy
import json
from pathlib import Path
import unittest

import yaml

from agents_v3.runtime import (
    RuntimeCapabilityError,
    assert_runtime_capability_report,
    runtime_report_hash,
    sign_runtime_capability_report,
    validate_runtime_capability_report,
)
from agents_v3.security import capability_manifest_hash, validate_capability_manifest


ROOT = Path(__file__).resolve().parents[1]
TEST_LAUNCHER_KEY = b"agents-v3-runtime-test-key"


class RuntimeCapabilityTests(unittest.TestCase):
    def fixture(self) -> dict:
        return yaml.safe_load(
            (ROOT / "config" / "runtime_capability_report.example.yaml").read_text(
                encoding="utf-8"
            )
        )

    def measured(self) -> dict:
        report = self.fixture()
        capability = json.loads(
            (ROOT / "sandbox" / "capability_manifest.example.json").read_text(
                encoding="utf-8"
            )
        )
        report["generated_by"] = "trusted-launcher-fixture"
        report["capability_manifest_sha256"] = capability_manifest_hash(capability)
        for role in report["roles"]:
            role["process_identity"] = f"uid-fixture-{role['role']}"
            role["executable"] = f"/opt/agents-v3/{role['role']}"
            role["probes"] = {key: True for key in role["probes"]}
            if role["role"] == "untrusted_code_runner":
                role["image_digest"] = "sha256:" + "a" * 64
        report["all_required_probes_passed"] = True
        report["run_config_sha256"] = "b" * 64
        return sign_runtime_capability_report(
            report, TEST_LAUNCHER_KEY, nonce="runtime-test-single-use-nonce"
        )

    def test_unmeasured_template_fails_closed(self) -> None:
        report = self.fixture()
        validation = validate_runtime_capability_report(report)
        self.assertFalse(validation.ok)
        self.assertFalse(validation.computed_all_probes_passed)
        with self.assertRaises(RuntimeCapabilityError):
            assert_runtime_capability_report(report)

    def test_complete_measured_report_is_content_addressed(self) -> None:
        report = self.measured()
        self.assertEqual(assert_runtime_capability_report(report), report["manifest_sha256"])

    def test_missing_role_and_false_summary_cannot_claim_pass(self) -> None:
        report = self.fixture()
        report["roles"].pop()
        for role in report["roles"]:
            role["probes"] = {key: True for key in role["probes"]}
        report["all_required_probes_passed"] = True
        report["manifest_sha256"] = runtime_report_hash(report)
        validation = validate_runtime_capability_report(report)
        self.assertFalse(validation.ok)
        self.assertIn("missing_roles", {item.code for item in validation.errors})

    def test_direct_entry_enforces_published_schema(self) -> None:
        report = self.measured()
        del report["roles"][0]["process_identity"]
        report["roles"][0]["unexpected_allow_all"] = True
        report["manifest_sha256"] = runtime_report_hash(report)
        validation = validate_runtime_capability_report(report)
        self.assertFalse(validation.ok)
        self.assertTrue(any(item.code.startswith("schema_") for item in validation.errors))


class StaticCapabilityMountTests(unittest.TestCase):
    def test_candidate_cannot_alias_test_truth_under_another_path(self) -> None:
        manifest = json.loads(
            (ROOT / "sandbox" / "capability_manifest.example.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = deepcopy(manifest)
        manifest["roles"]["candidate_worker"]["mounts"].append(
            {
                "source_id": "policy:test_ground_truth",
                "target": "/workspace/input/innocent-name",
                "access": "ro",
                "class": "test_ground_truth",
            }
        )
        validation = validate_capability_manifest(manifest)
        self.assertFalse(validation.ok)
        self.assertIn(
            "protected_mount_role_violation",
            {item.code for item in validation.errors},
        )

    def test_direct_static_entry_enforces_published_schema(self) -> None:
        manifest = json.loads(
            (ROOT / "sandbox" / "capability_manifest.example.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["schema_version"] = "garbage"
        manifest["override_allow_all"] = True
        validation = validate_capability_manifest(manifest)
        self.assertFalse(validation.ok)
        self.assertTrue(any(item.code.startswith("schema_") for item in validation.errors))


if __name__ == "__main__":
    unittest.main()
