"""Scrapling Spider Channel — Phase 4.

Wraps Scrapling's CrawlerEngine as a managed async spider backend.
Provides start/status/results lifecycle for background crawls.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

STALE_SESSION_HOURS = 1


class SpiderSession:
    """A single crawl session wrapping Scrapling's CrawlerEngine."""

    def __init__(
        self,
        spider_id: str,
        start_url: str,
        max_pages: int = 50,
        same_domain: bool = True,
        download_delay: float = 1.0,
        obey_robots_txt: bool = True,
    ) -> None:
        self.spider_id = spider_id
        self.start_url = start_url
        self.max_pages = max_pages
        self.same_domain = same_domain
        self.download_delay = download_delay
        self.obey_robots_txt = obey_robots_txt
        self.status = "created"
        self.pages_crawled: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._started_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spider_id": self.spider_id,
            "start_url": self.start_url,
            "status": self.status,
            "pages_crawled": len(self.pages_crawled),
            "max_pages": self.max_pages,
            "error_count": len(self.errors),
            "uptime_seconds": round(time.time() - (self._started_at or time.time()), 1),
        }


class ScraplingSpiderChannel:
    """Manages spider sessions using Scrapling's CrawlerEngine."""

    def __init__(self) -> None:
        self._sessions: dict[str, SpiderSession] = {}
        self._lock = asyncio.Lock()

    async def start_spider(
        self,
        url: str,
        max_pages: int = 50,
        same_domain: bool = True,
        download_delay: float = 1.0,
        obey_robots_txt: bool = True,
    ) -> dict[str, Any]:
        """Start a new spider crawl in background.

        Args:
            url: seed URL
            max_pages: max pages to crawl
            same_domain: stay on same domain
            download_delay: seconds between requests
            obey_robots_txt: respect robots.txt

        Returns:
            spider session info
        """
        await self._cleanup_stale()
        spider_id = f"spider-{uuid.uuid4().hex[:12]}"
        session = SpiderSession(
            spider_id=spider_id,
            start_url=url,
            max_pages=max_pages,
            same_domain=same_domain,
            download_delay=download_delay,
            obey_robots_txt=obey_robots_txt,
        )
        session.status = "running"
        session._started_at = time.time()

        async with self._lock:
            self._sessions[spider_id] = session

        asyncio.create_task(self._run(spider_id))
        return session.to_dict()

    async def _run(self, spider_id: str) -> None:
        """Background crawl loop."""
        import httpx

        session = self._sessions.get(spider_id)
        if not session:
            return

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        queue: list[str] = [session.start_url]
        seen: set[str] = {session.start_url}

        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=30.0
            ) as client:
                while queue and len(session.pages_crawled) < session.max_pages:
                    url = queue.pop(0)
                    if session.pages_crawled:
                        await asyncio.sleep(session.download_delay)

                    try:
                        resp = await client.get(url)
                        page: dict[str, Any] = {
                            "url": str(resp.url),
                            "status": resp.status_code,
                            "content_length": len(resp.text),
                            "text_preview": resp.text[:2000],
                            "crawled_at": time.time(),
                        }
                        session.pages_crawled.append(page)

                        # Extract links for further crawling
                        if len(session.pages_crawled) < session.max_pages:
                            import re

                            for m in re.finditer(
                                r'href=["\'](.*?)["\']', resp.text, re.IGNORECASE
                            ):
                                from urllib.parse import urljoin, urlparse

                                abs_url = urljoin(url, m.group(1))
                                parsed = urlparse(abs_url)
                                if parsed.scheme not in ("http", "https"):
                                    continue
                                if session.same_domain and parsed.netloc != urlparse(
                                    session.start_url
                                ).netloc:
                                    continue
                                if abs_url not in seen:
                                    seen.add(abs_url)
                                    queue.append(abs_url)

                    except Exception as e:
                        session.errors.append(f"{url}: {e}")
                        logger.warning("Spider %s failed on %s: %s", spider_id, url, e)

            session.status = "completed"

        except Exception as e:
            session.status = "failed"
            session.errors.append(str(e))
            logger.exception("Spider %s crashed", spider_id)

    async def _cleanup_stale(self) -> None:
        """Remove completed/failed sessions older than STALE_SESSION_HOURS."""
        now = time.time()
        stale_ids = [
            sid
            for sid, s in list(self._sessions.items())
            if s.status in ("completed", "failed")
            and (now - (s._started_at or now)) > STALE_SESSION_HOURS * 3600
        ]
        for sid in stale_ids:
            async with self._lock:
                self._sessions.pop(sid, None)
        if stale_ids:
            logger.info("Cleaned %d stale spider sessions", len(stale_ids))

    async def spider_status(self, spider_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(spider_id)
            if not session:
                return {"error": f"Spider {spider_id} not found"}
            return session.to_dict()

    async def spider_results(self, spider_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(spider_id)
            if not session:
                return {"error": f"Spider {spider_id} not found"}
            return {
                **session.to_dict(),
                "pages": session.pages_crawled,
                "errors": session.errors,
            }


_channel: ScraplingSpiderChannel | None = None


def get_channel() -> ScraplingSpiderChannel:
    global _channel
    if _channel is None:
        _channel = ScraplingSpiderChannel()
    return _channel
