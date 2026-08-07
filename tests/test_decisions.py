from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents_v3.decisions import DecisionOption, DecisionRequest, DecisionStore


class DecisionTests(unittest.TestCase):
    def _request(self, expires: datetime) -> DecisionRequest:
        return DecisionRequest(
            request_id="budget-1",
            question="Increase budget?",
            evidence=("probe exceeded estimate",),
            options=(
                DecisionOption("stop", "Stop and report"),
                DecisionOption("increase", "Increase budget", 5.0, 3600),
            ),
            default_option_id="stop",
            expires_at=expires.isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
            category="budget_change",
        )

    def test_unanswered_request_fails_closed_at_expiry(self) -> None:
        with TemporaryDirectory() as directory:
            store = DecisionStore(Path(directory) / "decisions.json")
            now = datetime.now(timezone.utc)
            store.create(self._request(now + timedelta(minutes=1)))
            self.assertIsNone(store.effective_option("budget-1", now=now))
            self.assertEqual(
                store.effective_option("budget-1", now=now + timedelta(minutes=2)),
                "stop",
            )

    def test_explicit_resolution_wins(self) -> None:
        with TemporaryDirectory() as directory:
            store = DecisionStore(Path(directory) / "decisions.json")
            store.create(self._request(datetime.now(timezone.utc) + timedelta(hours=1)))
            store.resolve("budget-1", "increase")
            self.assertEqual(store.effective_option("budget-1"), "increase")


if __name__ == "__main__":
    unittest.main()
