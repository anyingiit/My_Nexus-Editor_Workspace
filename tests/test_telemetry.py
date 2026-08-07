from __future__ import annotations

from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from agents_v3.telemetry import CallRecord, JsonlTelemetry


class TelemetryTests(unittest.TestCase):
    def test_append_and_summary(self) -> None:
        with TemporaryDirectory() as directory:
            log = JsonlTelemetry(Path(directory) / "calls.jsonl")
            log.append(
                CallRecord(
                    run_id="run",
                    request_id="r1",
                    task_id="t1",
                    provider="mock",
                    model="judge",
                    profile_hash="hash",
                    started_at="2026-01-01T00:00:00Z",
                    latency_ms=10,
                    status="SUCCESS",
                    input_tokens=4,
                    output_tokens=2,
                    actual_cost_usd=0.01,
                )
            )
            log.append(
                CallRecord(
                    run_id="run",
                    request_id="r2",
                    task_id="t2",
                    provider="mock",
                    model="judge",
                    profile_hash="hash",
                    started_at="2026-01-01T00:00:01Z",
                    latency_ms=30,
                    status="FAILED",
                    error_category="schema_invalid",
                )
            )
            summary = log.summary()
            self.assertEqual(summary["calls"], 2)
            self.assertEqual(summary["failures"], 1)
            self.assertEqual(summary["failure_categories"], {"schema_invalid": 1})
            self.assertEqual(summary["actual_cost_usd"], 0.01)

    def test_failed_record_requires_category(self) -> None:
        record = CallRecord(
            run_id="run",
            request_id="r1",
            task_id="t1",
            provider="mock",
            model="judge",
            profile_hash="hash",
            started_at="now",
            latency_ms=0,
            status="FAILED",
        )
        with self.assertRaises(ValueError):
            record.validate()


if __name__ == "__main__":
    unittest.main()
