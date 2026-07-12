"""Google Scholar channel — scholarly search via Scrapling page fetch.

Google Scholar has no official API. This channel scrapes the HTML search
results page (no JavaScript required for the results page) and extracts
paper titles, authors, snippets, and links.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

SCHOLAR_URL = "https://scholar.google.com/scholar"


class GoogleScholarChannel:
    """Google Scholar search channel (HTML scrape)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict]:
        """Search Google Scholar.

        Returns list of dicts with title, authors, snippet, url, year,
        citation_count (if available).
        """
        params = {"q": query, "hl": "en", "num": min(max_results, 20)}
        try:
            resp = await self.client.get(SCHOLAR_URL, params=params)
            resp.raise_for_status()
            return self._parse_html(resp.text, max_results)
        except Exception:
            return []

    def _parse_html(self, html: str, max_results: int) -> list[dict]:
        """Parse Google Scholar results HTML."""
        results: list[dict] = []

        # Modern Scholar uses data-cid on result divs. Try multiple patterns.
        # Pattern 1: data-cid blocks
        blocks = re.findall(
            r'<div class="gs_r gs_or gs_scl"[^>]*>(.*?)(?=<div class="gs_r gs_or gs_scl"|<div id="gs_nml"|$)',
            html,
            re.DOTALL,
        )
        if not blocks:
            # Pattern 2: gs_erk blocks (newer layout)
            blocks = re.findall(
                r'<div class="gs_erk"[^>]*>(.*?)(?=<div class="gs_erk"|<div id="gs_nml"|<div class="gs_r|$)',
                html,
                re.DOTALL,
            )
        if not blocks:
            # Pattern 3: any div containing an h3 with class gs_rt
            blocks = re.findall(
                r'(<h3[^>]*class="gs_rt"[^>]*>.*?</h3>.*?)(?=<h3[^>]*class="gs_rt"|$)',
                html,
                re.DOTALL,
            )

        for block in blocks[:max_results]:
            # Title + link
            title_match = re.search(r'<h3[^>]*class="gs_rt"[^>]*>\s*(?:<[^>]+>)*\s*(.*?)\s*</h3>', block, re.DOTALL)
            link_match = re.search(r'<a[^>]*href="(https?://[^"]*)"[^>]*>', block)
            # Authors + year
            author_match = re.search(r'<div class="gs_a">(.*?)</div>', block, re.DOTALL)
            # Snippet
            snippet_match = re.search(r'<div class="gs_rs">(.*?)</div>', block, re.DOTALL)

            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""
            if not title:
                continue
            url = link_match.group(1) if link_match else ""
            authors_line = re.sub(r"<[^>]+>", "", author_match.group(1)).strip() if author_match else ""
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip() if snippet_match else ""

            # Parse "Authors - Journal, Year - Publisher"
            parts = authors_line.split(" - ")
            authors = parts[0] if parts else ""
            year = ""
            if len(parts) >= 2:
                year_match = re.search(r"\b(20\d{2}|19\d{2})\b", parts[1])
                year = year_match.group(1) if year_match else ""

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "snippet": snippet[:500],
                "url": url,
                "source": "google_scholar",
            })
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_channel: GoogleScholarChannel | None = None


def get_channel() -> GoogleScholarChannel:
    global _channel
    if _channel is None:
        _channel = GoogleScholarChannel()
    return _channel