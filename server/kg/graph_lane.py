"""Graph-expansion retrieval lane (second-brain-kg L1-b).

A **1-hop** expansion over typed ``resource_edges``: given the strongest seed
``resource_id``s from the existing hybrid retrieval, walk one hop over typed
edges (``alternative_to``, ``documented_by``) plus weighted generic edges
(``similar_to``, ``co_triggered``) and emit an edge-ranked neighbor list the
caller folds into the existing RRF fusion as one more ranked input.

It is a **library, not a router** — no scoring, tiering, or gating. Research
grounding (ADR-002): RRF (Cormack SIGIR 2009, k=60) as the fusion primitive;
RAG-Fusion `arXiv:2402.03367` (extra source list, fused not appended);
retrieve-and-rerank skills `arXiv:2603.22455` (neighbors of strong seeds are
high-value candidates); Tool Attention `arXiv:2604.21816` + Lost in the
Middle `arXiv:2307.03172` (hard caps: top-N seeds, <=K neighbors, one hop).

Invariants: single hop (multi-hop deferred to L3), bounded fan-out
``max_seeds * per_seed_cap``, read-only, deterministic ordering
``(effective_weight desc, resource_id asc)``, never raises on an empty graph.
Imports nothing from ``router_v2`` (keeps the G17 import cycle clean).
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_EDGE_TYPES: tuple[str, ...] = (
    "alternative_to", "documented_by", "similar_to", "co_triggered")

# Typed edges outrank generic similarity when both reach the same neighbor.
# Tunable (second-brain-kg change doc: "typed edges > generic similar_to").
EDGE_TYPE_BOOST: dict[str, float] = {
    "alternative_to": 1.0,
    "documented_by": 1.0,
    "similar_to": 0.85,
    "co_triggered": 0.85,
}


def lane_enabled() -> bool:
    """Feature flag for the fold: NEXUS_GRAPH_LANE=0 disables (rollback valve)."""
    return os.environ.get("NEXUS_GRAPH_LANE", "1").strip().lower() \
        not in ("0", "false", "off", "no")


def expand_over_edges(
    seed_ids: list[int],
    conn: sqlite3.Connection,
    *,
    edge_types: tuple[str, ...] = DEFAULT_EDGE_TYPES,
    per_seed_cap: int = 5,
    max_seeds: int = 10,
    hops: int = 1,
) -> list[dict]:
    """Return edge-ranked neighbors as ``[{resource_id, edge_type, weight,
    via_seed}]``, best-first. Excludes ``seed_ids`` themselves.

    Deterministic: candidates are ranked by ``weight * EDGE_TYPE_BOOST``
    (desc), tie-broken by ``resource_id`` (asc). Bounded by
    ``max_seeds * per_seed_cap`` rows. Never raises on an empty graph/edge set.
    """
    if hops != 1:
        raise ValueError("graph_lane L1 supports hops=1 only (multi-hop is L3)")
    if per_seed_cap <= 0 or max_seeds <= 0:
        return []
    seeds: list[int] = []
    seen: set[int] = set()
    for s in (seed_ids or [])[:max_seeds]:
        try:
            sid = int(s)
        except (TypeError, ValueError):
            continue
        if sid not in seen:
            seen.add(sid)
            seeds.append(sid)
    if not seeds or not edge_types:
        return []
    seed_set = set(seeds)
    et_ph = ",".join("?" for _ in edge_types)
    id_ph = ",".join("?" for _ in seeds)
    sql = (
        "SELECT source_id, target_id, edge_type, weight FROM resource_edges "
        f"WHERE edge_type IN ({et_ph}) AND "
        f"(source_id IN ({id_ph}) OR target_id IN ({id_ph}))"
    )
    rows = conn.execute(sql, (*edge_types, *seeds, *seeds)).fetchall()
    if not rows:
        return []

    # group candidates per via_seed (an edge touches at most one seed endpoint)
    per_seed: dict[int, list[tuple[float, int, str, float]]] = {}
    for src, tgt, et, w in rows:
        src_in, tgt_in = src in seed_set, tgt in seed_set
        if src_in and tgt_in:
            continue                      # seed->seed edge: no external neighbor
        via = src if src_in else tgt
        if via not in seed_set:
            continue                      # unreachable with the WHERE clause
        nid = tgt if via == src else src
        if nid in seed_set:
            continue                      # seeds are never returned
        raw = float(w) if w is not None else 1.0
        eff = raw * EDGE_TYPE_BOOST.get(et, 1.0)
        per_seed.setdefault(via, []).append((eff, nid, et, raw))

    # per-seed cap: best (eff desc, id asc) first; keep global best per neighbor
    best: dict[int, tuple[float, int, str, float, int]] = {}
    for via in seeds:                     # seeds order, not dict order: deterministic
        cands = per_seed.get(via)
        if not cands:
            continue
        cands.sort(key=lambda t: (-t[0], t[1]))
        for eff, nid, et, raw in cands[:per_seed_cap]:
            cur = best.get(nid)
            if cur is None or eff > cur[0]:   # same nid => tie keeps earlier seed
                best[nid] = (eff, nid, et, raw, via)

    out = [
        {"resource_id": nid, "edge_type": et, "weight": raw, "via_seed": via}
        for eff, nid, et, raw, via in best.values()
    ]
    out.sort(key=lambda d: (-best[d["resource_id"]][0], d["resource_id"]))
    return out


def map_neighbors_to_chunks(
    conn: sqlite3.Connection,
    resource_ids: list[int],
    *,
    corpus: str = "chunks",
) -> list[tuple[int, int]]:
    """One representative chunk per resource, ranked by input order.

    ``[(resource_id, chunk_id)]`` — deterministic (lowest chunk/section id
    wins). Resources with no rows in the corpus table are dropped. Corpus
    tables mirror ``chunk_retrieval``: ``ingestion_chunks`` (chunk id space
    shared with ``_rrf``) or ``resource_sections`` (sections corpus, D1).
    """
    rids = [int(r) for r in resource_ids if r is not None]
    if not rids:
        return []
    if corpus == "chunks":
        table = "ingestion_chunks"
    elif corpus == "sections":
        table = "resource_sections"
    else:
        raise ValueError(f"unknown corpus {corpus!r} (expected 'chunks'|'sections')")
    ph = ",".join("?" for _ in rids)
    rows = conn.execute(
        f"SELECT resource_id, id FROM ("
        f"SELECT resource_id, id, ROW_NUMBER() OVER ("
        f"PARTITION BY resource_id ORDER BY id) AS rn "
        f"FROM {table} WHERE resource_id IN ({ph}) AND resource_id IS NOT NULL"
        f") WHERE rn = 1",
        tuple(rids),
    ).fetchall()
    rep = {int(r[0]): int(r[1]) for r in rows}
    return [(rid, rep[rid]) for rid in rids if rid in rep]
