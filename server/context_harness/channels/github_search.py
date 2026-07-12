"""GitHub search channel — search repositories, READMEs, and code.

Uses the GitHub REST API (no auth required for public search, but
rate-limited to 10 req/min without a token). Returns repos with
descriptions, stars, languages, and README content.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"


class GitHubChannel:
    """GitHub repository + code search channel."""

    def __init__(self, token: str = "") -> None:
        self._token = token
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/vnd.github.v3+json"}
            if self._token:
                headers["Authorization"] = f"token {self._token}"
            self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._client

    async def search_repos(
        self,
        query: str,
        max_results: int = 10,
        sort: str = "stars",
        language: str = "",
    ) -> list[dict]:
        """Search GitHub repositories.

        Args:
            query: search terms
            max_results: max results (GitHub caps at 100)
            sort: "stars", "forks", or "updated"
            language: filter by language (e.g. "python")

        Returns:
            list of repo dicts with name, full_name, description, url,
            stars, forks, language, topics, updated_at.
        """
        q = query
        if language:
            q += f" language:{language}"
        params = {
            "q": q,
            "sort": sort,
            "order": "desc",
            "per_page": min(max_results, 100),
        }
        try:
            resp = await self.client.get(f"{GITHUB_API}/search/repositories", params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            return [self._normalize_repo(r) for r in items]
        except Exception:
            return []

    async def search_code(
        self,
        query: str,
        max_results: int = 10,
        language: str = "",
    ) -> list[dict]:
        """Search code across GitHub repos."""
        q = query
        if language:
            q += f" language:{language}"
        params = {"q": q, "per_page": min(max_results, 100)}
        try:
            resp = await self.client.get(f"{GITHUB_API}/search/code", params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            return [
                {
                    "name": item.get("name", ""),
                    "path": item.get("path", ""),
                    "repo": item.get("repository", {}).get("full_name", ""),
                    "url": item.get("html_url", ""),
                    "score": item.get("score", 0),
                }
                for item in items
            ]
        except Exception:
            return []

    async def get_readme(self, owner: str, repo: str) -> str:
        """Fetch README content for a repo (decoded)."""
        try:
            resp = await self.client.get(f"{GITHUB_API}/repos/{owner}/{repo}/readme")
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", "")
            encoding = data.get("encoding", "base64")
            if encoding == "base64" and content:
                return base64.b64decode(content).decode("utf-8", errors="replace")[:10000]
            return content[:10000] if content else ""
        except Exception:
            return ""

    def _normalize_repo(self, r: dict) -> dict:
        return {
            "name": r.get("name", ""),
            "full_name": r.get("full_name", ""),
            "description": r.get("description", "") or "",
            "url": r.get("html_url", ""),
            "stars": r.get("stargazers_count", 0),
            "forks": r.get("forks_count", 0),
            "language": r.get("language", "") or "",
            "topics": r.get("topics", []),
            "updated_at": r.get("updated_at", ""),
            "open_issues": r.get("open_issues_count", 0),
            "license": (r.get("license") or {}).get("name", "") if r.get("license") else "",
        }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_channel: GitHubChannel | None = None


def get_channel() -> GitHubChannel:
    global _channel
    if _channel is None:
        import os
        _channel = GitHubChannel(token=os.environ.get("GITHUB_TOKEN", ""))
    return _channel