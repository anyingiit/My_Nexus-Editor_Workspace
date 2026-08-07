"""Strictly read-only GitHub metadata adapter for the collector process.

This module intentionally exposes no generic request method and no mutation
endpoint. Candidate workers and evaluators must not import or receive this
adapter; only the isolated collector role may have a read-only token.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_API_ROOT = "https://api.github.com"


class GitHubReadError(RuntimeError):
    pass


class JsonGetTransport(Protocol):
    def __call__(
        self, url: str, headers: Mapping[str, str]
    ) -> tuple[Any, Mapping[str, str]]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise GitHubReadError("GitHub API redirect refused")


def urllib_json_get(
    url: str, headers: Mapping[str, str]
) -> tuple[Any, Mapping[str, str]]:
    request = Request(url, method="GET", headers=dict(headers))
    opener = build_opener(_NoRedirect())
    with opener.open(request, timeout=30) as response:  # fixed HTTPS host, no redirects
        if response.status != 200:
            raise GitHubReadError(f"GitHub returned HTTP {response.status}")
        return json.load(response), dict(response.headers.items())


class GitHubReadOnlyClient:
    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        token: str | None = None,
        transport: JsonGetTransport = urllib_json_get,
        max_pages: int = 100,
    ) -> None:
        if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repo):
            raise ValueError("invalid GitHub owner or repository name")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.owner = owner
        self.repo = repo
        self._transport = transport
        self._max_pages = max_pages
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agents-v3-readonly-collector",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _repo_path(self, suffix: str) -> str:
        if not suffix.startswith("/") or ".." in suffix or "://" in suffix:
            raise ValueError("unsafe GitHub API suffix")
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    def _get(self, path: str, query: Mapping[str, Any] | None = None) -> tuple[Any, Mapping[str, str]]:
        if not path.startswith(f"/repos/{self.owner}/{self.repo}/"):
            raise ValueError("path is outside the configured repository")
        if ".." in path or "://" in path:
            raise ValueError("unsafe GitHub API path")
        url = _API_ROOT + path
        if query:
            url += "?" + urlencode(query)
        try:
            return self._transport(url, self._headers)
        except GitHubReadError:
            raise
        except Exception as exc:
            raise GitHubReadError(f"GitHub read failed: {type(exc).__name__}") from exc

    def _list(self, path: str, query: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        base = dict(query or {})
        base["per_page"] = 100
        results: list[dict[str, Any]] = []
        for page in range(1, self._max_pages + 1):
            payload, _headers = self._get(path, {**base, "page": page})
            if not isinstance(payload, list):
                raise GitHubReadError("expected a list response")
            results.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < 100:
                return results
        raise GitHubReadError("pagination exceeded configured max_pages")

    def list_pull_requests(self) -> list[dict[str, Any]]:
        return self._list(
            self._repo_path("/pulls"),
            {"state": "all", "sort": "created", "direction": "asc"},
        )

    def list_reviews(self, number: int) -> list[dict[str, Any]]:
        return self._list(self._repo_path(f"/pulls/{self._number(number)}/reviews"))

    def list_pull_files(self, number: int) -> list[dict[str, Any]]:
        return self._list(self._repo_path(f"/pulls/{self._number(number)}/files"))

    def list_pull_commits(self, number: int) -> list[dict[str, Any]]:
        return self._list(self._repo_path(f"/pulls/{self._number(number)}/commits"))

    def list_issue_comments(self, number: int) -> list[dict[str, Any]]:
        return self._list(self._repo_path(f"/issues/{self._number(number)}/comments"))

    def list_issue_events(self, number: int) -> list[dict[str, Any]]:
        return self._list(self._repo_path(f"/issues/{self._number(number)}/events"))

    def get_check_runs(self, sha: str) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
            raise ValueError("invalid commit SHA")
        path = self._repo_path(f"/commits/{sha}/check-runs")
        results: list[dict[str, Any]] = []
        for page in range(1, self._max_pages + 1):
            payload, _headers = self._get(path, {"per_page": 100, "page": page})
            if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
                raise GitHubReadError("invalid check-runs response")
            rows = payload["check_runs"]
            results.extend(row for row in rows if isinstance(row, dict))
            total = payload.get("total_count")
            if len(rows) < 100 or (isinstance(total, int) and len(results) >= total):
                return results
        raise GitHubReadError("check-run pagination exceeded configured max_pages")

    @staticmethod
    def _number(number: int) -> int:
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError("pull request number must be a positive integer")
        return number
