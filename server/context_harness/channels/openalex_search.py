"""OpenAlex channel — the free, open successor to Microsoft Academic Graph.

Covers 250M+ works from EVERY academic publisher: Springer, Elsevier, Wiley,
Taylor & Francis, Sage, Nature, IEEE, ACM, SSRN, NBER, and thousands more.
This is the single highest-leverage channel for journal/monograph coverage.

API: https://docs.openalex.org/  — fully free, no API key required (polite pool
with mailto gets you 100k requests/day). Returns works with concepts,
institutions, authors, citation counts, open access URLs, referenced_works.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

OPENALEX_API = "https://api.openalex.org/works"

# OpenAlex concept/venue → NEXUS domain cluster hint. Used by the smart
# dispatcher to decide when OpenAlex is the right channel for a query.
STRONG_DOMAINS = {
    "mathematics", "computer science", "economics", "finance",
    "statistics", "engineering", "physics", "biology", "medicine",
    "psychology", "sociology", "political science", "linguistics",
    "philosophy", "chemistry", "materials science", "environmental science",
    "business", "management", "law", "education", "geography",
}


class OpenAlexChannel:
    """OpenAlex academic search — universal journal/publisher coverage.

    Distinct from Semantic Scholar: OpenAlex has broader coverage of non-cs
    fields (humanities, social science, finance via SSRN/NBER ingest, law,
    medicine via PubMed ingest). S2 is stronger for CS citation graphs.
    """

    def __init__(self, mailto: str = "") -> None:
        # Polite-pool mailto — gives 100k req/day instead of 10k. Optional.
        env_mailto = __import__("os").environ.get("NEXUS_OPENALEX_MAILTO", "")
        self._mailto = mailto or env_mailto or "nexus-research@example.com"
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                params={"mailto": self._mailto},
            )
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 10,
        sort: str = "relevance_score:desc",
        filter_type: str = "",
    ) -> list[dict]:
        """Search OpenAlex works.

        Args:
            query: full-text search terms
            max_results: number of results (max 200/page)
            sort: OpenAlex sort field, e.g. 'relevance_score:desc',
                  'cited_by_count:desc', 'publication_date:desc'
            filter_type: optional OpenAlex filter, e.g. 'type:journal-article',
                         'type:preprint', 'from_publication_date:2024-01-01',
                         'primary_location.source.type:journal',
                         'locations.source.host_organization:V4310320990' (SSRN)

        Returns:
            list of normalized paper dicts with title, authors, abstract,
            year, citation_count, doi, open_access_url, venue, concepts, type.
        """
        params: dict[str, Any] = {
            "search": query,
            "per-page": min(max_results, 200),
            "sort": sort,
        }
        if filter_type:
            params["filter"] = filter_type

        for attempt in range(3):
            try:
                resp = await self.client.get(OPENALEX_API, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                return [self._normalize(w) for w in results]
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                return []
        return []

    async def search_ssrn(self, query: str, max_results: int = 10) -> list[dict]:
        """Search specifically within SSRN-hosted preprints.

        SSRN (Social Science Research Network) is the primary preprint server
        for finance, economics, law, and accounting research — the standard
        venue for working papers in these fields before journal publication.
        """
        return await self.search(
            query, max_results=max_results,
            filter_type="primary_location.source.id:S4306419645",
        )

    async def search_journal_only(self, query: str, max_results: int = 10) -> list[dict]:
        """Restrict to peer-reviewed journal articles (excludes preprints)."""
        return await self.search(
            query, max_results=max_results,
            filter_type="type:journal-article",
        )

    async def search_recent(self, query: str, days: int = 90, max_results: int = 10) -> list[dict]:
        """Search only works from the last N days."""
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return await self.search(
            query, max_results=max_results,
            filter_type=f"from_publication_date:{cutoff}",
            sort="publication_date:desc",
        )

    async def get_referenced_works(self, openalex_id: str, max_results: int = 10) -> list[dict]:
        """Fetch the papers that a given work cites (its reference list).

        Useful for citation graph traversal in deep_research.
        """
        try:
            resp = await self.client.get(
                f"{OPENALEX_API}/{openalex_id}",
                params={"select": "referenced_works"},
            )
            resp.raise_for_status()
            data = resp.json()
            ref_ids = data.get("referenced_works", [])[:max_results]
            if not ref_ids:
                return []
            # Fetch each referenced work (OpenAlex supports pipe-separated IDs)
            ids_param = "|".join(ref_ids)
            resp2 = await self.client.get(
                OPENALEX_API,
                params={"filter": f"openalex:{ids_param}", "per-page": len(ref_ids)},
            )
            resp2.raise_for_status()
            return [self._normalize(w) for w in resp2.json().get("results", [])]
        except Exception:
            return []

    async def get_cited_by(self, openalex_id: str, max_results: int = 10) -> list[dict]:
        """Fetch papers that cite the given work."""
        try:
            resp = await self.client.get(
                OPENALEX_API,
                params={
                    "filter": f"cites:{openalex_id}",
                    "per-page": max_results,
                    "sort": "cited_by_count:desc",
                },
            )
            resp.raise_for_status()
            return [self._normalize(w) for w in resp.json().get("results", [])]
        except Exception:
            return []

    def _normalize(self, w: dict) -> dict:
        # Abstract reconstruction: OpenAlex returns inverted index
        abstract = ""
        ai = w.get("abstract_inverted_index")
        if isinstance(ai, dict):
            positions: list[tuple[int, str]] = []
            for word, idxs in ai.items():
                for i in idxs:
                    positions.append((i, word))
            positions.sort()
            abstract = " ".join(word for _, word in positions)

        authors = []
        for a in w.get("authorships", [])[:20]:
            author = a.get("author", {})
            name = author.get("display_name", "")
            if name:
                authors.append(name)

        # Best OA / landing URL
        best_oa = ""
        oa = w.get("open_access", {})
        if isinstance(oa, dict):
            best_oa = oa.get("oa_url", "") or ""

        primary = w.get("primary_location") or {}
        venue = ""
        source = primary.get("source") or {}
        if isinstance(source, dict):
            venue = source.get("display_name", "")

        # Top concepts
        concepts = []
        for c in w.get("concepts", [])[:5]:
            if isinstance(c, dict):
                concepts.append(c.get("display_name", ""))

        # Type (journal-article, preprint, book-chapter, etc.)
        work_type = w.get("type", "")
        host_type = ""
        if isinstance(source, dict):
            host_type = source.get("type", "")

        return {
            "title": w.get("title", "") or w.get("display_name", ""),
            "abstract": abstract[:3000],
            "authors": authors,
            "year": w.get("publication_year"),
            "citation_count": w.get("cited_by_count", 0),
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "openalex_id": w.get("id", "").replace("https://openalex.org/", ""),
            "open_access_url": best_oa,
            "venue": venue,
            "venue_type": host_type,
            "concepts": concepts,
            "type": work_type,
            "is_oa": bool(oa.get("is_oa")) if isinstance(oa, dict) else False,
            "url": best_oa or w.get("doi", ""),
        }


_channel: OpenAlexChannel | None = None


def get_channel() -> OpenAlexChannel:
    global _channel
    if _channel is None:
        _channel = OpenAlexChannel()
    return _channel
