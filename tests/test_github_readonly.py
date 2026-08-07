from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from agents_v3.github_readonly import GitHubReadOnlyClient


class GitHubReadOnlyTests(unittest.TestCase):
    def test_only_constructs_fixed_host_gets(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake(url: str, headers):
            calls.append((url, dict(headers)))
            return [], {}

        client = GitHubReadOnlyClient("owner", "repo", token="secret", transport=fake)
        self.assertEqual(client.list_reviews(12), [])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].startswith("https://api.github.com/repos/owner/repo/"))
        self.assertIn("Authorization", calls[0][1])

    def test_rejects_injected_names_and_numbers(self) -> None:
        with self.assertRaises(ValueError):
            GitHubReadOnlyClient("owner/../../x", "repo")
        client = GitHubReadOnlyClient("owner", "repo", transport=lambda *_: ([], {}))
        with self.assertRaises(ValueError):
            client.list_reviews(-1)
        with self.assertRaises(ValueError):
            client.get_check_runs("not-a-sha")

    def test_check_runs_are_paginated(self) -> None:
        calls: list[str] = []

        def fake(url: str, headers):
            calls.append(url)
            page = parse_qs(urlparse(url).query).get("page", [""])[0]
            if page == "1":
                return {"total_count": 101, "check_runs": [{"id": i} for i in range(100)]}, {}
            return {"total_count": 101, "check_runs": [{"id": 100}]}, {}

        client = GitHubReadOnlyClient("owner", "repo", transport=fake)
        checks = client.get_check_runs("a" * 40)
        self.assertEqual(len(checks), 101)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
