"""7h typed edges (second-brain-kg L1-a) — property + reversibility tests.

Mini in-memory DB: no embeddings corpus, no prod access. Complements the live
verification run against a copy of the real brain (recorded in the change doc).
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import densify_kg as dk  # noqa: E402  (scripts/ on path)

SCHEMA = """
CREATE TABLE resources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL,
    body_text TEXT NOT NULL DEFAULT '',
    cluster TEXT
);
CREATE TABLE resource_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    co_trigger_count INTEGER DEFAULT 0,
    updated_at TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, edge_type)
);
"""


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # cluster "alpha": a1,a2,a3 substitutes (cos >= 0.82); b1 in cluster "beta"
    conn.executemany(
        "INSERT INTO resources (id, name, resource_type, cluster, body_text) "
        "VALUES (?,?,?,?,?)",
        [(1, "skill-foo", "skill", "alpha", ""),
         (2, "skill-foo-alt", "skill", "alpha", ""),
         (3, "skill-foo-alt2", "skill", "alpha", ""),
         (4, "skill-other", "skill", "beta", ""),
         (5, "skill-nullcluster", "skill", None, ""),
         (6, "skills-bar", "doc", None,
          "Explains [[skill-foo]] and [[skill-foo-alt|alt]]. Also [[topic-9]]."),
         (7, "topic-baz", "topic", None, "See [[skills/bar]] (unresolvable noise)."),
         (8, "skills-bar2", "doc", None, "no links here"),
         (9, "topic-9", "topic", None, "")])
    # co_triggered evidence: 1 <-> 2 are complements, never alternatives
    conn.execute(
        "INSERT INTO resource_edges (source_id, target_id, edge_type, weight, "
        "co_trigger_count) VALUES (1, 2, 'co_triggered', 0.9, 9)")
    conn.commit()
    return conn


def _embeddings(ids, dim=384):
    rng = np.random.default_rng(7)
    mat = rng.standard_normal((len(ids), dim)).astype(np.float32)
    # make 1,2,3 near-identical (cos >= 0.82), 4 different
    base = rng.standard_normal(dim).astype(np.float32)
    mat[0] = base
    mat[1] = base + 0.05 * rng.standard_normal(dim).astype(np.float32)
    mat[2] = base + 0.05 * rng.standard_normal(dim).astype(np.float32)
    mat[3] = rng.standard_normal(dim).astype(np.float32)
    mat[4] = rng.standard_normal(dim).astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    return ids, mat / norms


def test_7h_dry_run_counts_nonzero():
    conn = _mk_conn()
    n_alt, n_doc = dk.pass_7h_typed_edges(conn, _embeddings([1, 2, 3, 4, 5])[0],
                                          _embeddings([1, 2, 3, 4, 5])[1],
                                          dry_run=True)
    assert n_alt >= 1 and n_doc >= 1
    # dry-run wrote nothing
    assert conn.execute(
        "SELECT COUNT(*) FROM resource_edges WHERE edge_type IN "
        "('alternative_to','documented_by')").fetchone()[0] == 0
    conn.close()


def test_7h_alternative_to_same_cluster_and_not_cotrigged():
    conn = _mk_conn()
    ids, mat = _embeddings([1, 2, 3, 4, 5])
    dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    rows = conn.execute(
        "SELECT source_id, target_id FROM resource_edges "
        "WHERE edge_type='alternative_to'").fetchall()
    assert rows, "expected alternative_to edges among the 0.82+ triplet"
    clusters = dict(conn.execute("SELECT id, cluster FROM resources"))
    cotrig = {(1, 2), (2, 1)}
    for r in rows:
        a, b = r["source_id"], r["target_id"]
        # same, non-NULL cluster
        assert clusters[a] is not None and clusters[a] == clusters[b], (a, b)
        # never a co-triggered pair
        assert (a, b) not in cotrig
    # cross-cluster pair (1,4) and NULL-cluster node 5 must be absent
    pairs = {(r["source_id"], r["target_id"]) for r in rows}
    assert not any(5 in p for p in pairs)
    assert (1, 4) not in pairs and (4, 1) not in pairs
    conn.close()


def test_7h_documented_by_direction_and_types():
    conn = _mk_conn()
    ids, mat = _embeddings([1, 2, 3, 4, 5])
    dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    rows = conn.execute(
        "SELECT source_id, target_id, weight FROM resource_edges "
        "WHERE edge_type='documented_by'").fetchall()
    types = dict(conn.execute("SELECT id, resource_type FROM resources"))
    pairs = {(r["source_id"], r["target_id"]) for r in rows}
    # skill-foo documented BY skills-bar (wikilink) and by its name-match doc
    assert any(src == 1 for src, _ in pairs), pairs
    # explainer -> explainer chains (doc links to topic) are NOT documented_by
    assert (9, 6) not in pairs
    # direction: source = documented skill/tool, target = explaining doc-ish type
    for src, tgt in pairs:
        assert types[src] in ("skill", "agent", "command", "cli_tool", "mcp", "hook")
        assert types[tgt] in ("doc", "spec", "topic")
    # repeated wikilink (skill-foo in body once) vs name-only match (skill 8? none)
    conn.close()


def test_7h_idempotent_second_run_adds_nothing():
    conn = _mk_conn()
    ids, mat = _embeddings([1, 2, 3, 4, 5])
    dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    first = conn.execute(
        "SELECT COUNT(*) FROM resource_edges WHERE edge_type IN "
        "('alternative_to','documented_by')").fetchone()[0]
    n_alt2, n_doc2 = dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    assert (n_alt2, n_doc2) == (0, 0)
    second = conn.execute(
        "SELECT COUNT(*) FROM resource_edges WHERE edge_type IN "
        "('alternative_to','documented_by')").fetchone()[0]
    assert second == first
    conn.close()


def test_7h_cap_is_cumulative_per_node():
    """Cap=4 incident edges per node must hold ACROSS runs (not per-run)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    ids = list(range(10, 18))                    # 8 nodes, all in cluster "gamma"
    conn.executemany(
        "INSERT INTO resources (id, name, resource_type, cluster) VALUES (?,?,?,?)",
        [(i, f"skill-cap-{i}", "skill", "gamma") for i in ids])
    conn.commit()
    rng = np.random.default_rng(11)
    base = rng.standard_normal(384).astype(np.float32)
    mat = np.stack([base + 0.01 * rng.standard_normal(384).astype(np.float32)
                    for _ in ids])
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)   # all cos ~1.0
    n1, _ = dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    assert n1 > 0
    incident = {}
    for a, b in conn.execute(
            "SELECT source_id, target_id FROM resource_edges "
            "WHERE edge_type='alternative_to'"):
        incident[a] = incident.get(a, 0) + 1
        incident[b] = incident.get(b, 0) + 1
    assert incident and max(incident.values()) <= dk.TYPED_ALT_CAP, incident
    # second run adds nothing (cap already reached for every candidate node)
    n2, _ = dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    assert n2 == 0
    conn.close()


def test_7h_reversible_via_run_ts():
    conn = _mk_conn()
    ids, mat = _embeddings([1, 2, 3, 4, 5])
    before = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    dk.pass_7h_typed_edges(conn, ids, mat, dry_run=False)
    # the pass stamps its own run_ts — recover it, then reverse like the docs say
    run_ts = conn.execute(
        "SELECT DISTINCT created_at FROM resource_edges WHERE edge_type IN "
        "('alternative_to','documented_by')").fetchone()[0]
    assert run_ts >= ts  # same-second or later
    conn.execute(
        "DELETE FROM resource_edges WHERE created_at >= ? AND edge_type IN "
        "('alternative_to','documented_by')", (run_ts,))
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
    assert after == before
    assert conn.execute(
        "SELECT COUNT(*) FROM resource_edges WHERE edge_type IN "
        "('alternative_to','documented_by')").fetchone()[0] == 0
    conn.close()
