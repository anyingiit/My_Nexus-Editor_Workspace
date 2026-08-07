import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "sandbox" / "run-untrusted.sh"
WATCHDOG = ROOT / "sandbox" / "run_with_limits.py"
IMAGE = "fixture@sha256:" + "a" * 64


class SandboxPreflightTests(unittest.TestCase):
    def run_preflight(self, source: Path, output: Path, command: str = "/bin/sh"):
        environment = {
            **os.environ,
            "RUNNER_PREFLIGHT_ONLY": "1",
            "RUNNER_COMMAND_ALLOWLIST": "/bin/sh",
        }
        return subprocess.run(
            ["sh", str(LAUNCHER), IMAGE, str(source), str(output), command, "-c", "true"],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

    def test_sibling_paths_pass_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            source.mkdir()
            output.mkdir()
            result = self.run_preflight(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no container was started", result.stdout)

    def test_nested_output_alias_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            output = source / "output"
            output.mkdir(parents=True)
            result = self.run_preflight(source, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("inside source", result.stderr)

    def test_disallowed_entrypoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            source.mkdir()
            output.mkdir()
            result = self.run_preflight(source, output, "/usr/bin/python3")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not in RUNNER_COMMAND_ALLOWLIST", result.stderr)

    def test_missing_allowlist_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            source.mkdir()
            output.mkdir()
            environment = {**os.environ, "RUNNER_PREFLIGHT_ONLY": "1"}
            environment.pop("RUNNER_COMMAND_ALLOWLIST", None)
            result = subprocess.run(
                ["sh", str(LAUNCHER), IMAGE, str(source), str(output), "/bin/sh", "-c", "true"],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be set explicitly", result.stderr)


class WatchdogTests(unittest.TestCase):
    def test_output_quota_terminates_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            script = (
                "from pathlib import Path; "
                f"Path({str(output / 'large.bin')!r}).write_bytes(b'x' * 4096)"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(WATCHDOG),
                    "--wall-seconds",
                    "5",
                    "--output-dir",
                    str(output),
                    "--max-output-bytes",
                    "1024",
                    "--",
                    sys.executable,
                    "-c",
                    script,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 125)
            self.assertIn("output-byte limit", result.stderr)


if __name__ == "__main__":
    unittest.main()
