"""Local-harness acceptance suite — DB-free. Broker/DB-touching paths assert by
signature only (live-DB integration lands with M7 wiring, Wave 1).
"""
import inspect
import sqlite3

import pytest

from server.local_harness.context import (
    NexusContext, classify_transition, was_satisfied,
)
from server.local_harness.loader import Entry, EntryTree, reconcile
from server.local_harness.session import LocalHarnessSession, keys_for_cluster


class FakeInner:
    session_id = "test123"


def make_session():
    return LocalHarnessSession(FakeInner(), realm="local:test123")


# ── M2 trichotomy ────────────────────────────────────────────────────────────
def test_notify_trichotomy():
    assert was_satisfied(False, True) == "activating"
    assert was_satisfied(True, False) == "deactivating"
    assert was_satisfied(True, True) == "neutral"
    assert was_satisfied(False, False) == "neutral"


def test_classify_multi_key():
    out = classify_transition({"db": True, "faiss": False}, {"db": True, "faiss": True})
    assert out == {"db": "neutral", "faiss": "activating"}


def test_context_defaults():
    ctx = NexusContext()
    assert ctx.realm == "local:default" and ctx.accumulator == []


# ── M3 session ───────────────────────────────────────────────────────────────
def test_lifo_dispose():
    s = make_session()
    order = []
    s.effect(lambda: (lambda: order.append(1)))
    s.effect(lambda: (lambda: order.append(2)))
    s.effect(lambda: (lambda: order.append(3)))
    s.dispose()
    assert order == [3, 2, 1]


def test_armed_at_most_once():
    s = make_session()
    calls = []
    s.effect(lambda: (lambda: calls.append(1)))
    s.dispose()
    s.dispose()
    assert calls == [1]


def test_disposer_error_isolated():
    s = make_session()
    order = []

    def bad():
        def inv():
            raise RuntimeError("boom")
        return inv
    s.effect(bad)
    s.effect(lambda: (lambda: order.append("ok")))
    s.dispose()  # must not raise; LIFO continues
    assert order == ["ok"]


def test_chaos_no_leak():
    for _ in range(100):
        s = make_session()
        try:
            s.effect(lambda: (lambda: None))
            if len(s.ctx.accumulator) > 1:
                raise RuntimeError("mid-session failure")
        except RuntimeError:
            pass
        finally:
            s.dispose()
        assert s.ctx.accumulator == []


def test_keys_for_cluster():
    assert "db" in keys_for_cluster("general")
    assert "faiss" in keys_for_cluster("coding")


# ── M4 loader ────────────────────────────────────────────────────────────────
def recording_apply():
    calls = []

    def apply(action, old, new):
        calls.append(action)
        if getattr(new, "id", "") == "boom" or getattr(old, "id", "") == "boom":
            raise RuntimeError("apply failed")
    return calls, apply


def test_reconcile_insert_delete():
    calls, apply = recording_apply()
    old = EntryTree({"a": Entry(id="a", kind="skill")})
    new = EntryTree({"b": Entry(id="b", kind="skill")})
    rep = reconcile(old, new, apply)
    assert rep.removed == ["a"] and rep.added == ["b"]


def test_disabled_is_fresh_not_unretire():
    calls, apply = recording_apply()
    old = EntryTree({"a": Entry(id="a", kind="skill", disabled=True)})
    new = EntryTree({"a": Entry(id="a", kind="skill", disabled=False)})
    rep = reconcile(old, new, apply)
    assert rep.reloaded == ["a"] and "reload" in calls


def test_intercept_in_place_no_reload():
    calls, apply = recording_apply()
    old = EntryTree({"a": Entry(id="a", kind="skill", intercept={})})
    new = EntryTree({"a": Entry(id="a", kind="skill", intercept={"q": 1})})
    rep = reconcile(old, new, apply)
    assert calls == ["intercept"] and rep.reloaded == ["a"]


def test_rollback_on_error():
    calls, apply = recording_apply()
    old = EntryTree({})
    new = EntryTree({"boom": Entry(id="boom", kind="skill")})
    with pytest.raises(RuntimeError):
        reconcile(old, new, apply)


# ── M6/M7/M10 signature grounding (no DB) ────────────────────────────────────
def test_opencode_ide_preserved():
    from server.execution_plane import _sanitize_ide
    assert _sanitize_ide("opencode") == "opencode"


def test_ctx_kwargs_present():
    from server.tool_session_broker import ToolSessionBroker
    assert "ctx" in inspect.signature(ToolSessionBroker.start_session).parameters
    assert "ctx" in inspect.signature(ToolSessionBroker.end_session).parameters


def test_weights_default_identical():
    from server.router_v2.fusion import v5_get_weights
    w = v5_get_weights()
    assert w.get("shared", 0.0) == 0.0


# ── Wave 2: M9 subscribe/notify + A4 read-only + M10 fusion ────────────────
def test_subscribe_notify_trichotomy():
    from server.precondition import StateGates, StateVariable
    g = StateGates()
    seen = []
    g.subscribe("has_db", on_change=seen.append)
    g.set_and_notify(StateVariable.HAS_DB, False)
    g.set_and_notify(StateVariable.HAS_DB, True)
    assert seen == ["deactivating", "activating"]


def test_check_unchanged_with_subs():
    from server.precondition import StateGates
    g = StateGates()
    g.subscribe("*")
    ok, _ = g.check("query_db")
    assert ok is True  # subscribers never alter gate verdicts


def test_command_is_readonly():
    from server.precondition import command_is_readonly as ro
    assert ro("list projects") is True
    assert ro("") is True
    assert ro("write file") is False
    assert ro("git push") is False
    assert ro("orx exp run x") is False  # conservative: unknown ⇒ False
    assert ro("do something novel") is False


def test_convex_default_identical():
    from server.router_v2.fusion import v5_convex_combine
    # float renormalization is pre-existing (0.9999…); the gate is that the
    # shared term changes nothing when off (None vs explicit zeros).
    assert v5_convex_combine([1.0], [1.0], [1.0], [1.0]) == \
        v5_convex_combine([1.0], [1.0], [1.0], [1.0], shared=[0.0])
    assert v5_convex_combine([0.5], [0.5], [0.5], [0.5]) == \
        v5_convex_combine([0.5], [0.5], [0.5], [0.5], shared=[0.0])


def test_shared_prior_bounded():
    from server.router_v2.fusion import shared_prior
    assert shared_prior({}) == 0.0
    assert shared_prior({"times_correct": 0}) == 0.0
    assert shared_prior({"times_correct": 10}, max_correct=10) == 1.0
    assert 0.0 < shared_prior({"times_correct": 3}, max_correct=10) < 1.0


def test_recycle_spill_preserves_top2():
    from server.router_v2.fusion import recycle_spill
    ranked = [{"id": i, "cluster": "a" if i < 3 else "b"} for i in range(5)]
    assert recycle_spill(ranked, 0) == ranked  # disabled == identical
    out = recycle_spill(ranked, 2)
    assert [r["id"] for r in out[:4]] == [0, 1, 3, 4]
    assert out[-1]["id"] == 2  # overflow spilled to tail, order kept


# ── A01 / A03 / M12 ──────────────────────────────────────────────────────────
def test_registry_default_and_unknown():
    from server.local_harness.harness import get
    assert get("opencode").id() == "opencode"
    assert get("nope-not-real").id() == "opencode"


def test_playbook_has_no_procedures():
    from server.local_harness.playbook import build_context_block
    block = build_context_block("p", "pid", "state", "art", "q",
                                {"cluster": "general"}, ["t1"], None)
    assert "NEXUS-routed" in block and "Task: q" in block


def test_redis_none_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_HARNESS_REDIS", raising=False)
    from server.cache import redis_cache
    assert redis_cache.get_redis() is None


# ── Wave 1: broker ctx round-trip (live-DB, repo-normative) ────────────────
def test_broker_ctx_round_trip():
    """M7: start registers inverse+deps; end disposes LIFO; row completed, not clobbered."""
    from server.local_harness.context import NexusContext
    from server.tool_session_broker import ToolSessionBroker
    broker = ToolSessionBroker()
    ctx = NexusContext(realm="local:test-wave1")
    s = broker.start_session(project="nexus", query="wave1 ctx round trip probe",
                             use_embedding=False, max_tools=3, ctx=ctx)
    try:
        assert len(ctx.accumulator) == 1  # persisted-row inverse registered
        assert "db" in ctx.deps           # cluster coeffects declared
        out = broker.end_session(s.session_id, outcome="correct",
                                 summary="wave1 probe", ctx=ctx)
        assert out["status"] == "dumped"
        assert ctx.accumulator == []      # LIFO disposed
        assert not ctx._armed             # at-most-once consumed
    finally:
        broker._active.pop(s.session_id, None)


# ── Wave 1: adapter + flag + reconcile/status (DB-free paths) ───────────────
def test_adapter_mapping_and_payload():
    from server import opencode_adapter as oa
    assert oa.report_mapping("done") == "correct"
    assert oa.report_mapping("FAILED") == "wrong"
    assert oa.IDE_NAME == "opencode"
    p = oa.session_payload({"session_id": "s1", "visible_tools": ["a"],
                            "context_block": "ctx"})
    assert p["session_id"] == "s1" and p["tools"] == ["a"]


def test_adapter_config_shape():
    from server import opencode_adapter as oa
    cfg = oa.get_opencode_config()
    assert cfg["transport"] == "stdio" and "nexus-mcp-enhanced.py" in cfg["command"][1]


def _load_mcp_module():
    """nexus-mcp-enhanced.py has hyphens — load by path, not dotted import."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "server" / "nexus-mcp-enhanced.py"
    spec = importlib.util.spec_from_file_location("nexus_mcp_enhanced_live", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_flag_contract():
    mcp_mod = _load_mcp_module()
    import os
    old = os.environ.get("NEXUS_LOCAL_HARNESS")
    try:
        os.environ["NEXUS_LOCAL_HARNESS"] = "1"
        assert mcp_mod._local_harness_enabled() is True
        os.environ["NEXUS_LOCAL_HARNESS"] = "0"
        assert mcp_mod._local_harness_enabled() is False
    finally:
        if old is None:
            os.environ.pop("NEXUS_LOCAL_HARNESS", None)
        else:
            os.environ["NEXUS_LOCAL_HARNESS"] = old


def test_reconcile_rejects_bad_json():
    import json
    mcp_mod = _load_mcp_module()
    out = json.loads(mcp_mod.local_harness_reconcile("not json{{{"))
    assert "error" in out


def test_manifest_backfill_empty():
    from server.execution_plane import artifact_manifest
    d, items = artifact_manifest("no-such-session-ever-12345")
    assert d is None and items == []
# ── Session-path wiring (audit fix): ctx adopted, registry, report disposes ──
def test_ctx_adopted_not_replaced():
    from server.local_harness.context import NexusContext
    from server.local_harness.session import LocalHarnessSession
    ctx = NexusContext()
    lh = LocalHarnessSession(FakeInner(), realm="local:x", ctx=ctx)
    assert lh.ctx is ctx and ctx.realm == "local:x"


def test_wrapper_registry_pop():
    from server.local_harness.session import (
        LocalHarnessSession, pop_wrapper, _WRAPPERS)
    assert pop_wrapper("never-registered") is None
    lh = LocalHarnessSession(FakeInner())
    _WRAPPERS["t-reg"] = lh
    assert pop_wrapper("t-reg") is lh
    assert "t-reg" not in _WRAPPERS


def test_report_disposes_wrapper(monkeypatch):
    import server.execution_plane as ep
    import server.local_harness.session as sess
    monkeypatch.setattr(ep, "report_session",
                        lambda *a, **k: {"status": "dumped"})
    lh = sess.LocalHarnessSession(FakeInner())
    lh.effect(lambda: (lambda: None))
    sess._WRAPPERS["t-rep"] = lh
    out = sess.dispose_by_id("t-rep", "correct")
    assert out["status"] == "dumped"
    assert lh.ctx.accumulator == [] and "t-rep" not in sess._WRAPPERS


# ── M18 / A06c SQL validity ──────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["020_local_harness.sql", "021_experiments.sql"])
def test_migrations_apply_clean(name):
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent
           / "db" / "migrations" / name).read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql)
    conn.executescript(sql)  # idempotent double-apply
    conn.close()


# ── M9 spatial coeffects (okf-native-cordis Phase 4) ─────────────────────────
def _m9_committed_session():
    """Session with deps committed + one registered inverse; caller sets state."""
    import asyncio
    from server.local_harness.session import ACTIVE

    s = make_session()
    s.require("db", "git")
    calls = []
    s.effect(lambda: (lambda: calls.append("disposed")))
    s.commit_providers(resolve=lambda k: f"{k}:uid")
    s.state = ACTIVE
    return s, calls


def test_m9_provider_loss_disposes_once():
    import asyncio
    from server.local_harness.session import INACTIVE, ACTIVE

    s, calls = _m9_committed_session()
    # all alive → inert
    out = asyncio.run(s.refresh({"db": True, "git": True}))
    assert out == ACTIVE and calls == [] and s.ctx.accumulator

    # db dies → ACTIVE → UNLOADING → INACTIVE, inverse runs exactly once
    out = asyncio.run(s.refresh({"db": False, "git": True}))
    assert out == INACTIVE
    assert calls == ["disposed"]
    assert s.ctx._armed is False and s.ctx.accumulator == []
    assert "db" not in s._committed                    # binding cleared
    assert s.ctx.deps["db"].satisfied is False
    assert s.ctx.deps["git"].satisfied is True         # surviving key untouched


def test_m9_flap_no_double_dispose():
    import asyncio
    from server.local_harness.session import INACTIVE, ACTIVE

    s, calls = _m9_committed_session()
    asyncio.run(s.refresh({"db": True, "git": True}))    # stay ACTIVE
    asyncio.run(s.refresh({"db": False, "git": True}))   # dispose (1st)
    out = asyncio.run(s.refresh({"db": True, "git": True}))   # recover
    assert out == ACTIVE and "db" in s._committed
    asyncio.run(s.refresh({"db": False, "git": True}))   # die again → armed guard
    assert out == ACTIVE                                  # (from recovery)
    assert s.state == INACTIVE
    assert calls == ["disposed"]                          # STILL exactly once
    assert s.ctx._armed is False


def test_m9_activation_commits_and_satisfies():
    import asyncio
    from server.local_harness.session import ACTIVE

    s = make_session()
    s.require("db")
    out = asyncio.run(s.refresh({"db": True}))
    assert out == ACTIVE
    assert "db" in s._committed
    assert s.ctx.deps["db"].satisfied is True


def test_m9_refresh_defaults_to_live_snapshot():
    import asyncio
    from server.local_harness.session import ACTIVE

    s = make_session()
    s.require("db")
    # no `current` → liveness_snapshot() (real signal source) drives the transition
    out = asyncio.run(s.refresh())
    assert out in (ACTIVE, "INACTIVE")   # either liveness verdict is valid
    assert isinstance(out, str)


def test_liveness_snapshot_shape_never_raises():
    from server.local_harness.providers import liveness_snapshot

    snap = liveness_snapshot("local:test")
    assert isinstance(snap, dict)
    assert set(snap) == {"db", "faiss", "git", "internet", "embedder"}
    assert all(isinstance(v, bool) for v in snap.values())


def test_m9_live_db_loss_deactivates_clean(monkeypatch):
    """Live-signal path: DB probe fails → refresh() deactivates the session."""
    import asyncio

    import server.db as server_db
    from server.local_harness.session import INACTIVE

    s, calls = _m9_committed_session()

    def _dead():
        raise RuntimeError("db gone")

    monkeypatch.setattr(server_db, "get_connection", _dead)
    out = asyncio.run(s.refresh())          # current=None → live liveness_snapshot
    assert out == INACTIVE
    assert calls == ["disposed"]            # clean teardown, exactly once
    assert s.ctx.accumulator == []          # no leaked bindings
    # report/dispose_by_id path still safe post-deactivation (armed guard)
    s.dispose()
    assert calls == ["disposed"]


# ── Phase 5: HMR live-apply (apply_db + live reconcile) ──────────────────────
_HMR_SCHEMA = """
CREATE TABLE resources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  resource_type TEXT NOT NULL CHECK(resource_type IN
    ('skill','agent','command','cli_tool','mcp','hook','topic','design_system','craft','doc','spec')),
  repo_name TEXT NOT NULL DEFAULT 'unknown',
  file_path TEXT NOT NULL DEFAULT 'unknown',
  description TEXT DEFAULT '',
  body_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  cluster TEXT,
  archive_reason TEXT,
  archived_at TEXT,
  updated_at TEXT
);
CREATE TABLE local_harness_reconciles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL DEFAULT 'default',
  added INTEGER NOT NULL DEFAULT 0,
  removed INTEGER NOT NULL DEFAULT 0,
  changed INTEGER NOT NULL DEFAULT 0,
  report_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _hmr_db(tmp_path):
    import sqlite3 as _sq
    conn = _sq.connect(str(tmp_path / "hmr.db"))
    conn.row_factory = __import__("sqlite3").Row
    conn.executescript(_HMR_SCHEMA)
    conn.executemany(
        "INSERT INTO resources (name, resource_type, status) VALUES (?, 'skill', ?)",
        [("alpha", "active"), ("beta", "active"), ("gamma", "archived")])
    conn.commit()
    return conn


class _FlakyConn:
    """Proxy that fails INSERTs — forces a mid-batch apply failure."""

    def __init__(self, conn, fail_substring="INSERT INTO resources"):
        self._conn, self._fail = conn, fail_substring

    def execute(self, sql, *a):
        if self._fail and self._fail in sql:
            raise RuntimeError("injected apply failure")
        return self._conn.execute(sql, *a)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _statuses(conn):
    return {r["name"]: r["status"] for r in
            conn.execute("SELECT name, status FROM resources ORDER BY name")}


def test_hmr_apply_add_remove_live(tmp_path):
    import json as _json
    from server.local_harness.apply_db import make_apply
    from server.local_harness.loader import Entry, EntryTree, reconcile

    conn = _hmr_db(tmp_path)
    current = EntryTree({"alpha": Entry("alpha", "skill"),
                         "beta": Entry("beta", "skill")})
    desired = EntryTree({"beta": Entry("beta", "skill"),
                         "delta": Entry("delta", "skill")})  # +new, -alpha
    report = reconcile(current, desired, apply=make_apply(conn, "local:test"))
    conn.commit()
    assert not report.rolled_back
    assert report.added == ["delta"] and report.removed == ["alpha"]
    st = _statuses(conn)
    assert st["alpha"] == "archived" and st["delta"] == "active"
    assert st["beta"] == "active"
    # archive never deletes
    n = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    assert n == 4
    conn.close()


def test_hmr_mid_batch_failure_lifo_reverse(tmp_path):
    from server.local_harness.apply_db import make_apply
    from server.local_harness.loader import Entry, EntryTree, reconcile

    conn = _hmr_db(tmp_path)
    flaky = _FlakyConn(conn)              # INSERT fails → add dies mid-batch
    current = EntryTree({"alpha": Entry("alpha", "skill"),
                         "beta": Entry("beta", "skill")})
    desired = EntryTree({"beta": Entry("beta", "skill"),
                         "delta": Entry("delta", "skill")})  # remove alpha, add delta
    before = _statuses(conn)
    try:
        reconcile(current, desired, apply=make_apply(flaky, "local:test"))
        raise AssertionError("reconcile should have raised")
    except RuntimeError:
        pass
    # loader LIFO reverse restored alpha BEFORE the handler's conn.rollback()
    assert _statuses(conn)["alpha"] == "active"
    conn.rollback()
    assert _statuses(conn) == before          # DB unchanged, row counts equal
    n = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    assert n == len(before)
    conn.close()


def test_hmr_disabled_toggle_archives_not_deletes(tmp_path):
    from server.local_harness.apply_db import make_apply
    from server.local_harness.loader import Entry, EntryTree, reconcile

    conn = _hmr_db(tmp_path)
    # disable an active entry → reload → archived with reason
    reconcile(EntryTree({"alpha": Entry("alpha", "skill")}),
              EntryTree({"alpha": Entry("alpha", "skill", disabled=True)}),
              apply=make_apply(conn, "local:test"))
    conn.commit()
    row = conn.execute("SELECT status, archive_reason FROM resources "
                       "WHERE name='alpha'").fetchone()
    assert row["status"] == "archived" and row["archive_reason"] == "hmr:disabled"
    assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 3

    # re-enable: alpha no longer active → "add" → fresh activation
    reconcile(EntryTree({}), EntryTree({"alpha": Entry("alpha", "skill")}),
              apply=make_apply(conn, "local:test"))
    conn.commit()
    row = conn.execute("SELECT status, archive_reason FROM resources "
                       "WHERE name='alpha'").fetchone()
    assert row["status"] == "active" and row["archive_reason"] is None
    assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 3
    conn.close()


def test_hmr_audit_row_per_call(tmp_path):
    from server.local_harness.apply_db import make_apply, record_reconcile
    from server.local_harness.loader import Entry, EntryTree, reconcile

    conn = _hmr_db(tmp_path)
    current = EntryTree({"alpha": Entry("alpha", "skill"),
                         "beta": Entry("beta", "skill")})
    desired = EntryTree({"beta": Entry("beta", "skill"),
                         "delta": Entry("delta", "skill")})
    report = reconcile(current, desired, apply=make_apply(conn, "local:test"))
    record_reconcile(conn, "default", added_ids=report.added,
                     removed_ids=report.removed,
                     changed_ids=report.reloaded + report.rebuilt,
                     extra={"rolled_back": report.rolled_back})
    conn.commit()
    rows = conn.execute("SELECT * FROM local_harness_reconciles").fetchall()
    assert len(rows) == 1
    assert rows[0]["added"] == 1 and rows[0]["removed"] == 1 and rows[0]["changed"] == 0
    import json as _json
    payload = _json.loads(rows[0]["report_json"])
    assert payload["added"] == ["delta"] and payload["rolled_back"] is False
    conn.close()


def test_hmr_intercept_merge_and_rollback(tmp_path):
    from server.local_harness.apply_db import get_intercept, make_apply
    from server.local_harness.loader import Entry, EntryTree, reconcile

    conn = _hmr_db(tmp_path)
    # forward: intercept merge applies in the maybe phase
    reconcile(EntryTree({"alpha": Entry("alpha", "skill")}),
              EntryTree({"alpha": Entry("alpha", "skill",
                                        intercept={"deny": ["rm -rf"]})}),
              apply=make_apply(conn, "local:test"))
    assert get_intercept("alpha") == {"deny": ["rm -rf"]}

    # failed batch: intercept op runs FIRST (sorted maybe ids), then a later
    # reload fails → LIFO reverse must restore the intercept preimage.
    # loader order is removes → adds → maybe, so the failure must sit in maybe.
    flaky = _FlakyConn(conn, fail_substring="archive_reason=?")  # parameterized!
    try:
        reconcile(
            EntryTree({"alpha": Entry("alpha", "skill"),
                       "zeta": Entry("zeta", "skill")}),
            EntryTree({"alpha": Entry("alpha", "skill",
                                      intercept={"deny": ["rm"]}),
                       "zeta": Entry("zeta", "skill", disabled=True)}),
            apply=make_apply(flaky, "local:t"))
        raise AssertionError("should have raised")
    except RuntimeError:
        pass
    # rollback:intercept restored the preimage captured BEFORE this batch's
    # merge: {"rm"} (partially applied) → back to the prior {"rm -rf"}.
    assert get_intercept("alpha") == {"deny": ["rm -rf"]}
    conn.close()


def test_hmr_handler_live_applied(tmp_path, monkeypatch):
    """Integration: MCP handler returns applied=true and mutates status."""
    import json as _json
    import sqlite3 as _sq
    mcp_mod = _load_mcp_module()
    conn = _hmr_db(tmp_path)          # handler owns + closes this conn
    monkeypatch.setattr(mcp_mod, "get_db", lambda: conn)
    out = _json.loads(mcp_mod.local_harness_reconcile(_json.dumps({"entries": [
        {"id": "beta", "kind": "skill"},
        {"id": "delta", "kind": "skill"}]})))   # -alpha, +delta
    assert out["applied"] is True and out["rolled_back"] is False
    assert out["removed"] == ["alpha"] and out["added"] == ["delta"]
    # handler closed its conn (finally) — reopen the file to assert persistence
    ro = _sq.connect(str(tmp_path / "hmr.db"))
    ro.row_factory = _sq.Row
    st = _statuses(ro)
    assert st["alpha"] == "archived" and st["delta"] == "active"
    rows = ro.execute("SELECT * FROM local_harness_reconciles").fetchall()
    assert len(rows) == 1 and rows[0]["added"] == 1 and rows[0]["removed"] == 1
    ro.close()


def test_hmr_handler_failure_rolls_back_and_records(tmp_path, monkeypatch):
    import json as _json
    import sqlite3 as _sq
    mcp_mod = _load_mcp_module()
    _hmr_db(tmp_path)                 # writes prestate, handler owns the conn
    raw = _sq.connect(str(tmp_path / "hmr.db"))
    raw.row_factory = _sq.Row
    before = _statuses(raw)
    before_n = raw.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    raw.close()
    def _mk_flaky():
        c = _sq.connect(str(tmp_path / "hmr.db"))
        c.row_factory = _sq.Row
        return _FlakyConn(c)
    monkeypatch.setattr(mcp_mod, "get_db", _mk_flaky)
    out = _json.loads(mcp_mod.local_harness_reconcile(_json.dumps({"entries": [
        {"id": "beta", "kind": "skill"},
        {"id": "delta", "kind": "skill"}]})))
    assert out["applied"] is False and out["rolled_back"] is True
    assert "injected apply failure" in out["error"]
    # DB unchanged (row counts + statuses equal pre-state)
    ro = _sq.connect(str(tmp_path / "hmr.db"))
    ro.row_factory = _sq.Row
    assert _statuses(ro) == before
    assert ro.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == before_n
    # audit row still written for the failed run
    rows = ro.execute("SELECT * FROM local_harness_reconciles").fetchall()
    assert len(rows) == 1
    payload = _json.loads(rows[0]["report_json"])
    assert payload["rolled_back"] is True and payload["error"]
    ro.close()


def test_hmr_handler_preview_no_mutation(tmp_path, monkeypatch):
    """apply=false → dry-run: diff only, no status changes, no audit row."""
    import json as _json
    import sqlite3 as _sq
    mcp_mod = _load_mcp_module()
    _hmr_db(tmp_path)
    probe = _sq.connect(str(tmp_path / "hmr.db"))
    probe.row_factory = _sq.Row
    before = _statuses(probe)
    probe.close()
    def _mk_conn():
        c = _sq.connect(str(tmp_path / "hmr.db"))
        c.row_factory = _sq.Row
        return c
    monkeypatch.setattr(mcp_mod, "get_db", _mk_conn)
    out = _json.loads(mcp_mod.local_harness_reconcile(
        _json.dumps({"entries": [{"id": "beta", "kind": "skill"},
                                 {"id": "delta", "kind": "skill"}]}),
        apply=False))
    assert out["applied"] is False and "preview" in out.get("reason", "")
    assert out["removed"] == ["alpha"] and out["added"] == ["delta"]
    ro = _sq.connect(str(tmp_path / "hmr.db"))
    ro.row_factory = _sq.Row
    assert _statuses(ro) == before                          # no mutation
    assert ro.execute("SELECT COUNT(*) FROM local_harness_reconciles"
                      ).fetchone()[0] == 0                  # no audit row
    ro.close()
