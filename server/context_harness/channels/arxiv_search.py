"""arXiv channel — search papers, fetch abstracts, extract metadata.

Uses the official arXiv API (http://export.arxiv.org/api/query) which is
free, requires no API key, and returns Atom XML. Up to 50 results per call.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

try:
    from ..rate_limited_http import RateLimitedClient, create_arxiv_client
except ImportError:
    RateLimitedClient = None  # type: ignore[assignment]
    create_arxiv_client = None

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    published: str
    updated: str
    pdf_url: str
    abs_url: str
    comment: str = ""

    def to_dict(self) -> dict:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract[:2000],
            "categories": self.categories,
            "published": self.published,
            "pdf_url": self.pdf_url,
            "abs_url": self.abs_url,
        }


class ArxivChannel:
    """arXiv API search channel with optional rate limiting (1 req/3s)."""

    def __init__(self, rate_limiter: "object | None" = None) -> None:
        self._client: httpx.AsyncClient | None = None
        self._limiter = rate_limiter
        if self._limiter is None and create_arxiv_client is not None:
            self._limiter = create_arxiv_client()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _rate_limit(self) -> None:
        """Wait for rate limit token before API call."""
        if self._limiter is not None and hasattr(self._limiter, '_acquire'):
            await self._limiter._acquire()

    async def search(
        self,
        query: str,
        max_results: int = 10,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> list[dict]:
        """Search arXiv for papers matching *query*.

        Args:
            query: search terms (e.g. "Thompson Sampling regret bounds")
            max_results: number of results (max 50)
            sort_by: "relevance", "lastUpdatedDate", or "submittedDate"
            sort_order: "ascending" or "descending"

        Returns:
            list of paper dicts with arxiv_id, title, authors, abstract, etc.
        """
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(max_results, 50),
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        resp = await self.client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        return self._parse_atom(resp.text)

    async def fetch_abstract(self, arxiv_id: str) -> dict | None:
        """Fetch a single paper by arXiv ID."""
        await self._rate_limit()
        resp = await self.client.get(ARXIV_API, params={"id_list": arxiv_id})
        resp.raise_for_status()
        papers = self._parse_atom(resp.text)
        return papers[0] if papers else None

    async def search_category(
        self, category: str, max_results: int = 10
    ) -> list[dict]:
        """Search by arXiv category (e.g. cs.AI, stat.ML, math.OC)."""
        params = {
            "search_query": f"cat:{category}",
            "max_results": min(max_results, 50),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        resp = await self.client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        return self._parse_atom(resp.text)

    def _parse_atom(self, xml_text: str) -> list[dict]:
        """Parse arXiv Atom XML response."""
        papers: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return papers

        for entry in root.findall("atom:entry", ARXIV_NS):
            arxiv_url = entry.find("atom:id", ARXIV_NS)
            if arxiv_url is None:
                continue
            full_id = arxiv_url.text or ""
            arxiv_id = full_id.split("/abs/")[-1] if "/abs/" in full_id else full_id

            title_el = entry.find("atom:title", ARXIV_NS)
            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""

            summary_el = entry.find("atom:summary", ARXIV_NS)
            abstract = (summary_el.text or "").strip() if summary_el is not None else ""

            authors = []
            for author in entry.findall("atom:author", ARXIV_NS):
                name_el = author.find("atom:name", ARXIV_NS)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            published_el = entry.find("atom:published", ARXIV_NS)
            published = published_el.text if published_el is not None else ""

            updated_el = entry.find("atom:updated", ARXIV_NS)
            updated = updated_el.text if updated_el is not None else ""

            pdf_url = ""
            abs_url = ""
            for link in entry.findall("atom:link", ARXIV_NS):
                href = link.get("href", "")
                if link.get("title") == "pdf" or href.endswith(".pdf"):
                    pdf_url = href
                elif link.get("type") == "text/html":
                    abs_url = href
            if not abs_url:
                abs_url = f"https://arxiv.org/abs/{arxiv_id}"
            if not pdf_url:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            categories = [s.get("term", "") for s in entry.findall("{http://arxiv.org/schemas/atom}primary_category")]
            categories += [s.get("term", "") for s in entry.findall("atom:category", ARXIV_NS)]
            categories = [c for c in categories if c]

            comment_el = entry.find("{http://arxiv.org/schemas/atom}comment")
            comment = comment_el.text if comment_el is not None else ""

            papers.append(ArxivPaper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                categories=categories,
                published=published,
                updated=updated,
                pdf_url=pdf_url,
                abs_url=abs_url,
                comment=comment,
            ).to_dict())
        return papers

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_channel: ArxivChannel | None = None


def get_channel() -> ArxivChannel:
    global _channel
    if _channel is None:
        _channel = ArxivChannel()
    return _channel