"""Scrapling Fetch Channel — Phase 1.

Wraps Scrapling's Fetcher as an MCP-callable backend with:
  - Auto mode fallback: static → dynamic → stealth
  - Response-to-text extraction pipeline
  - Batch fetch with concurrency control
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapling import Fetcher

logger = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    """Strip HTML tags, extract visible text, normalize whitespace."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self._parts.append(cleaned)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def extract_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text


def extract_links(html: str, base_url: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for m in re.finditer(r'href=["\'](.*?)["\']', html, re.IGNORECASE):
        href = m.group(1)
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme in ("http", "https") and abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    return links


def extract_meta(html: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for m in re.finditer(
        r'<meta\s+[^>]*?(?:name|property)=["\']([^"\']+)["\'][^>]*?content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    ):
        key = m.group(1).strip().lower().replace("og:", "og_").replace(":", "_")
        meta[key] = m.group(2).strip()
    # Title fallback
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if t:
        meta.setdefault("title", t.group(1).strip())
    return meta


# ── Fetch Wrapper ──────────────────────────────────────────────────────────


class ScraplingFetchChannel:
    """Wraps Scrapling's Fetcher with auto-fallback and extraction pipeline."""

    def __init__(self) -> None:
        self._fetcher: Fetcher | None = None
        self._lock = asyncio.Lock()

    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = Fetcher()
        return self._fetcher

    async def fetch(
        self,
        url: str,
        mode: str = "auto",
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Fetch a URL with auto-fallback through fetch modes.

        Args:
            url: target URL
            mode: 'auto' (static→dynamic→stealth), 'static', 'dynamic', 'stealth'
            timeout: request timeout seconds

        Returns:
            dict with url, title, text_content, links, meta, status, fetch_mode, latency_ms
        """
        modes = ["static", "dynamic", "stealth"]
        if mode != "auto":
            modes = [mode]

        last_error: str | None = None
        for attempt_mode in modes:
            try:
                started = time.time()
                resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda m=attempt_mode: self._do_fetch(url, m, timeout),
                )
                latency = int((time.time() - started) * 1000)

                if resp is None:
                    last_error = f"{attempt_mode} returned None"
                    continue

                return self._parse_response(url, resp, attempt_mode, latency)

            except Exception as e:
                last_error = f"{attempt_mode}: {e}"
                logger.warning("Fetch %s mode=%s failed: %s", url, attempt_mode, e)
                continue

        return {
            "url": url,
            "success": False,
            "error": last_error or "all modes failed",
            "fetch_mode": "failed",
            "latency_ms": 0,
        }

    def _do_fetch(self, url: str, mode: str, timeout: float) -> Any:
        f = self.fetcher
        if mode == "dynamic":
            from scrapling import DynamicFetcher
            return DynamicFetcher().get(url, timeout=timeout)
        if mode == "stealth":
            from scrapling.fetchers.stealth_chrome import StealthyFetcher
            return StealthyFetcher().get(url, timeout=timeout)
        return f.get(url, timeout=timeout)

    def _parse_response(
        self, url: str, resp: Any, mode: str, latency: int
    ) -> dict[str, Any]:
        html = resp.body if hasattr(resp, "body") else (resp.text or "")
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")

        title = ""
        t = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if t:
            title = t.group(1).strip()

        return {
            "url": url,
            "success": True,
            "title": title,
            "text_content": extract_text(html),
            "links": extract_links(html, url),
            "meta": extract_meta(html),
            "status": getattr(resp, "status_code", 200),
            "fetch_mode": mode,
            "latency_ms": latency,
        }


# ── Singleton ──────────────────────────────────────────────────────────────

_channel: ScraplingFetchChannel | None = None


def get_channel() -> ScraplingFetchChannel:
    global _channel
    if _channel is None:
        _channel = ScraplingFetchChannel()
    return _channel
