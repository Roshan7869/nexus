"""Papers with Code channel — search papers linked to code implementations.

Uses the Papers with Code API (https://paperswithcode.com/api/v1/).
Free, no auth required. Returns papers with linked GitHub repos.
"""
from __future__ import annotations

from typing import Any

import httpx

PWC_API = "https://paperswithcode.com/api/v1"


class PapersWithCodeChannel:
    """Papers with Code search channel."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict]:
        """Search Papers with Code for papers + implementations.

        Returns list of dicts with title, abstract, authors, year,
        paper_url, code_url, stars, area.
        """
        params = {"q": query, "page": 1, "page_size": min(max_results, 50)}
        try:
            resp = await self.client.get(f"{PWC_API}/search/", params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            return [self._normalize(r) for r in results]
        except Exception:
            return []

    async def get_paper(self, paper_id: str) -> dict | None:
        """Fetch a single paper by ID."""
        try:
            resp = await self.client.get(f"{PWC_API}/papers/{paper_id}/")
            resp.raise_for_status()
            return self._normalize(resp.json())
        except Exception:
            return None

    async def list_evaluations(self, task: str = "") -> list[dict]:
        """List evaluation areas/tasks (e.g. 'image-classification')."""
        try:
            url = f"{PWC_API}/tasks/"
            if task:
                url += f"?q={task}"
            resp = await self.client.get(url)
            resp.raise_for_status()
            return resp.json().get("results", [])
        except Exception:
            return []

    def _normalize(self, r: dict) -> dict:
        paper = r.get("paper", r)
        repo = r.get("repository", r.get("implementation", {}))
        return {
            "title": paper.get("title", r.get("title", "")),
            "abstract": paper.get("abstract", r.get("abstract", "")),
            "authors": paper.get("authors", r.get("authors", [])),
            "year": paper.get("year", r.get("year")),
            "paper_url": paper.get("url_pdf", paper.get("url", r.get("url", ""))),
            "arxiv_id": paper.get("arxiv_id", r.get("arxiv_id", "")),
            "code_url": repo.get("url", r.get("repo_url", "")) if isinstance(repo, dict) else "",
            "stars": repo.get("stars", 0) if isinstance(repo, dict) else 0,
            "area": r.get("area", ""),
            "task": r.get("task", paper.get("tasks", [])),
        }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_channel: PapersWithCodeChannel | None = None


def get_channel() -> PapersWithCodeChannel:
    global _channel
    if _channel is None:
        _channel = PapersWithCodeChannel()
    return _channel