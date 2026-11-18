"""Graph lane (second-brain-kg L1-b) — unit + restored-brain acceptance tests.

Mini in-memory DB for semantics; the restored brain (db/nexus.db, read-only)
for the acceptance checks the change doc demands. Env flags are monkeypatched
and always restored.
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))

from kg.graph_lane import (DEFAULT_EDGE_TYPES, EDGE_TYPE_BOOST,  # noqa: E402
                           expand_over_edges, lane_enabled,
                           map_neighbors_to_chunks)
from router_v2.chunk_retrieval import _rrf, retrieve_chunks  # noqa: E402

BRAIN = REPO / "db" / "nexus.db"

SCHEMA = """
CREATE TABLE resources (id INTEGER PRIMARY KEY, name TEXT, resource_type TEXT, cluster TEXT);
CREATE TABLE resource_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL, weight REAL DEFAULT 1.0,
    co_trigger_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, edge_type)
);
CREATE TABLE ingestion_chunks (
    id INTEGER PRIMARY KEY, resource_id INTEGER, content TEXT DEFAULT '',
    heading TEXT, source_id INTEGER, chunk_type TEXT
);
CREATE TABLE resource_sections (
    id INTEGER PRIMARY KEY, resource_id INTEGER, content TEXT DEFAULT '', heading TEXT
);
"""


def _mk_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # seed 1: hub. edges: to 101 (documented_by w=.9), 102 (alternative_to w=.95),
    # 103 (similar_to w=.9 -> eff .765), 104 (similar_to w=.9), 105/106/107 (similar_to)
    # seed 2: second seed, edge 1->2 (seed-seed, must be skipped)
    conn.executemany(
        "INSERT INTO resources (id, name, resource_type, cluster) VALUES (?,?,?,?)",
        [(1, "skill-hub", "skill", "a"), (2, "skill-other", "skill", "a")]
        + [(i, f"res{i}", "skill", "a") for i in range(101, 108)])
    edges = [
        (1, 101, "documented_by", 0.9),
        (1, 102, "alternative_to", 0.95),
        (1, 103, "similar_to", 0.9),
        (1, 104, "similar_to", 0.9),
        (1, 105, "similar_to", 0.9),
        (1, 106, "similar_to", 0.8),
        (1, 107, "similar_to", 0.7),
        (1, 2, "co_triggered", 0.99),   # seed-seed: skipped
        (2, 104, "documented_by", 0.5),
    ]
    conn.executemany(
        "INSERT INTO resource_edges (source_id, target_id, edge_type, weight) "
        "VALUES (?,?,?,?)", edges)
    conn.commit()
    return conn


def test_empty_graph_never_raises():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    assert expand_over_edges([], conn) == []
    assert expand_over_edges([1, 2], conn) == []
    assert map_neighbors_to_chunks(conn, [1]) == []
    conn.close()


def test_seeds_excluded_and_seed_seed_edges_skipped():
    conn = _mk_conn()
    out = expand_over_edges([1, 2], conn)
    ids = [r["resource_id"] for r in out]
    assert 1 not in ids and 2 not in ids
    assert all(r["resource_id"] != r["via_seed"] for r in out)
    # edge 1->2 (both seeds) never appears; 2's neighbor 104 comes via 2 only
    via_104 = [r for r in out if r["resource_id"] == 104]
    assert len(via_104) == 1 and via_104[0]["via_seed"] in (1, 2)
    conn.close()


def test_typed_edges_outrank_generic_at_equal_weight():
    conn = _mk_conn()
    out = expand_over_edges([1], conn, per_seed_cap=5)
    # seed 1's candidates: 102 alt(.95), 101 doc(.9), 103-105 similar(.9*.85=.765)
    order = [r["resource_id"] for r in out]
    assert order.index(102) < order.index(101) < order.index(103)
    assert out[0]["edge_type"] == "alternative_to"
    assert out[1]["edge_type"] == "documented_by"
    conn.close()


def test_per_seed_cap_and_max_seeds_bound_fanout():
    conn = _mk_conn()
    out = expand_over_edges([1], conn, per_seed_cap=3)
    assert len(out) == 3
    # bound: max_seeds * per_seed_cap even with many seeds
    many = list(range(1, 3))
    out2 = expand_over_edges(many, conn, per_seed_cap=5, max_seeds=10)
    assert len(out2) <= 10 * 5
    # max_seeds truncates expansion to the strongest seeds (input order)
    out3 = expand_over_edges([1, 2], conn, per_seed_cap=5, max_seeds=1)
    assert all(r["via_seed"] == 1 for r in out3)
    conn.close()


def test_edge_types_filter_and_invalid_caps():
    conn = _mk_conn()
    out = expand_over_edges([1], conn, edge_types=("documented_by",))
    assert out and all(r["edge_type"] == "documented_by" for r in out)
    assert expand_over_edges([1], conn, per_seed_cap=0) == []
    assert expand_over_edges([1], conn, max_seeds=0) == []
    with pytest.raises(ValueError):
        expand_over_edges([1], conn, hops=2)
    conn.close()


def test_deterministic_ordering():
    conn = _mk_conn()
    a = expand_over_edges([1, 2], conn, per_seed_cap=5)
    b = expand_over_edges([1, 2], conn, per_seed_cap=5)
    assert a == b
    # tie-break: equal eff keeps resource_id asc
    effs = [EDGE_TYPE_BOOST[r["edge_type"]] * r["weight"] for r in a]
    assert all(effs[i] >= effs[i + 1] or a[i]["resource_id"] < a[i + 1]["resource_id"]
               for i in range(len(a) - 1))
    conn.close()


def test_map_neighbors_to_chunks_representative_and_drops():
    conn = _mk_conn()
    conn.executemany(
        "INSERT INTO ingestion_chunks (id, resource_id, content) VALUES (?,?,?)",
        [(50, 101, "a"), (51, 101, "b"), (60, 102, "c")])
    conn.executemany(
        "INSERT INTO resource_sections (id, resource_id, content) VALUES (?,?,?)",
        [(70, 101, "s1"), (71, 101, "s2")])
    conn.commit()
    out = map_neighbors_to_chunks(conn, [101, 102, 999], corpus="chunks")
    assert out == [(101, 50), (102, 60)]          # lowest id wins, 999 dropped
    out_s = map_neighbors_to_chunks(conn, [101], corpus="sections")
    assert out_s == [(101, 70)]
    with pytest.raises(ValueError):
        map_neighbors_to_chunks(conn, [101], corpus="bogus")
    conn.close()


def test_rrf_without_graph_is_pre_lane_identical():
    fts = [{"chunk_id": 10}, {"chunk_id": 20}]
    sem = [{"chunk_id": 20, "cos": 0.9}, {"chunk_id": 30, "cos": 0.8}]
    out = _rrf(fts, sem)
    by = {r["chunk_id"]: r for r in out}
    k = 60
    # chunk20: fts rank2 + sem rank1; chunk10: fts rank1; chunk30: sem rank2
    assert abs(by[20]["rrf_score"] - (1 / (k + 2) + 1 / (k + 1))) < 1e-12
    assert abs(by[10]["rrf_score"] - 1 / (k + 1)) < 1e-12
    assert abs(by[30]["rrf_score"] - 1 / (k + 2)) < 1e-12
    assert by[20]["source"] == "both"
    assert by[10]["source"] == "fts"
    assert by[30]["source"] == "sem"
    assert all(r["graph_rank"] is None for r in out)
    # explicit empty graph == no graph
    assert _rrf(fts, sem, graph=[], graph_weight=0.95) == out


def test_rrf_graph_list_folds_with_weight_and_labels():
    fts = [{"chunk_id": 10}]
    sem = [{"chunk_id": 20, "cos": 0.9}]
    graph = [{"chunk_id": 30}, {"chunk_id": 10}]
    out = _rrf(fts, sem, graph=graph, graph_weight=0.95)
    by = {r["chunk_id"]: r for r in out}
    k = 60
    assert by[30]["source"] == "graph"
    assert abs(by[30]["rrf_score"] - 0.95 / (k + 1)) < 1e-12   # weighted term
    assert abs(by[10]["rrf_score"] - (1 / (k + 1) + 0.95 / (k + 2))) < 1e-12
    assert by[10]["source"] == "fts+graph"
    assert by[10]["graph_rank"] == 2 and by[10]["fts_rank"] == 1
    # 10 = text rank1 + graph rank2 beats sem-only rank1
    assert out[0]["chunk_id"] == 10
    # weight scales the graph contribution
    half = {r["chunk_id"]: r for r in
            _rrf(fts, sem, graph=graph, graph_weight=0.5)}
    assert half[30]["rrf_score"] < by[30]["rrf_score"]


def test_lane_enabled_flag(monkeypatch):
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "0")
    assert lane_enabled() is False
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "1")
    assert lane_enabled() is True
    monkeypatch.delenv("NEXUS_GRAPH_LANE")
    assert lane_enabled() is True           # default on


# ── restored-brain acceptance ────────────────────────────────────────────────

def _brain_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{BRAIN}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def test_brain_expand_returns_typed_neighbors_best_first():
    conn = _brain_ro()
    row = conn.execute(
        "SELECT source_id AS skill, target_id AS doc FROM resource_edges "
        "WHERE edge_type='documented_by' LIMIT 1").fetchone()
    assert row is not None, "restored brain must carry documented_by edges"
    skill, doc = row["skill"], row["doc"]
    out = expand_over_edges([skill, doc], conn)
    assert out, "known seeds must expand"
    ids = [r["resource_id"] for r in out]
    assert skill not in ids and doc not in ids
    # typed edges present (this seed's neighborhood includes the typed pair)
    assert any(r["edge_type"] == "documented_by" for r in out) or \
        any(r["edge_type"] in DEFAULT_EDGE_TYPES for r in out)
    # fan-out bound
    assert len(out) <= 10 * 5
    # best-first by effective weight
    effs = [EDGE_TYPE_BOOST[r["edge_type"]] * r["weight"] for r in out]
    assert effs == sorted(effs, reverse=True)
    conn.close()


def test_brain_expand_alternative_to_neighbors():
    conn = _brain_ro()
    row = conn.execute(
        "SELECT source_id AS a, target_id AS b FROM resource_edges "
        "WHERE edge_type='alternative_to' LIMIT 1").fetchone()
    assert row is not None, "restored brain must carry alternative_to edges"
    out = expand_over_edges([row["a"]], conn)
    assert any(r["resource_id"] == row["b"] and
               r["edge_type"] == "alternative_to" for r in out)
    conn.close()


def test_retrieve_chunks_fold_changes_ordering_flag_gated(monkeypatch):
    q = "how do I debug a failing tool invocation"
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "0")
    off = retrieve_chunks(q, top_k=10, corpus="chunks")
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "1")   # default weight 0.95
    on = retrieve_chunks(q, top_k=10, corpus="chunks")
    off_ids = [r["chunk_id"] for r in off["results"]]
    on_ids = [r["chunk_id"] for r in on["results"]]
    # lane off must not carry graph-sourced rows
    assert all("graph" not in r["source"] for r in off["results"])
    # lane on changes ordering AND introduces graph entries (lane does work)
    assert off_ids != on_ids
    assert any("graph" in r["source"] for r in on["results"])
    # bounded output preserved
    assert off["n"] == 10 and on["n"] == 10


def test_graph_lane_param_overrides_env(monkeypatch):
    q = "how do I debug a failing tool invocation"
    # explicit off beats env on (router chunk stream path)
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "1")
    forced_off = retrieve_chunks(q, top_k=10, corpus="chunks",
                                 graph_lane=False)
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "0")
    env_off = retrieve_chunks(q, top_k=10, corpus="chunks")  # param None→env
    assert all("graph" not in r["source"] for r in forced_off["results"])
    assert [r["chunk_id"] for r in forced_off["results"]] == \
           [r["chunk_id"] for r in env_off["results"]]
    # explicit on beats env off (RAG consumers)
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "0")
    forced_on = retrieve_chunks(q, top_k=10, corpus="chunks", graph_lane=True)
    assert any("graph" in r["source"] for r in forced_on["results"])


def test_promote_mode_never_emits_graph_only_rows(monkeypatch):
    """promote attaches neighbors to TEXT-pool chunks only — a row sourced
    purely from 'graph' is impossible in promote mode (inject's signature)."""
    q = "how do I debug a failing tool invocation"
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "1")
    monkeypatch.setenv("NEXUS_GRAPH_LANE_MODE", "promote")
    on = retrieve_chunks(q, top_k=10, corpus="chunks")
    graph_rows = [r for r in on["results"] if "graph" in r["source"]]
    assert graph_rows, "promote still does work (boosts text candidates)"
    assert all(r["source"] != "graph" for r in graph_rows)


def test_cluster_gate_drops_out_of_cluster_neighbors(monkeypatch):
    """Neighbors outside the caller's clusters must not occupy the window."""
    import router_v2.chunk_retrieval as cr
    q = "retro"                       # FTS matches on the restored brain
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "0")
    text = retrieve_chunks(q, top_k=10, corpus="chunks", vec=False)
    assert text["results"], "probe needs text hits to anchor a cluster"
    anchor = text["results"][0]
    anchor_cluster, anchor_rid = anchor["cluster"], anchor["resource_id"]
    conn = _brain_ro()
    row = conn.execute(
        "SELECT c.resource_id AS rid FROM ingestion_chunks c "
        "JOIN resources r ON r.id = c.resource_id "
        "WHERE r.cluster IS NOT NULL AND r.cluster != ? "
        "AND c.resource_id != ? LIMIT 1",
        (anchor_cluster, anchor_rid)).fetchone()
    conn.close()
    assert row is not None
    neighbor = [{"resource_id": row["rid"], "edge_type": "similar_to",
                 "weight": 0.9, "via_seed": 1}]
    monkeypatch.setattr(cr, "expand_over_edges",
                        lambda *a, **k: list(neighbor))
    monkeypatch.setenv("NEXUS_GRAPH_LANE", "1")
    monkeypatch.setenv("NEXUS_GRAPH_LANE_MODE", "inject")
    ungated = retrieve_chunks(q, top_k=10, corpus="chunks", vec=False,
                              clusters=None)
    assert any("graph" in r["source"] for r in ungated["results"]), \
        "with no cluster constraint the neighbor folds in"
    gated = retrieve_chunks(q, top_k=10, corpus="chunks", vec=False,
                            clusters=[anchor_cluster])
    assert gated["results"], "in-cluster text hits survive the constraint"
    assert all("graph" not in r["source"] for r in gated["results"]), \
        "out-of-cluster neighbor must be gated before the fold"
