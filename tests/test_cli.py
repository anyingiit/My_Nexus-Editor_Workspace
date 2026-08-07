from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from agents_v3.cli import main
from agents_v3.config import load_config
from agents_v3.profiles import profile_hash
from agents_v3.store import SQLiteStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def invoke(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_validate_is_structurally_green_but_final_closed(self) -> None:
        code, stdout, stderr = self.invoke("validate", "--project-root", str(PROJECT_ROOT))
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertTrue(report["structural_ok"])
        self.assertFalse(report["final_test_ready"])
        code, _, _ = self.invoke(
            "validate", "--project-root", str(PROJECT_ROOT), "--require-final-ready"
        )
        self.assertEqual(code, 3)

    def test_demo_is_no_network_blocked_and_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo"
            code, stdout, stderr = self.invoke("demo", "--output", str(output))
            self.assertEqual((code, stderr), (0, ""))
            summary = json.loads(stdout)
            self.assertEqual(summary["terminal_status"], "BLOCKED")
            self.assertFalse(summary["final_test_executed"])
            self.assertEqual(summary["offline_proxy_status"], "INCONCLUSIVE")
            self.assertTrue(summary["event_chain_valid"])
            for name in ("DEMO_SUMMARY.json", "STATUS.md", "ANYTIME_REPORT.md", "state.sqlite3"):
                self.assertTrue((output / name).exists(), name)
            with SQLiteStore(output / "state.sqlite3") as store:
                self.assertTrue(store.verify_event_chain())
                self.assertEqual(store.get_run_state()["terminal_status"], "BLOCKED")
            second, _, second_error = self.invoke("demo", "--output", str(output))
            self.assertEqual(second, 2)
            self.assertIn("refusing to overwrite", second_error)

    def test_power_and_approval_hash_commands(self) -> None:
        code, stdout, _ = self.invoke("power")
        self.assertEqual(code, 0)
        self.assertGreater(json.loads(stdout)["required_sample_size"], 0)
        code, stdout, _ = self.invoke(
            "approval-hash", str(PROJECT_ROOT / "config" / "run_config.example.yaml")
        )
        self.assertEqual(code, 0)
        self.assertRegex(stdout.strip(), r"^[a-f0-9]{64}$")

    def test_status_does_not_create_a_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "typo.sqlite3"
            code, _, stderr = self.invoke("status", "--state", str(missing))
            self.assertEqual(code, 2)
            self.assertIn("existing non-symlink", stderr)
            self.assertFalse(missing.exists())

    def test_status_rejects_corrupt_database_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / "corrupt.sqlite3"
            corrupt.write_bytes(b"this is not sqlite")
            code, _, stderr = self.invoke("status", "--state", str(corrupt))
            self.assertEqual(code, 2)
            self.assertIn("invalid SQLite state", stderr)
            self.assertNotIn("Traceback", stderr)

    def test_status_rejects_symlink_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite3"
            with SQLiteStore(database):
                pass
            link = root / "state-link.sqlite3"
            link.symlink_to(database)
            code, _, stderr = self.invoke("status", "--state", str(link))
            self.assertEqual(code, 2)
            self.assertIn("non-symlink", stderr)

    def test_require_final_ready_checks_self_hash_and_cross_bindings(self) -> None:
        from tests.test_core import (
            CANDIDATE_A,
            TEST_LAUNCHER_KEY,
            final_config,
            frozen_bundle,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                root,
                ignore=shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__"),
            )
            config_data = final_config()
            config = load_config(config_data)
            profile, report, capability, _, artifacts = frozen_bundle(CANDIDATE_A, config)
            (root / "config" / "run_config.example.yaml").write_text(
                yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8"
            )
            (root / "config" / "evaluation_profile.example.yaml").write_text(
                yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
            )
            (root / "config" / "runtime_capability_report.example.yaml").write_text(
                yaml.safe_dump(report, sort_keys=False), encoding="utf-8"
            )
            (root / "sandbox" / "capability_manifest.example.json").write_text(
                json.dumps(capability, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            key_path = root / "launcher.key"
            key_path.write_bytes(TEST_LAUNCHER_KEY)
            artifact_root = root / "final-artifacts"
            artifact_root.mkdir()
            resolution_entries = []
            for index, (profile_path, content) in enumerate(sorted(artifacts.items())):
                relative = f"artifact-{index:02d}.bin"
                (artifact_root / relative).write_bytes(content)
                resolution_entries.append(
                    {"profile_path": profile_path, "relative_path": relative}
                )
            resolution_path = artifact_root / "resolution.json"
            resolution_path.write_text(
                json.dumps(
                    {
                        "schema_version": "3.0.0",
                        "run_id": config.run_id,
                        "artifacts": resolution_entries,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            code, stdout, stderr = self.invoke(
                "validate",
                "--project-root",
                str(root),
                "--require-final-ready",
                "--launcher-key-file",
                str(key_path),
                "--artifact-resolution-manifest",
                str(resolution_path),
            )
            self.assertEqual((code, stderr), (0, ""), stdout)
            self.assertTrue(json.loads(stdout)["final_test_ready"])

            profile["run_id"] = "different-run"
            profile["profile_sha256"] = profile_hash(profile)
            (root / "config" / "evaluation_profile.example.yaml").write_text(
                yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
            )
            code, stdout, _ = self.invoke(
                "validate",
                "--project-root",
                str(root),
                "--require-final-ready",
                "--launcher-key-file",
                str(key_path),
                "--artifact-resolution-manifest",
                str(resolution_path),
            )
            self.assertEqual(code, 3)
            result = json.loads(stdout)
            self.assertFalse(result["final_test_ready"])
            self.assertTrue(
                any("profile.run_id" in item for item in result["readiness_blockers"])
            )


if __name__ == "__main__":
    unittest.main()
