"""P1.2 — site coeffect keys wired into open_local_session + liveness."""
import asyncio
from types import SimpleNamespace

import server.local_harness.providers as providers
import server.local_harness.session as session_mod
from server.local_harness.providers import liveness_snapshot, site_key
from server.local_harness.session import ACTIVE, open_local_session


class _FakeBroker:
    def start_session(self, **kwargs):
        ctx = kwargs.get("ctx")
        if ctx is not None:
            ctx.realm = "local:pending"
        return SimpleNamespace(session_id="sess-1", cluster="general", ctx=ctx)


def _patch_open(monkeypatch, urls):
    monkeypatch.setattr(
        "server.tool_session_broker.get_tool_session_broker", lambda: _FakeBroker())
    monkeypatch.setattr(session_mod, "_task_site_urls", lambda project, task_type: list(urls))


def test_site_key_is_stable_prefixed_and_short():
    k = site_key("https://docs.example/a")
    assert k == site_key("https://docs.example/a")
    assert k.startswith("site:") and len(k) == len("site:") + 12
    assert site_key("https://docs.example/b") != k


def test_snapshot_includes_only_site_keys_from_sites_arg(monkeypatch):
    monkeypatch.setattr(providers, "_git_ok", lambda *a, **k: True)
    monkeypatch.setattr(providers, "_net_ok", lambda *a, **k: True)
    snap = liveness_snapshot("local:t", sites={"site:abc": True, "bogus": True, "site:x": False})
    assert snap["site:abc"] is True
    assert snap["site:x"] is False
    assert "bogus" not in snap


def test_open_declares_task_sites_as_coeffects(monkeypatch):
    urls = ["https://docs.example/a", "https://docs.example/b"]
    _patch_open(monkeypatch, urls)
    lh = open_local_session("task", project="kaari", task_type="coding")
    keys = {site_key(u) for u in urls}
    assert set(lh.site_urls) == keys
    assert keys <= set(lh.ctx.deps)                       # declared as coeffects
    assert all(lh.ctx.deps[k].satisfied for k in keys)    # satisfied at open
    assert lh.state == ACTIVE
    lh.dispose()


def test_open_without_sites_is_unchanged(monkeypatch):
    _patch_open(monkeypatch, [])
    lh = open_local_session("task", project="default")
    assert lh.site_urls == {}
    assert not any(k.startswith("site:") for k in lh.ctx.deps)
    assert lh.state == ACTIVE
    lh.dispose()


def test_refresh_does_not_deactivate_bound_sites(monkeypatch):
    """Site keys must be reported alive to refresh(), or an open session dies on refresh."""
    urls = ["https://docs.example/a"]
    _patch_open(monkeypatch, urls)
    lh = open_local_session("task", project="kaari", task_type="coding")

    seen = {}

    def fake_snapshot(realm, sites=None):
        seen["sites"] = dict(sites or {})
        out = {k: True for k in ("db", "faiss", "git", "internet", "embedder")}
        out.update(sites or {})
        return out

    monkeypatch.setattr(providers, "liveness_snapshot", fake_snapshot)
    state = asyncio.run(lh.refresh())
    assert seen["sites"] == {site_key(u): True for u in urls}
    assert state == ACTIVE
    lh.dispose()
