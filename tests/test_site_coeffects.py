"""P1.2 + P1.3 — site coeffects, harvest-with-undo, and refresh safety."""
import asyncio
from types import SimpleNamespace

import server.local_harness.providers as providers
import server.local_harness.session as session_mod
import server.local_harness.sites as sites_mod
from server.local_harness.providers import liveness_snapshot, site_key
from server.local_harness.session import ACTIVE, open_local_session


class _FakeBroker:
    def start_session(self, **kwargs):
        ctx = kwargs.get("ctx")
        if ctx is not None:
            ctx.realm = "local:pending"
        return SimpleNamespace(session_id="sess-1", cluster="general", ctx=ctx)


def _patch_open(monkeypatch, urls, *, fail=()):
    """Stub broker, task lookup, harvest and DB writes. Records rows written/deleted."""
    rows = {"next": 0, "live": {}, "deleted": []}

    def fake_harvest(url, timeout=0):
        if url in fail:
            return {"url": url, "status": 0, "error": "boom", "text_content": ""}
        return {"url": url, "title": "T", "text_content": "hello", "status": 200}

    def fake_insert(project, session_id, url, title, text):
        rows["next"] += 1
        rows["live"][rows["next"]] = url
        return rows["next"]

    def fake_delete(row_id):
        rows["deleted"].append(row_id)
        rows["live"].pop(row_id, None)

    monkeypatch.setattr(
        "server.tool_session_broker.get_tool_session_broker", lambda: _FakeBroker())
    monkeypatch.setattr(session_mod, "_task_site_urls", lambda project, task_type: list(urls))
    monkeypatch.setattr(sites_mod, "_harvest", fake_harvest)
    monkeypatch.setattr(sites_mod, "_insert_row", fake_insert)
    monkeypatch.setattr(sites_mod, "_delete_row", fake_delete)
    return rows


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


def test_open_declares_and_binds_task_sites(monkeypatch):
    urls = ["https://docs.example/a", "https://docs.example/b"]
    rows = _patch_open(monkeypatch, urls)
    lh = open_local_session("task", project="kaari", task_type="coding")
    keys = {site_key(u) for u in urls}
    assert set(lh.site_urls) == keys
    assert keys <= set(lh.ctx.deps)
    assert all(lh.ctx.deps[k].satisfied for k in keys)
    assert lh.site_status == {u: "bound" for u in urls}
    assert len(rows["live"]) == 2                       # one row per site
    assert lh.state == ACTIVE
    lh.dispose()


def test_dispose_removes_harvested_rows(monkeypatch):
    urls = ["https://docs.example/a", "https://docs.example/b"]
    rows = _patch_open(monkeypatch, urls)
    lh = open_local_session("task", project="kaari", task_type="coding")
    lh.dispose()
    assert rows["live"] == {}                           # close leaves no residue
    assert sorted(rows["deleted"]) == [1, 2]


def test_failed_harvest_registers_no_undo_and_stays_open(monkeypatch):
    good, bad = "https://docs.example/ok", "https://docs.example/down"
    rows = _patch_open(monkeypatch, [good, bad], fail={bad})
    lh = open_local_session("task", project="kaari", task_type="coding")
    assert lh.site_status[good] == "bound"
    assert lh.site_status[bad].startswith("boom")
    assert site_key(bad) not in lh._committed
    assert lh.ctx.deps[site_key(bad)].satisfied is False
    assert len(lh.ctx.accumulator) == 1                 # only the good site's undo
    assert lh.state == ACTIVE
    lh.dispose()
    assert len(rows["deleted"]) == 1                    # only the good row removed


def test_refresh_does_not_tear_down_on_failed_site(monkeypatch):
    """Unbound site must not count as a deactivation (would dispose the session)."""
    bad = "https://docs.example/down"
    _patch_open(monkeypatch, [bad], fail={bad})
    lh = open_local_session("task", project="kaari", task_type="coding")

    def fake_snapshot(realm, sites=None):
        out = {k: True for k in ("db", "faiss", "git", "internet", "embedder")}
        out.update(sites or {})
        return out

    monkeypatch.setattr(providers, "liveness_snapshot", fake_snapshot)
    assert asyncio.run(lh.refresh()) == ACTIVE
    lh.dispose()


def test_open_without_sites_is_unchanged(monkeypatch):
    rows = _patch_open(monkeypatch, [])
    lh = open_local_session("task", project="default")
    assert lh.site_urls == {} and lh.site_status == {}
    assert not any(k.startswith("site:") for k in lh.ctx.deps)
    assert rows["next"] == 0
    assert lh.state == ACTIVE
    lh.dispose()


def test_refresh_keeps_bound_sites_alive(monkeypatch):
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
    assert asyncio.run(lh.refresh()) == ACTIVE
    assert seen["sites"] == {site_key(u): True for u in urls}
    lh.dispose()


def test_dispose_by_id_reports_released_sites(monkeypatch):
    import server.execution_plane as ep
    from server.local_harness.session import dispose_by_id

    good, bad = "https://docs.example/ok", "https://docs.example/down"
    rows = _patch_open(monkeypatch, [good, bad], fail={bad})
    lh = open_local_session("task", project="kaari", task_type="coding")
    monkeypatch.setattr(ep, "report_session", lambda sid, outcome, **kw: {"status": "ok"})

    out = dispose_by_id(lh.inner.session_id, "correct")
    assert out["status"] == "ok"
    assert out["sites_released"] == [good]              # only bound sites are released
    assert rows["live"] == {}                           # rows removed by the undo chain


def test_site_flap_disposes_rows_exactly_once(monkeypatch):
    """Site drops (dispose) → returns (activate) → drops again: no double delete."""
    urls = ["https://docs.example/a"]
    rows = _patch_open(monkeypatch, urls)
    lh = open_local_session("task", project="kaari", task_type="coding")
    key = site_key(urls[0])
    state = {"alive": True}

    def snapshot(realm, sites=None):
        out = {k: True for k in ("db", "faiss", "git", "internet", "embedder")}
        out.update(sites or {})
        out[key] = state["alive"]
        return out

    monkeypatch.setattr(providers, "liveness_snapshot", snapshot)
    state["alive"] = False
    assert asyncio.run(lh.refresh()) == "INACTIVE"      # dependency lost → undo runs
    assert rows["deleted"] == [1]
    state["alive"] = True
    assert asyncio.run(lh.refresh()) == ACTIVE          # recovery
    state["alive"] = False
    asyncio.run(lh.refresh())                           # second loss: armed guard
    assert rows["deleted"] == [1]                       # still exactly one delete
