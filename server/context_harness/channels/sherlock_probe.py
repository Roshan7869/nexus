"""Sherlock Probe Channel — Phase 2.

Wraps Sherlock's username-probe engine as an async MCP backend.
Runs Sherlock synchronously via asyncio.to_thread with timeout.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from server.net_guard import guarded_request

logger = logging.getLogger(__name__)


class UsernameValidator:
    """Validate username format before probing (regex per site)."""

    def __init__(self) -> None:
        self._site_regexes: dict[str, re.Pattern] = {}

    def load(self, sites_data: dict) -> None:
        for name, info in sites_data.items():
            pattern = info.get("regexCheck")
            if pattern:
                try:
                    self._site_regexes[name] = re.compile(pattern)
                except re.error:
                    pass

    def validate(self, username: str, site: str | None = None) -> bool:
        if site and site in self._site_regexes:
            return bool(self._site_regexes[site].match(username))
        # If no site specified, must match ALL site regexes
        return all(p.match(username) for p in self._site_regexes.values())


class SherlockProbeChannel:
    """Port of Sherlock's core probe logic — no CLI, no notify, no colorama.

    Probes 400+ sites for a username using regex validation + HTTP checks.
    """

    def __init__(self) -> None:
        self._sites_data: dict[str, Any] = {}
        self._validator = UsernameValidator()

    async def initialize(self) -> None:
        """Load site manifest from sherlock resources."""
        if self._sites_data:
            return
        import json
        from pathlib import Path

        data_path = (
            Path.home()
            / "sherlock/sherlock_project/resources/data.json"
        )
        if not data_path.exists():
            logger.warning("Sherlock data.json not found at %s", data_path)
            self._sites_data = {}
            return

        raw = data_path.read_text(encoding="utf-8")
        self._sites_data = json.loads(raw)
        self._validator.load(self._sites_data)

    async def probe_username(
        self,
        username: str,
        sites: list[str] | None = None,
        timeout: float = 60.0,
        max_concurrent: int = 20,
    ) -> dict[str, Any]:
        """Probe all (or selected) sites for a username.

        Args:
            username: username to search for
            sites: subset of sites (None = all 400+)
            timeout: total timeout seconds
            max_concurrent: max concurrent HTTP probes

        Returns:
            {site_name: {exists, status, url, error}, ...} for all checked sites
        """
        await self.initialize()
        if not self._sites_data:
            return {"error": "No site data loaded", "results": {}}

        target_sites = dict(self._sites_data)
        if sites:
            target_sites = {
                k: v for k, v in target_sites.items()
                if k.lower() in [s.lower() for s in sites]
            }

        import httpx

        results: dict[str, Any] = {}
        sem = asyncio.Semaphore(max_concurrent)
        started = time.time()

        async def _check(name: str, info: dict) -> tuple[str, dict]:
            async with sem:
                url = info.get("url", "").replace("{}", username)
                method = info.get("method", "GET")
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                }
                error_type = info.get("errorType", "status_code")
                error_msg = info.get("errorMsg", "")
                error_url = info.get("errorUrl", "")

                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                        if method == "GET":
                            resp = await guarded_request(client, "GET", url, headers=headers)
                        else:
                            resp = await guarded_request(client, "POST", url, headers=headers)

                    if error_type == "status_code":
                        exists = resp.status_code not in (int(e) for e in error_msg.split(",")) if error_msg else resp.status_code == 200
                    elif error_type == "message":
                        exists = error_msg not in resp.text
                    elif error_type == "response_url":
                        exists = error_url not in str(resp.url)
                    else:
                        exists = resp.status_code == 200

                    return name, {
                        "exists": exists,
                        "status": resp.status_code,
                        "url": str(resp.url),
                    }
                except Exception as e:
                    return name, {
                        "exists": False,
                        "status": 0,
                        "url": url,
                        "error": str(e),
                    }

        tasks = [_check(name, info) for name, info in target_sites.items()]
        done = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )

        for item in done:
            if isinstance(item, Exception):
                continue
            if isinstance(item, tuple):
                name, data = item
                results[name] = data

        elapsed = time.time() - started
        return {
            "username": username,
            "sites_checked": len(results),
            "sites_found": sum(1 for v in results.values() if v.get("exists")),
            "results": results,
            "elapsed_seconds": round(elapsed, 2),
        }

    async def probe_sites(
        self,
        username: str,
        sites: list[str],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Probe only specific sites — faster than full scan."""
        return await self.probe_username(
            username, sites=sites, timeout=timeout, max_concurrent=10
        )


_channel: SherlockProbeChannel | None = None


def get_channel() -> SherlockProbeChannel:
    global _channel
    if _channel is None:
        _channel = SherlockProbeChannel()
    return _channel
