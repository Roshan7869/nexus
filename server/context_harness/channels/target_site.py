"""Target-site harvester — fetch + extract context from specific project/tech websites.

Unlike the academic channels (which search paper databases), this channel
takes a list of TARGET URLs (documentation sites, project wikis, tech blogs,
API references, changelogs) and extracts clean structured context from each.

It builds on the existing Scrapling fetch channel but adds:
  - Domain-aware extraction rules (GitHub README, docs sites, blogs, RFCs)
  - Boilerplate removal (nav, footer, ads, cookie banners)
  - Per-domain content selectors (learned + fallback)
  - Batch-concurrent fetching with per-domain rate limiting
  - Output shaping for LLM context windows

Use cases:
  - "Study the Rust async book" → harvest https://rust-lang.org/async-book
  - "Get the latest FastAPI docs" → harvest specific doc sections
  - "Read the Linear engineering blog" → harvest blog posts
  - "Study the k8s architecture docs" → harvest k8s.io docs
"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

import httpx

def _load_domain_rules() -> dict[str, dict]:
    try:
        from server.site_registry import get_domain_rules
        rules = get_domain_rules()
        if rules:
            return rules
    except Exception:
        pass
    return _BUILTIN_DOMAIN_RULES


# Per-domain extraction rules. Keyed by domain suffix. Each rule defines
# the CSS-like selectors or heuristics for extracting the main content.
# Falls back to generic boilerplate removal when no rule matches.
_BUILTIN_DOMAIN_RULES: dict[str, dict] = {
    "github.com": {
        "type": "github_readme",
        "content_hint": "article.markdown-body, div[role='main']",
    },
    "raw.githubusercontent.com": {
        "type": "raw_markdown",
    },
    "developer.mozilla.org": {
        "type": "mdn",
        "content_hint": "article.main-page-content, main#content",
    },
    "docs.python.org": {
        "type": "sphinx_docs",
        "content_hint": "div.body, div.document, section",
    },
    "rust-lang.org": {
        "type": "mdbook",
        "content_hint": "main, article#content",
    },
    "kubernetes.io": {
        "type": "hugo_docs",
        "content_hint": "div.content, main td-content",
    },
    "fastapi.tiangolo.com": {
        "type": "mkdocs_material",
        "content_hint": "div.md-content, article.md-content__inner",
    },
    "systemdesign.one": {"type": "blog"},
    "martinfowler.com": {"type": "blog"},
    "bytebytego.com": {"type": "blog"},
}

DOMAIN_RULES: dict[str, dict] = _load_domain_rules()


class TargetSiteChannel:
    """Harvest context from specific project/tech documentation sites."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request: dict[str, float] = {}  # per-domain rate limiting
        self._min_domain_interval = 1.0  # 1s between requests to same domain

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "User-Agent": "NEXUS-Harvester/1.0 (+context research)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
            )
        return self._client

    async def _domain_rate_limit(self, domain: str) -> None:
        """Enforce per-domain politeness (1 req/sec/domain)."""
        import time
        last = self._last_request.get(domain, 0.0)
        wait = self._min_domain_interval - (time.time() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request[domain] = time.time()

    def _get_rule(self, url: str) -> dict:
        rules = _load_domain_rules()
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Strip www. prefix
        domain = domain.removeprefix("www.")
        # Check exact match, then suffix match
        if domain in rules:
            return rules[domain]
        for key, rule in rules.items():
            if domain.endswith(key):
                return rule
        return {"type": "generic"}

    async def fetch_page(self, url: str) -> dict:
        """Fetch a single page and extract clean context.

        Returns dict with:
          url, domain, title, text_content, html, status, fetch_mode,
          rule_type, word_count, links (list of related URLs on the page).
        """
        domain = urlparse(url).netloc
        await self._domain_rate_limit(domain)

        rule = self._get_rule(url)
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            html = resp.text
            status = resp.status_code
        except Exception as e:
            return {
                "url": url, "domain": domain, "status": 0, "error": str(e),
                "text_content": "", "word_count": 0,
            }

        title = self._extract_title(html)
        text = self._extract_content(html, rule)
        links = self._extract_links(html, url)

        return {
            "url": url,
            "domain": domain,
            "title": title,
            "text_content": text,
            "html": html,  # kept for pattern extraction if needed
            "status": status,
            "rule_type": rule.get("type", "generic"),
            "word_count": len(text.split()),
            "links": links,
        }

    async def fetch_batch(
        self,
        urls: list[str],
        max_concurrent: int = 5,
    ) -> list[dict]:
        """Fetch multiple URLs concurrently with per-domain rate limiting."""
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(u: str) -> dict:
            async with sem:
                return await self.fetch_page(u)

        tasks = [_one(u) for u in urls]
        return await asyncio.gather(*tasks)

    async def harvest_site(
        self,
        seed_url: str,
        max_pages: int = 20,
        same_domain: bool = True,
        depth: int = 2,
    ) -> dict:
        """Breadth-first crawl from a seed URL, extracting context from each page.

        Args:
            seed_url: starting URL
            max_pages: total pages to crawl
            same_domain: restrict to seed domain
            depth: max BFS depth (1 = seed only, 2 = seed + links, 3 = deeper)

        Returns:
            dict with seed_url, pages_crawled, total_words, results (list).
        """
        seed_domain = urlparse(seed_url).netloc
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(seed_url, 0)]
        all_results: list[dict] = []

        while queue and len(all_results) < max_pages:
            batch: list[tuple[str, int]] = []
            while queue and len(batch) < 5:
                url, d = queue.pop(0)
                if url in visited:
                    continue
                if d > depth:
                    continue
                visited.add(url)
                batch.append((url, d))

            if not batch:
                continue

            results = await self.fetch_batch([u for u, _ in batch])
            for (url, d), result in zip(batch, results):
                if result.get("text_content"):
                    all_results.append(result)
                    # Enqueue links from this page
                    if d < depth:
                        for link in result.get("links", [])[:10]:
                            if same_domain and urlparse(link).netloc != seed_domain:
                                continue
                            if link not in visited:
                                queue.append((link, d + 1))

        total_words = sum(r.get("word_count", 0) for r in all_results)
        return {
            "seed_url": seed_url,
            "pages_crawled": len(all_results),
            "total_words": total_words,
            "results": all_results,
        }

    def _extract_title(self, html: str) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
        # H1 fallback
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return ""

    def _extract_content(self, html: str, rule: dict) -> str:
        """Extract main text content using rule type + generic fallback."""
        rule_type = rule.get("type", "generic")

        # Try to use the Scrapling fetch channel for better extraction
        try:
            return self._extract_with_heuristics(html, rule_type)
        except Exception:
            return self._crude_text_extraction(html)

    def _extract_with_heuristics(self, html: str, rule_type: str) -> str:
        """Heuristic content extraction — remove boilerplate, keep main content."""
        # Remove obvious non-content blocks
        for pattern in [
            r"<script[^>]*>.*?</script>",
            r"<style[^>]*>.*?</style>",
            r"<nav[^>]*>.*?</nav>",
            r"<footer[^>]*>.*?</footer>",
            r"<header[^>]*>.*?</header>",
            r"<aside[^>]*>.*?</aside>",
            r"<noscript[^>]*>.*?</noscript>",
            r"<!--.*?-->",
        ]:
            html = re.sub(pattern, "", html, flags=re.DOTALL | re.IGNORECASE)

        # Type-specific main-content extraction
        content_patterns: list[str] = []
        if rule_type in ("mdbook", "hugo_docs", "mkdocs_material", "sphinx_docs"):
            content_patterns = [
                r"<main[^>]*>(.*?)</main>",
                r'<article[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</article>',
                r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            ]
        elif rule_type == "github_readme":
            content_patterns = [
                r'<div[^>]*class="[^"]*markdown-body[^"]*"[^>]*>(.*?)</div>',
                r"<article[^>]*>(.*?)</article>",
            ]
        elif rule_type == "mdn":
            content_patterns = [r'<article[^>]*>(.*?)</article>']
        else:
            # Generic: try article tag, then main, then body
            content_patterns = [
                r"<article[^>]*>(.*?)</article>",
                r"<main[^>]*>(.*?)</main>",
                r"<body[^>]*>(.*?)</body>",
            ]

        for pat in content_patterns:
            m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
            if m:
                return self._html_to_text(m.group(1))

        return self._html_to_text(html)

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text, preserving structure."""
        # Preserve paragraph/heading breaks
        html = re.sub(r"<(p|div|h[1-6]|li|tr|br)[^>]*>", "\n", html, flags=re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", html)
        # Decode common HTML entities
        for entity, char in [
            ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
            ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "),
            ("&mdash;", "—"), ("&ndash;", "–"), ("&hellip;", "…"),
        ]:
            text = text.replace(entity, char)
        # Collapse whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _crude_text_extraction(self, html: str) -> str:
        return self._html_to_text(html)

    def _extract_links(self, html: str, base_url: str) -> list[str]:
        """Extract and normalize links from the page."""
        from urllib.parse import urljoin
        raw = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
        links: list[str] = []
        seen: set[str] = set()
        for href in raw:
            if href.startswith(("#", "mailto:", "javascript:", "data:")):
                continue
            full = urljoin(base_url, href.split("#")[0])
            if full not in seen:
                seen.add(full)
                links.append(full)
        return links

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_channel: TargetSiteChannel | None = None


def get_channel() -> TargetSiteChannel:
    global _channel
    if _channel is None:
        _channel = TargetSiteChannel()
    return _channel
