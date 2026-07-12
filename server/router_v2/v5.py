"""unified_route_v5 — HGMF intent→cluster→retrieve→rerank (the refiner unifier).

Research grounding:
  - HGMF (arXiv:2508.07602): hierarchical cluster → retrieve → rerank. L1 picks the
    cluster, L2 retrieves within it, L3 reranks. The unifier is a REFINER, not a
    fusion function (Tool2Vec / ToolRefiner, arXiv:2409.02141) — so L3 reranks L2's
    topic candidates with L1's intent signal rather than running a fresh RRF merge.
  - Bruch et al. (arXiv:2210.11934): RRF generalizes poorly across heterogeneous
    scales; the v5 hand-tuned blend replaces resource-keyed RRF with a fixed,
    interpretable 4-term weight vector tuned for cross-router agreement.
  - ToolkenGPT (arXiv:2310.03054): two-tier hot/cold split preserved from v2.

Pipeline:
  L1 intent   : route_task(query, use_embedding=True) → skills, cluster, confidence
  L1 short-ckt: if confidence > θ AND |cluster pool| ≥ N → return L1 skills (autocut)
  L2 topic    : unified_route CONSTRAINED to top-2 clusters ([detected, "general"])
  L3 rerank   : 0.45·topic + 0.25·intent + 0.20·skill_match + 0.10·listwise
  L4 autocut  : drop trailing results below 0.5·max_score (natural cluster break)

Targets (from plan): cross-router agreement >60% (from 39.6/41.7%); latency p95
<300ms (warm; cold L1 embedder load dominates first call).

This module is read-only w.r.t. the live MCP and the Thompson tables: it only reads
route_task and unified_route outputs. Default OFF — wired in by P4 (USE_UNIFIED_ROUTER=2).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_SELF = Path(__file__).resolve().parent          # server/router_v2/
_SERVER = _SELF.parent                             # server/
_REPO = _SERVER.parent                              # nexus/
for _p in (str(_REPO), str(_SERVER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)  # router.py imports `core` → repo root must be on path

from router_v2 import unified_route  # noqa: E402
from router_v2.fusion import (  # noqa: E402  — L3 fusion math (P3)
    v5_get_weights, v5_fusion_scores, v5_agreement,
)

DB_PATH = _REPO / "db" / "nexus.db"


def _load_reflexion_context(conn) -> tuple[dict[int, list[tuple[str, int]]], set[str]]:
    """Port of router.py:446-472 reflexion consultation for v5 (P1 Step 1.0).

    Returns (fixes_map, recent_failures):
      fixes_map: resource_id → [(alternative_name, times_successful), ...]
                 built from reflection_fixes joined to resources via
                 alternative_resource_id (migration 017), with a free-text
                 fallback ("Use X instead") for any pre-migration rows.
      recent_failures: lowercased resource_name set with unfixed failures.

    Used by _v5_rerank to downweight failed resources (*0.7) and upweight
    known-successful alternatives (+0.1·times_successful). Read-only.
    """
    fixes_map: dict[int, list[tuple[str, int]]] = {}
    recent_failures: set[str] = set()
    try:
        # Successful alternatives — prefer the alternative_resource_id FK
        # (migration 017); fall back to parsing fix_description for legacy rows.
        for f in conn.execute(
            """
            SELECT rf.resource_id AS rid, rf.alternative_resource_id AS alt_id,
                   rf.fix_description AS desc, rf.times_successful AS succ
            FROM reflection_fixes rf
            WHERE rf.times_applied > 0
            """
        ).fetchall():
            rid = f[0]
            if rid is None:
                continue
            alt_id, desc, succ = f[1], f[2] or "", f[3] or 0
            alt_name = None
            if alt_id is not None:
                row = conn.execute(
                    "SELECT name FROM resources WHERE id=?", (alt_id,)
                ).fetchone()
                if row:
                    alt_name = row[0]
            if alt_name is None and desc.startswith("Use ") and desc.endswith(" instead"):
                alt_name = desc[4:-8]
            if alt_name:
                fixes_map.setdefault(rid, []).append((alt_name, succ))
        for fr in conn.execute(
            """
            SELECT resource_name FROM reflection_failures
            WHERE fix_successful IS NULL OR fix_successful = 0
            """
        ).fetchall():
            if fr[0]:
                recent_failures.add(fr[0].lower())
    except Exception:
        # Reflexion is advisory — never let a schema/DB hiccup break routing.
        pass
    return fixes_map, recent_failures

# ── L3 blend weights (hand-tuned; sum to 1.0) ─────────────────────────────────
# Source of truth now lives in router_v2.fusion (v5_get_weights), so P5-bench
# can sweep them via NEXUS_V5_W_<NAME> env without touching this file. These
# module-level aliases preserve the prior public surface and default to the
# exact hand-tuned values (byte-identical when no env is set).
W_TOPIC, W_INTENT, W_SKILLMATCH, W_LISTWISE = (
    v5_get_weights()["topic"], v5_get_weights()["intent"],
    v5_get_weights()["skill_match"], v5_get_weights()["listwise"],
)

# ── Shadow cutover flag (P3) ──────────────────────────────────────────────────
# When NEXUS_ROUTER_SHADOW=true, unified_route_v5 additionally computes the
# weighted-RRF BASELINE on the same candidate set and returns both rankings +
# top-k agreement, so P5-bench can verify Bruch's convex > RRF finding on
# NEXUS data WITHOUT changing the production ranking. Default OFF.
SHADOW_MODE = os.environ.get("NEXUS_ROUTER_SHADOW", "false").lower() == "true"
FUSION_MODE = os.environ.get("NEXUS_V5_FUSION", "convex").lower()  # "convex"|"wrrf"

# ── L1 short-circuit gate ────────────────────────────────────────────────────
SHORT_CIRCUIT_CONFIDENCE = 0.85
SHORT_CIRCUIT_MIN_POOL = 50

# ── L4 autocut ───────────────────────────────────────────────────────────────
# Rank-gap autocut: the L3 skill_match term is binary (1.0 for L1 matches, 0
# otherwise), which creates a natural score CLIFF between L1-matched resources
# and the rest. A score-threshold cut (0.3·max) would shrink the set to just the
# L1 matches (~3). Instead, keep top_k and cut only at a large consecutive score
# gap (AUTOCUT_GAP) — the natural cluster break in the ranking.
AUTOCUT_GAP = 0.40


def _autocut(merged: list[dict], top_k: int) -> list[dict]:
    """L4 rank-gap autocut: keep best-first results up to top_k, but stop at the
    first consecutive score gap > AUTOCUT_GAP (a natural cluster break). Always
    keeps at least one result; never shrinks below top_k//2 unless the gap fires
    in the first half (genuinely sparse)."""
    if not merged:
        return []
    top = merged[:top_k]
    if len(top) <= 1:
        return top
    out = [top[0]]
    for i in range(1, len(top)):
        prev = top[i - 1].get("final_score", 0.0) or 0.0
        cur = top[i].get("final_score", 0.0) or 0.0
        if prev - cur > AUTOCUT_GAP and len(out) >= max(1, top_k // 2):
            break  # natural cluster break — stop here
        out.append(top[i])
    return out


def _name_to_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["name"]: r["id"] for r in conn.execute(
        "SELECT id, name FROM resources WHERE name IS NOT NULL").fetchall()}


def _cluster_pool_size(conn: sqlite3.Connection, cluster: str | None) -> int:
    """Count active resources in a cluster (cluster_membership, fallback resources.cluster)."""
    if not cluster:
        return 0
    try:
        n = conn.execute(
            "SELECT COUNT(DISTINCT r.id) FROM resources r "
            "JOIN cluster_membership cm ON cm.resource_id=r.id "
            "WHERE cm.cluster_name=? AND r.status='active'", (cluster,)
        ).fetchone()[0]
        if n:
            return int(n)
    except Exception:
        pass
    # fallback: resources.cluster column
    return int(conn.execute(
        "SELECT COUNT(*) FROM resources WHERE cluster=? AND status='active'",
        (cluster,)
    ).fetchone()[0] or 0)


def _rank_normalize(scores: list[float]) -> list[float]:
    """Map a best-first score list to [0,1] by rank (1.0 at top, →0). Robust to
    heterogeneous scales (Bruch et al.): uses rank, not raw score magnitude."""
    n = len(scores)
    if n == 0:
        return []
    return [1.0 - (i / n) for i in range(n)]


def _minmax(xs: list[float]) -> list[float]:
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi <= lo:
        return [1.0] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]


def _l1_intent(query: str, db_path: str) -> dict:
    """L1: pure v4 intent rank (no learning side effects).

    P1 design plan: write_feedback=False so hierarchical v5 does not
    double-count counters/Thompson for L1's provisional top skill.
    Returns {skills, cluster, confidence, name2id, intent_rids, intent_rank}.
    """
    from router import route_task
    res = route_task(
        query, project="default", use_embedding=True, write_feedback=False,
    )
    skills = res.get("skills", []) if isinstance(res, dict) else []
    cluster = res.get("cluster") if isinstance(res, dict) else None
    confidence = float(res.get("task_confidence", 0.0) or 0.0) if isinstance(res, dict) else 0.0

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        n2i = _name_to_id_map(conn)
    finally:
        conn.close()

    # map L1 skills → resource_ids, rank-normalize their scores to [0,1]
    intent_rids: list[int] = []
    for s in skills:
        rid = n2i.get(s.get("name") if isinstance(s, dict) else None)
        if rid is not None and rid not in intent_rids:
            intent_rids.append(rid)
    intent_rank = {rid: sc for rid, sc in zip(intent_rids, _rank_normalize(
        [float(s.get("score", 0.0) or 0.0) for s in skills
         if n2i.get(s.get("name")) is not None]))}
    return {
        "skills": skills, "cluster": cluster, "confidence": confidence,
        "name2id": n2i, "intent_rids": intent_rids, "intent_rank": intent_rank,
        "raw": res, "latency_ms": float(res.get("latency_ms", 0.0) or 0.0) if isinstance(res, dict) else 0.0,
    }


def _short_circuit(intent: dict, top_k: int, db_path: str) -> list[dict]:
    """L1 short-circuit: build merged from L1's intent skills (resource-keyed),
    hydrate name/cluster, apply autocut. Used when L1 is highly confident."""
    rids = intent["intent_rids"][:top_k * 2]
    if not rids:
        return []
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ph = ",".join("?" for _ in rids)
        rows = conn.execute(
            f"SELECT id, name, cluster, resource_type FROM resources WHERE id IN ({ph})",
            tuple(rids)).fetchall()
        meta = {r["id"]: dict(r) for r in rows}
        triggered = {r["id"] for r in conn.execute(
            f"SELECT id FROM resources WHERE id IN ({ph}) AND times_triggered > 0",
            tuple(rids)).fetchall()}
    finally:
        conn.close()
    ranked = intent["intent_rank"]
    merged = []
    for rid in rids:
        m = meta.get(rid, {})
        merged.append({
            "resource_id": rid, "name": m.get("name"), "cluster": m.get("cluster"),
            "resource_type": m.get("resource_type"),
            "final_score": ranked.get(rid, 0.0),
            "intent_score": ranked.get(rid, 0.0),
            "source": "l1_short_circuit",
            "hot": rid in triggered,
        })
    return _autocut(merged, top_k)


def _v5_components(topic_merged: list[dict], intent: dict) -> tuple:
    """Build the four L3 score arrays aligned by candidate index.

    topic_norm  : min-max normalized final_score from L2 (the topic retrieval)
    intent_arr  : L1 rank-normalized intent score (0 if not surfaced by L1)
    skill_match : 1.0 if the resource is in L1's intent set, else 0.0
    listwise    : reciprocal rank of the resource in the L2 topic list, ∈ (0,1]
                  (normalized by the max reciprocal so the top item = 1.0)

    Returns (rids, topic_norm, intent_arr, sm_arr, lw_arr). The arrays are
    used by BOTH the convex default and the weighted-RRF baseline (ranks are
    monotonic in the normalized values, so the two fusions see the same
    candidate ordering) — centralizing here is what lets the shadow cutover
    compare them on an identical candidate set.
    """
    intent_rank = intent["intent_rank"]
    intent_rids = set(intent["intent_rids"])
    finals = [float(x.get("final_score", 0.0) or 0.0) for x in topic_merged]
    topic_norm = _minmax(finals)
    max_rrf = max((1.0 / (60.0 + i + 1) for i in range(len(topic_merged))), default=1.0)
    rids, intent_arr, sm_arr, lw_arr = [], [], [], []
    for i, x in enumerate(topic_merged):
        rid = x.get("resource_id")
        rids.append(rid)
        intent_arr.append(intent_rank.get(rid, 0.0) if rid is not None else 0.0)
        sm_arr.append(1.0 if rid in intent_rids else 0.0)
        lw_arr.append((1.0 / (60.0 + i + 1)) / max_rrf)  # listwise reciprocal rank, ∈ (0,1]
    return rids, topic_norm, intent_arr, sm_arr, lw_arr


def _v5_rerank(topic_merged: list[dict], intent: dict,
               top_k: int, cold_fraction: float, db_path: str,
               fusion_mode: str = "convex") -> list[dict]:
    """L3 rerank via the fusion module (P3). Default 'convex' = the tuned convex
    combination (Bruch et al. 2022, byte-identical to the prior inline arithmetic
    when no NEXUS_V5_W_* env is set); 'wrrf' = the weighted-RRF BASELINE (Cormack
    et al. 2009) for comparison only. See router_v2/fusion.py for the math."""
    if not topic_merged:
        return []
    rids, topic_norm, intent_arr, sm_arr, lw_arr = _v5_components(topic_merged, intent)
    finals = v5_fusion_scores(topic_norm, intent_arr, sm_arr, lw_arr, mode=fusion_mode)

    # Reflexion consultation (P1 Step 1.0, port of router.py:446-472):
    # downweight resources with unfixed recent failures, upweight resources
    # that are known-successful alternatives for a failed peer. Read-only.
    fixes_map: dict[int, list[tuple[str, int]]] = {}
    recent_failures: set[str] = set()
    try:
        _rc_conn = sqlite3.connect(db_path, timeout=30)
        try:
            fixes_map, recent_failures = _load_reflexion_context(_rc_conn)
        finally:
            _rc_conn.close()
    except Exception:
        pass
    # name → index for upweighting alternatives by name
    name_to_idx = {x.get("name"): i for i, x in enumerate(topic_merged) if x.get("name")}
    if recent_failures or fixes_map:
        for i, x in enumerate(topic_merged):
            nm = (x.get("name") or "").lower()
            if nm and nm in recent_failures:
                finals[i] *= 0.7
        for rid, alts in fixes_map.items():
            for alt_name, succ in alts:
                j = name_to_idx.get(alt_name)
                if j is not None and succ > 0:
                    finals[j] += 0.1 * succ

    scored = []
    for i, x in enumerate(topic_merged):
        scored.append({
            "resource_id": rids[i], "name": x.get("name"), "cluster": x.get("cluster"),
            "resource_type": x.get("resource_type"),
            "final_score": finals[i],
            "topic_score": topic_norm[i], "intent_score": intent_arr[i],
            "skill_match": sm_arr[i], "listwise": lw_arr[i],
            "rrf_score": x.get("rrf_score"), "hot": x.get("hot"),
            "fusion_mode": fusion_mode,
            "source": "v5_rerank",
        })
    scored.sort(key=lambda s: s["final_score"], reverse=True)

    # two-tier hot/cold split (ToolkenGPT): preserve cold_fraction cold picks.
    # Look up triggered status from the DB (unified_route was called with
    # cold_fraction=0.0 so its items carry no hot/cold flag).
    is_canary = os.environ.get("USE_UNIFIED_ROUTER") == "2"
    if cold_fraction > 0.0:
        rids_hot = [s["resource_id"] for s in scored if s.get("resource_id") is not None]
        triggered: set[int] = set()
        if rids_hot:
            conn = sqlite3.connect(db_path, timeout=30)
            try:
                ph = ",".join("?" for _ in rids_hot)
                for r in conn.execute(
                    f"SELECT id FROM resources WHERE id IN ({ph}) AND times_triggered > 0",
                    tuple(rids_hot)).fetchall():
                    triggered.add(r[0])
            finally:
                conn.close()
        for s in scored:
            s["hot"] = s.get("resource_id") in triggered

        # ── Canary: IDS-inspired cold-pool exploration bonus ──────────────
        # When USE_UNIFIED_ROUTER=2, add an information-gain bonus to
        # never-triggered cold items. The bonus is proportional to topic
        # uncertainty: topic*(1-topic) peaks at 0.5 (max information gain),
        # modeling the IDS principle that exploring a resource with maximal
        # posterior uncertainty yields the most information per unit regret.
        # Configurable via NEXUS_CANARY_COLD_BONUS (default 0.15).
        if is_canary:
            canary_bonus = float(os.environ.get("NEXUS_CANARY_COLD_BONUS", "0.15"))
            for s in scored:
                if s.get("hot"):
                    continue
                topic = s.get("topic_score", 0.0) or 0.0
                info_gain = topic * (1.0 - topic)  # IDS: peak uncertainty at 0.5
                s["final_score"] += canary_bonus * info_gain
                s["canary_info_bonus"] = round(canary_bonus * info_gain, 4)
            scored.sort(key=lambda s: s["final_score"], reverse=True)

        hot_k = max(1, int(round(top_k * (1.0 - cold_fraction))))
        cold_k = top_k - hot_k
        hot = [s for s in scored if s.get("hot")][:hot_k]
        cold = [s for s in scored if not s.get("hot")][:cold_k]
        seen = {s["resource_id"] for s in hot}
        cold = [s for s in cold if s["resource_id"] not in seen]
        return hot + cold
    return scored[:top_k]


def unified_route_v5(query: str, top_k: int = 10, cold_fraction: float = 0.3,
                     db_path: str | Path = DB_PATH,
                     short_circuit_confidence: float = SHORT_CIRCUIT_CONFIDENCE,
                     short_circuit_min_pool: int = SHORT_CIRCUIT_MIN_POOL,
                     intent: dict | None = None,
                     autocut: bool = False,
                     fusion_mode: str | None = None,
                     shadow: bool | None = None) -> dict[str, Any]:
    """HGMF v5: L1 intent → confidence-gated short-circuit → L2 cluster-constrained
    topic → L3 fusion rerank → L4 autocut.

    Args:
        query: natural-language task description.
        top_k: total results to return.
        cold_fraction: fraction reserved for cold-tier (never-triggered) resources.
        db_path: path to nexus.db.
        short_circuit_confidence: L1 confidence threshold for the short-circuit.
        short_circuit_min_pool: min cluster-pool size to allow the short-circuit
            (small pools need L2 topic retrieval to surface enough candidates).
        intent: optional precomputed L1 result ({"raw": route_task output}); skips
            the L1 call. Used by callers that already ran route_task.
        fusion_mode: 'convex' (default, Bruch et al. tuned convex combination) or
            'wrrf' (weighted-RRF BASELINE, Cormack et al. 2009). None → NEXUS_V5_FUSION
            env, default 'convex'. Production ranking stays 'convex'; 'wrrf' is for
            P5-bench comparison only.
        shadow: if True (or NEXUS_ROUTER_SHADOW=true), ALSO compute the wrrf
            baseline on the same candidate set and return both rankings + top-k
            agreement. The production `merged` is unchanged (still convex). Default
            None → NEXUS_ROUTER_SHADOW env (OFF).

    Returns:
        ``{query, merged, hot, cold, used_dense, path, l1, n, latency_ms}`` where
        ``path`` ∈ {"short_circuit", "full_pipeline"} and ``l1`` carries the
        L1 confidence/cluster. Each merged item carries ``resource_id``, ``name``,
        ``cluster``, ``final_score``, and the L3 components. When shadow is on,
        additionally carries ``shadow`` = {wrrf_merged, agreement@5, weights}.
    """
    db_path = str(db_path)
    if fusion_mode is None:
        fusion_mode = FUSION_MODE
    if shadow is None:
        shadow = SHADOW_MODE
    t0 = time.perf_counter()

    # ── L1: intent ────────────────────────────────────────────────────────────
    if intent is None:
        intent = _l1_intent(query, db_path)
    else:
        # normalize the precomputed intent dict shape
        intent = _l1_intent_from_raw(intent, db_path) if "intent_rids" not in intent else intent

    cluster = intent.get("cluster")
    confidence = intent.get("confidence", 0.0)

    # ── L1 short-circuit ─────────────────────────────────────────────────────
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        pool_size = _cluster_pool_size(conn, cluster)
    finally:
        conn.close()

    if confidence > short_circuit_confidence and pool_size >= short_circuit_min_pool:
        merged = _short_circuit(intent, top_k, db_path)
        if merged:
            return _result(query, merged, "short_circuit", intent, top_k,
                           cold_fraction, t0, used_dense=False, fusion_mode=fusion_mode)

    # ── L2: topic CONSTRAINED to top-2 clusters — but ONLY when L1 is confident
    # AND detected a real domain cluster. When L1 is weak (low confidence or the
    # "general" fallback pool), constraining would shrink the candidate set
    # below what unified (unconstrained) retrieves, diverging from both routers.
    # HGMF assumes L1 picks a good cluster; here we degrade gracefully: weak L1
    # → unconstrained L2 (= unified's candidate set) + L3 intent-aware rerank.
    constrain = (cluster is not None
                 and cluster != "general"
                 and confidence >= 0.4
                 and pool_size >= 20)
    clusters = [cluster, "general"] if constrain else None
    topic = unified_route(query, top_k=max(top_k * 3, 30),
                          cold_fraction=0.0,  # v5 does its own two-tier split in L3
                          db_path=db_path, vec=True, clusters=clusters)
    topic_merged = topic.get("merged", [])
    used_dense = topic.get("used_dense", False)

    # ── L3: fusion rerank ───────────────────────────────────────────────────
    merged = _v5_rerank(topic_merged, intent, top_k, cold_fraction, db_path,
                        fusion_mode=fusion_mode)

    # ── L4: autocut (disabled by default — the L3 skill_match cliff makes
    # score-gap cuts shrink the set to ~3, killing cross-router agreement).
    # Re-enable via autocut=True when tuning for precision over recall.
    if autocut:
        merged = _autocut(merged, top_k)

    # ── Shadow cutover (P3): compute the weighted-RRF BASELINE on the SAME
    # candidate set and report top-k agreement. Production `merged` is the
    # convex output above; the wrrf ranking is returned separately so P5-bench
    # can verify Bruch's convex > RRF finding on NEXUS data without changing
    # the live ranking. Disabled unless shadow=True / NEXUS_ROUTER_SHADOW=true.
    shadow_block: dict | None = None
    if shadow and topic_merged:
        wrrf_merged = _v5_rerank(topic_merged, intent, top_k, cold_fraction, db_path,
                                 fusion_mode="wrrf")
        a_ids = [m.get("resource_id") for m in merged if m.get("resource_id") is not None]
        b_ids = [m.get("resource_id") for m in wrrf_merged if m.get("resource_id") is not None]
        shadow_block = {
            "wrrf_merged": [
                {"resource_id": m.get("resource_id"), "name": m.get("name"),
                 "final_score": round(m.get("final_score", 0.0), 6)}
                for m in wrrf_merged
            ],
            "agreement_at_5": round(v5_agreement(a_ids, b_ids, k=5), 4),
            "agreement_at_10": round(v5_agreement(a_ids, b_ids, k=10), 4),
            "weights": v5_get_weights(),
        }

    return _result(query, merged, "full_pipeline", intent, top_k,
                   cold_fraction, t0, used_dense=used_dense,
                   fusion_mode=fusion_mode, shadow=shadow_block)


def _l1_intent_from_raw(raw: dict, db_path: str) -> dict:
    """Build the L1 intent dict from a raw route_task output (for the `intent` arg)."""
    skills = raw.get("skills", []) if isinstance(raw, dict) else []
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        n2i = _name_to_id_map(conn)
    finally:
        conn.close()
    intent_rids = []
    for s in skills:
        rid = n2i.get(s.get("name") if isinstance(s, dict) else None)
        if rid is not None and rid not in intent_rids:
            intent_rids.append(rid)
    intent_rank = {rid: sc for rid, sc in zip(intent_rids, _rank_normalize(
        [float(s.get("score", 0.0) or 0.0) for s in skills
         if n2i.get(s.get("name")) is not None]))}
    return {
        "skills": skills, "cluster": raw.get("cluster"), "confidence": float(raw.get("task_confidence", 0.0) or 0.0),
        "name2id": n2i, "intent_rids": intent_rids, "intent_rank": intent_rank,
        "raw": raw, "latency_ms": float(raw.get("latency_ms", 0.0) or 0.0),
    }


def _result(query: str, merged: list[dict], path: str, intent: dict,
            top_k: int, cold_fraction: float, t0: float, used_dense: bool,
            fusion_mode: str = "convex", shadow: dict | None = None) -> dict:
    hot = [x for x in merged if x.get("hot")] if cold_fraction > 0 else merged
    cold = [x for x in merged if not x.get("hot")] if cold_fraction > 0 else []
    w = v5_get_weights()
    out = {
        "query": query,
        "merged": merged,
        "hot": hot,
        "cold": cold,
        "hot_k": len(hot), "cold_k": len(cold),
        "used_dense": used_dense,
        "path": path,
        "fusion_mode": fusion_mode,
        "l1": {"cluster": intent.get("cluster"), "confidence": intent.get("confidence", 0.0),
               "intent_n": len(intent.get("intent_rids", [])),
               "l1_latency_ms": intent.get("latency_ms", 0.0)},
        "weights": {"topic": w["topic"], "intent": w["intent"],
                    "skill_match": w["skill_match"], "listwise": w["listwise"]},
        "n": len(merged),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    if shadow is not None:
        out["shadow"] = shadow
    return out


if __name__ == "__main__":
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "fix a login registration bug"
    out = unified_route_v5(q, top_k=10)
    print(json.dumps({
        "query": out["query"], "path": out["path"], "n": out["n"],
        "l1": out["l1"], "latency_ms": out["latency_ms"],
        "merged": [{"name": x.get("name"), "cluster": x.get("cluster"),
                    "final": round(x.get("final_score", 0), 4),
                    "src": x.get("source")}
                   for x in out["merged"]],
    }, indent=2))