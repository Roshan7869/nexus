"""Generic HTTP channel for user-defined harness sites (plan W2).

Built from a ``SiteDef`` in ``config/harness_sites.yaml`` that has a ``base_url``.
Sends ``GET base_url?<query_param>=<query>`` and turns the response into HarnessResults.

Safety:
  - every hop goes through ``server.net_guard.guarded_request`` (https only, public IPs)
  - credentials are read from the env var named by ``auth_env`` and never stored or logged
  - responses are capped at MAX_BYTES; query length is capped at MAX_QUERY_CHARS
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import httpx

from server.context_harness.models import HarnessResult
from server.harness_sites import SiteDef
from server.net_guard import guarded_request

MAX_BYTES = 1_000_000
MAX_QUERY_CHARS = 500
MAX_ITEM_CHARS = 1500
MAX_TEXT_CHARS = 4000


def _extract_path(data: Any, dotted: str | None) -> Any:
    if not dotted:
        return data
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _build_url(base: str, param: str, query: str) -> str:
    parts = urlsplit(base)
    existing = parse_qsl(parts.query, keep_blank_values=True)
    existing.append((param, query))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(existing), ""))


class UserSiteChannel:
    """Implements BackendProtocol for a user site."""

    def __init__(self, site: SiteDef) -> None:
        if not site.base_url:
            raise ValueError(f"site '{site.name}' has no base_url")
        self._site = site
        self.name = site.name
        self.weight = site.weight
        self.cache_ttl = site.cache_ttl
        self.timeout_seconds = site.timeout_seconds

    def _headers(self) -> dict[str, str] | None:
        if not self._site.auth_env:
            return {}
        value = os.environ.get(self._site.auth_env, "").strip()
        if not value:
            return None  # credential missing: caller treats site as unavailable
        return {"Authorization": f"Bearer {value}"}

    async def is_available(self) -> bool:
        return self._site.status == "ready" and self._headers() is not None

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        query = (query or "").strip()[:MAX_QUERY_CHARS]
        if not query:
            return []
        headers = self._headers()
        if headers is None:
            return []  # auth_env set but not exported in the environment

        url = _build_url(self._site.base_url, self._site.query_param, query)
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                     headers={"User-Agent": "NEXUS-Harness/1.0"}) as client:
            resp = await guarded_request(client, "GET", url, headers=headers)
        resp.raise_for_status()

        body = resp.content[:MAX_BYTES]
        return self._to_results(body, resp.headers.get("content-type", ""), url, max_results)

    def _to_results(self, body: bytes, ctype: str, url: str, max_results: int) -> list[HarnessResult]:
        if "json" in ctype.lower():
            try:
                data = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                return []
            items = _extract_path(data, self._site.results_path)
            if not isinstance(items, list):
                return []
            out = []
            for i, item in enumerate(items[:max_results]):
                text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                out.append(HarnessResult(
                    source=self.name,
                    content=text[:MAX_ITEM_CHARS],
                    score=1.0 - i * 0.01,  # rank order is what RRF uses
                    metadata={"url": url, "rank": i},
                ))
            return out

        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        return [HarnessResult(
            source=self.name,
            content=text[:MAX_TEXT_CHARS],
            score=1.0,
            metadata={"url": url, "rank": 0},
        )]


def build_backends(sites: list[SiteDef]) -> list[UserSiteChannel]:
    """Channels for every ``ready`` site that has a base_url. Builtins are excluded."""
    return [UserSiteChannel(s) for s in sites if s.status == "ready" and s.base_url]
