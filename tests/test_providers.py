from __future__ import annotations

import json
import unittest

from agents_v3.providers import (
    DeterministicMockProvider,
    EvaluationProfile,
    ProviderGate,
    ProviderRequest,
)


class ProviderTests(unittest.TestCase):
    def test_profile_fingerprint_is_stable_and_sensitive(self) -> None:
        a = EvaluationProfile("mock", "judge", "p1", "s1")
        b = EvaluationProfile("mock", "judge", "p1", "s1")
        c = EvaluationProfile("mock", "judge-v2", "p1", "s1")
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertNotEqual(a.fingerprint(), c.fingerprint())

    def test_mock_is_deterministic(self) -> None:
        profile = EvaluationProfile("mock", "judge", "p1", "s1")
        request = ProviderRequest("r1", "t1", profile, "hello", {})
        provider = DeterministicMockProvider()
        first = provider.complete(request)
        second = provider.complete(request)
        self.assertEqual(json.loads(first.text), json.loads(second.text))
        self.assertEqual(request.cache_key(), request.cache_key())

    def test_gate_rejects_nonpositive_rate(self) -> None:
        with self.assertRaises(ValueError):
            ProviderGate(requests_per_second=0)


if __name__ == "__main__":
    unittest.main()
