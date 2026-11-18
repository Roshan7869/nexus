"""W2: generic HTTP channel for user-defined harness sites."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.context_harness.channels import user_site
from server.context_harness.channels.user_site import UserSiteChannel, build_backends
from server.harness_sites import SiteDef
from server.net_guard import UnsafeURLError


def _site(**over) -> SiteDef:
    base = dict(
        name="design-site", use_cases=("web-dev",), weight=1.4, cache_ttl=3600,
        timeout_seconds=5, max_results=10, status="ready",
        base_url="https://design.example.com/search", query_param="q",
        results_path="data.items", auth_env=None,
    )
    base.update(over)
    return SiteDef(**base)


def _fake_response(body: bytes, ctype: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body, headers={"content-type": ctype},
                          request=httpx.Request("GET", "https://design.example.com/search"))


def _run(coro):
    return asyncio.run(coro)


def test_json_results_extracted_by_results_path_and_ranked(monkeypatch):
    ch = UserSiteChannel(_site())
    body = json.dumps({"data": {"items": [{"title": "Card grid"}, {"title": "Hero"}]}}).encode()

    async def fake(client, method, url, **kw):
        assert "q=card+layout" in url or "q=card%20layout" in url
        return _fake_response(body, "application/json")

    monkeypatch.setattr(user_site, "guarded_request", fake)
    results = _run(ch.search("card layout", max_results=5))

    assert [r.metadata["rank"] for r in results] == [0, 1]
    assert "Card grid" in results[0].content
    assert results[0].source == "design-site"
    assert results[0].score > results[1].score


def test_text_response_becomes_single_capped_result(monkeypatch):
    ch = UserSiteChannel(_site(results_path=None))
    body = ("x" * 9000).encode()

    async def fake(client, method, url, **kw):
        return _fake_response(body, "text/plain")

    monkeypatch.setattr(user_site, "guarded_request", fake)
    results = _run(ch.search("anything"))
    assert len(results) == 1
    assert len(results[0].content) == user_site.MAX_TEXT_CHARS


def test_empty_query_returns_nothing_without_network(monkeypatch):
    ch = UserSiteChannel(_site())

    async def boom(*a, **k):
        raise AssertionError("network must not be touched for empty query")

    monkeypatch.setattr(user_site, "guarded_request", boom)
    assert _run(ch.search("   ")) == []


def test_auth_env_sent_as_bearer_and_missing_env_disables_site(monkeypatch):
    ch = UserSiteChannel(_site(auth_env="NEXUS_TEST_DESIGN_KEY"))
    monkeypatch.delenv("NEXUS_TEST_DESIGN_KEY", raising=False)
    assert _run(ch.is_available()) is False
    assert _run(ch.search("q")) == []

    monkeypatch.setenv("NEXUS_TEST_DESIGN_KEY", "tok-123")
    assert _run(ch.is_available()) is True
    seen = {}

    async def fake(client, method, url, **kw):
        seen["headers"] = kw.get("headers")
        return _fake_response(b"ok", "text/plain")

    monkeypatch.setattr(user_site, "guarded_request", fake)
    _run(ch.search("q"))
    assert seen["headers"] == {"Authorization": "Bearer tok-123"}


def test_private_address_blocked_even_if_site_def_bypasses_loader():
    """Defense in depth: the guard runs at fetch time, not only at load time."""
    ch = UserSiteChannel(_site(base_url="https://127.0.0.1/search"))
    with pytest.raises(UnsafeURLError):
        _run(ch.search("q"))


def test_build_backends_only_ready_sites_with_base_url():
    sites = [
        _site(name="ok"),
        _site(name="placeholder", status="unconfigured", base_url=None),
        _site(name="builtin-ref", status="builtin", base_url=None),
    ]
    backends = build_backends(sites)
    assert [b.name for b in backends] == ["ok"]


def test_fuse_registered_isolates_failing_site(monkeypatch):
    """A failing user site must not break fusion for other backends."""
    from server.context_harness.backend_protocol import fuse_registered
    from server.context_harness.models import HarnessResult

    class Good:
        name, weight, cache_ttl, timeout_seconds = "good", 1.0, 60, 5

        async def search(self, query, max_results=10):
            return [HarnessResult(source="good", content="hit", score=1.0, metadata={})]

        async def is_available(self):
            return True

    bad = UserSiteChannel(_site(base_url="https://127.0.0.1/x"))  # always blocked
    results = _run(fuse_registered("q", [Good(), bad], max_results=5))
    assert [r.content for r in results] == ["hit"]
