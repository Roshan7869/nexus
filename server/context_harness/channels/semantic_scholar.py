"""Semantic Scholar channel — academic paper search via S2 API.

Free API (no key required for low volume, rate limit 1 req/sec without key).
Returns paper metadata including citation counts, references, and TLDRs.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

S2_API = "https://api.semanticscholar.org/graph/v1"
S2_SEARCH = f"{S2_API}/paper/search"
S2_PAPER = f"{S2_API}/paper"


class SemanticScholarChannel:
    """Semantic Scholar academic search channel."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._api_key:
                headers["x-api-key"] = self._api_key
            self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 10,
        fields: str = "title,abstract,authors,year,citationCount,url,externalIds,tldr,fieldsOfStudy",
    ) -> list[dict]:
        """Search Semantic Scholar for papers.

        Returns list of dicts with title, abstract, authors, year,
        citationCount, url, externalIds (DOI/arxiv), tldr, fieldsOfStudy.
        """
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": fields,
        }
        for attempt in range(3):
            try:
                resp = await self.client.get(S2_SEARCH, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                papers = data.get("data", [])
                return [self._normalize(p) for p in papers]
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                return []
        return []

    async def get_paper(self, paper_id: str) -> dict | None:
        """Fetch a single paper by S2 paper ID, DOI, or arXiv ID."""
        try:
            resp = await self.client.get(
                f"{S2_PAPER}/{paper_id}",
                params={"fields": "title,abstract,authors,year,citationCount,url,externalIds,tldr,references.title"},
            )
            resp.raise_for_status()
            return self._normalize(resp.json())
        except Exception:
            return None

    async def get_citations(self, paper_id: str, max_results: int = 20) -> list[dict]:
        """Get papers that cite *paper_id*."""
        try:
            resp = await self.client.get(
                f"{S2_PAPER}/{paper_id}/citations",
                params={"fields": "title,year,authors,citationCount", "limit": min(max_results, 100)},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [
                {
                    "title": c.get("title", ""),
                    "year": c.get("year"),
                    "authors": [a.get("name", "") for a in c.get("authors", [])],
                    "citation_count": c.get("citationCount", 0),
                }
                for c in data
            ]
        except Exception:
            return []

    def _normalize(self, p: dict) -> dict:
        authors = [a.get("name", "") for a in p.get("authors", [])]
        tldr = p.get("tldr")
        tldr_text = tldr.get("text", "") if isinstance(tldr, dict) else ""
        external = p.get("externalIds", {}) or {}
        return {
            "title": p.get("title", ""),
            "abstract": p.get("abstract", "") or tldr_text,
            "authors": authors,
            "year": p.get("year"),
            "citation_count": p.get("citationCount", 0),
            "url": p.get("url", ""),
            "doi": external.get("DOI", ""),
            "arxiv_id": external.get("ArXiv", ""),
            "tldr": tldr_text,
            "fields_of_study": p.get("fieldsOfStudy", []),
        }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_channel: SemanticScholarChannel | None = None


def get_channel() -> SemanticScholarChannel:
    global _channel
    if _channel is None:
        _channel = SemanticScholarChannel()
    return _channel