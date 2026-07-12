"""CrossRef channel — the universal DOI registry, covers every publisher.

CrossRef is the registration authority for DOIs across virtually every
academic publisher: IEEE, ACM, Springer Nature, Elsevier, Wiley, Taylor &
Francis, Sage, Oxford University Press, Cambridge University Press, APA,
AMA, and 15,000+ smaller publishers. If a journal article has a DOI,
it's in CrossRef.

API: https://api.crossref.org  — free, no key, polite pool with mailto.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

CROSSREF_API = "https://api.crossref.org/works"

# Publisher code → human label, used by the smart dispatcher to detect
# when a query names a specific publisher ("search IEEE Xplore", "find
# in Springer").
PUBLISHER_PREFIXES = {
    "10.1109": "IEEE",
    "10.1145": "ACM",
    "10.1007": "Springer",
    "10.1016": "Elsevier",
    "10.1038": "Nature",
    "10.1093": "Oxford University Press",
    "10.1111": "Wiley",
    "10.1080": "Taylor & Francis",
    "10.1126": "AAAS (Science)",
    "10.1371": "PLOS",
    "10.3390": "MDPI",
    "10.1177": "Sage",
    "10.1063": "AIP",
    "10.1364": "Optica",
    "10.1073": "PNAS",
    "10.21105": "Open Journals (JOSS, etc.)",
}


class CrossRefChannel:
    """CrossRef DOI/metadata search — universal publisher coverage.

    Best for:
      - DOI lookup (resolve a DOI → full metadata)
      - Funder queries (papers funded by a specific grant/org)
      - Finding the canonical version of a preprint (ISSN/type filtering)
      - Citation lookups by bibliographic fields
    """

    def __init__(self, mailto: str = "") -> None:
        env_mailto = __import__("os").environ.get("NEXUS_CROSSREF_MAILTO", "")
        self._mailto = mailto or env_mailto or "nexus-research@example.com"
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": f"NEXUS/1.0 (mailto:{self._mailto})"},
            )
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 10,
        filter_type: str = "",
        sort: str = "relevance",
        order: str = "desc",
    ) -> list[dict]:
        """Search CrossRef works by bibliographic terms.

        Args:
            query: free-text bibliographic query (title/author/abstract)
            max_results: number of results (max 100)
            filter_type: CrossRef filter, e.g. 'type:journal-article',
                         'from-pub-date:2024-01-01', 'container-title:...'.
                         Multiple filters comma-separated.
            sort: 'relevance' (default), 'published', 'is-referenced-by-count',
                  'submitted'
            order: 'asc' or 'desc'

        Returns:
            list of normalized paper dicts with title, authors, abstract,
            year, doi, container_title, publisher, citation_count, url.
        """
        params: dict[str, Any] = {
            "query": query,
            "rows": min(max_results, 100),
            "sort": sort,
            "order": order,
        }
        if filter_type:
            params["filter"] = filter_type

        for attempt in range(3):
            try:
                resp = await self.client.get(CROSSREF_API, params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                items = data.get("message", {}).get("items", [])
                return [self._normalize(it) for it in items]
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                return []
        return []

    async def lookup_doi(self, doi: str) -> dict | None:
        """Fetch full metadata for a specific DOI."""
        doi = doi.replace("https://doi.org/", "").replace("doi:", "")
        try:
            resp = await self.client.get(f"{CROSSREF_API}/{doi}")
            resp.raise_for_status()
            msg = resp.json().get("message", {})
            return self._normalize(msg)
        except Exception:
            return None

    async def search_by_publisher(
        self, query: str, prefix: str, max_results: int = 10
    ) -> list[dict]:
        """Search within a specific publisher via DOI prefix.

        Use PUBLISHER_PREFIXES keys (e.g. '10.1109' for IEEE).
        """
        return await self.search(
            query, max_results=max_results,
            filter_type=f"prefix:{prefix},type:journal-article",
        )

    def _normalize(self, it: dict) -> dict:
        title_list = it.get("title") or []
        title = title_list[0] if title_list else ""

        abstract = it.get("abstract", "")
        # CrossRef wraps abstracts in <jats:p> tags — strip for plain text
        if abstract:
            import re
            abstract = re.sub(r"<[^>]+>", "", abstract)[:3000]

        authors = []
        for a in it.get("author", []):
            given = a.get("given", "")
            family = a.get("family", "")
            name = f"{given} {family}".strip()
            if name:
                authors.append(name)

        container_list = it.get("container-title") or []
        container = container_list[0] if container_list else ""

        # Extract year from published date parts
        year = None
        date_parts = (it.get("published-print") or it.get("published-online")
                      or it.get("issued") or {}).get("date-parts", [[None]])
        if date_parts and date_parts[0]:
            year = date_parts[0][0]

        publisher = it.get("publisher", "")
        doi = it.get("DOI", "")

        # Infer publisher label from DOI prefix
        publisher_label = ""
        for prefix, label in PUBLISHER_PREFIXES.items():
            if doi.startswith(prefix):
                publisher_label = label
                break

        references = it.get("reference", [])
        references_count = it.get("references-count", len(references))

        return {
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "year": year,
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else "",
            "container_title": container,
            "publisher": publisher or publisher_label,
            "publisher_label": publisher_label,
            "type": it.get("type", ""),
            "citation_count": it.get("is-referenced-by-count", 0),
            "reference_count": references_count,
            "issn": it.get("ISSN", []),
            "subject": it.get("subject", []),
        }


_channel: CrossRefChannel | None = None


def get_channel() -> CrossRefChannel:
    global _channel
    if _channel is None:
        _channel = CrossRefChannel()
    return _channel
