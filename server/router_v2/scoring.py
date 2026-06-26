"""P2/P6: DB-backed scoring inputs for the unified router's 4-term blend.

Builds the three learning-signal dicts (Thompson posterior means, normalized
EFC, UCB1 bonuses) from the live DB, then calls fusion.rerank_with_efc_ucb.

This is the integration glue between router_v2's pure fusion functions and
the NEXUS learning tables (outcome_log, resources, routing_log). It is the
single entry point the unified router uses for the 4-term combined_score:

    combined = 0.40*iso + 0.15*thompson + 0.25*efc + 0.20*ucb

Two-tier cold-start routing (Paper 3: ToolkenGPT) splits top_k into a hot
tier (times_triggered > 0, Thompson-driven) and a cold tier (never triggered,
UCB-driven) and merges them.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Sequence

from fusion import (
    DEFAULT_BLEND_WEIGHTS,
    reciprocal_rank_fusion,
    rerank_with_efc_ucb,
    two_tier_split,
    ucb1_bonus,
)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "db" / "nexus.db"


def _connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def thompson_posteriors(conn: sqlite3.Connection,
                        resource_ids: list[int]) -> dict[int, float]:
    """Beta(1+correct, 1+wrong) posterior mean per resource.

    Mean = alpha / (alpha + beta). Resources with no outcomes default to 0.5
    (uninformative prior), NOT 0 — so cold resources aren't zeroed out.
    """
    if not resource_ids:
        return {}
    placeholders = ",".join("?" for _ in resource_ids)
    rows = conn.execute(
        f"SELECT id, times_correct, times_wrong FROM resources "
        f"WHERE id IN ({placeholders})",
        tuple(resource_ids),
    ).fetchall()
    out: dict[int, float] = {}
    for r in rows:
        alpha = (r["times_correct"] or 0) + 1
        beta = (r["times_wrong"] or 0) + 1
        out[r["id"]] = alpha / (alpha + beta)
    return out


def efc_normalized(conn: sqlite3.Connection,
                   resource_ids: list[int],
                   cap: float = 10.0) -> dict[int, float]:
    """Mean EFC per resource, normalized to [0, 1] (cap = κ = 10).

    Resources with no EFC data contribute 0 (no learning signal yet).
    """
    if not resource_ids:
        return {}
    placeholders = ",".join("?" for _ in resource_ids)
    rows = conn.execute(
        f"SELECT resource_id, AVG(efc_score) as avg_efc FROM outcome_log "
        f"WHERE efc_score IS NOT NULL AND resource_id IN ({placeholders}) "
        f"GROUP BY resource_id",
        tuple(resource_ids),
    ).fetchall()
    return {r["resource_id"]: min((r["avg_efc"] or 0.0) / cap, 1.0) for r in rows}


def ucb_bonuses(conn: sqlite3.Connection,
                resource_ids: list[int],
                total_pulls: int | None = None) -> dict[int, float]:
    """UCB1 exploration bonus per resource (1.0 for never-triggered)."""
    if total_pulls is None:
        total_pulls = conn.execute(
            "SELECT COALESCE(SUM(times_triggered), 0) FROM resources"
        ).fetchone()[0] or 1
    if not resource_ids:
        return {}
    placeholders = ",".join("?" for _ in resource_ids)
    rows = conn.execute(
        f"SELECT id, times_triggered FROM resources WHERE id IN ({placeholders})",
        tuple(resource_ids),
    ).fetchall()
    return {r["id"]: ucb1_bonus(r["times_triggered"] or 0, total_pulls) for r in rows}


def rank(fused: Sequence[dict],
         db_path: str | Path = DB_PATH,
         weights: dict[str, float] | None = None) -> list[dict]:
    """Apply the 4-term blend to a fused list.

    ``fused`` entries must contain ``resource_id`` and an ISO/RRF score field
    (``rrf_score`` from reciprocal_rank_fusion).
    """
    rids = [x["resource_id"] for x in fused if x.get("resource_id") is not None]
    if not rids:
        # no resource ids (cold FTS-only path) — return as-is with final=rrf
        return [{**x, "final_score": x.get("rrf_score", 0)} for x in fused]
    conn = _connect(db_path)
    try:
        t = thompson_posteriors(conn, rids)
        e = efc_normalized(conn, rids)
        u = ucb_bonuses(conn, rids)
    finally:
        conn.close()
    return rerank_with_efc_ucb(fused, t, e, u, weights)


def route_two_tier(query: str, fts_results, vec_results, *,
                   db_path: str | Path = DB_PATH,
                   top_k: int = 10,
                   cold_fraction: float = 0.3,
                   weights: dict[str, float] | None = None) -> dict:
    """Two-tier hot/cold routing (Paper 3: ToolkenGPT).

    Hot tier: top resources by the 4-term blend (times_triggered > 0).
    Cold tier: never-triggered resources ranked by UCB + semantic similarity,
    mixed in at cold_fraction (default 30%).
    """
    hot_k, cold_k = two_tier_split(top_k, cold_fraction)
    fused = reciprocal_rank_fusion(fts_results, vec_results)

    # split fused into hot (triggered) and cold (never triggered) by DB lookup
    rids = [x["resource_id"] for x in fused if x.get("resource_id") is not None]
    triggered: set[int] = set()
    if rids:
        conn = _connect(db_path)
        try:
            for r in conn.execute(
                f"SELECT id FROM resources WHERE id IN ({','.join('?'*len(rids))}) "
                f"AND times_triggered > 0",
                tuple(rids),
            ).fetchall():
                triggered.add(r["id"])
        finally:
            conn.close()

    hot_fused = [x for x in fused if x.get("resource_id") in triggered]
    cold_fused = [x for x in fused if x.get("resource_id") not in triggered]

    hot_ranked = rank(hot_fused, db_path, weights)[:hot_k] if hot_fused else []
    # cold tier: rank by UCB alone (semantic iso + ucb, no thompson/efc)
    cold_ranked = rank(cold_fused, db_path,
                       weights={"iso": 0.5, "thompson": 0.0, "efc": 0.0, "ucb": 0.5}
                       )[:cold_k] if cold_fused else []

    return {
        "query": query,
        "hot": hot_ranked,
        "cold": cold_ranked,
        "hot_k": hot_k,
        "cold_k": cold_k,
        "merged": hot_ranked + cold_ranked,
    }


if __name__ == "__main__":
    # smoke test: synthetic fused input + real DB scoring
    import json
    conn = _connect()
    # pick 5 real resource ids
    rids = [r["id"] for r in conn.execute(
        "SELECT id FROM resources WHERE times_triggered > 0 LIMIT 3"
    ).fetchall()]
    rids += [r["id"] for r in conn.execute(
        "SELECT id FROM resources WHERE times_triggered = 0 LIMIT 2"
    ).fetchall()]
    conn.close()
    fused = [{"resource_id": rid, "rrf_score": 0.5 - i * 0.05} for i, rid in enumerate(rids)]
    out = rank(fused)
    print(json.dumps([{k: round(v, 4) if isinstance(v, float) else v for k, v in x.items()}
                      for x in out], indent=2))
    print(f"\nweights: {DEFAULT_BLEND_WEIGHTS}")
    print(f"two_tier_split(10): {two_tier_split(10)}")