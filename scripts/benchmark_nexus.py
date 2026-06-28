"""Industry-standard performance benchmark for NEXUS routing.

Measures retrieval quality, latency, routing accuracy, cold-start coverage,
and token efficiency for NEXUS vs baselines. READ-ONLY: never writes the DB,
never kills the live MCP. Uses the resource's own cluster as weak ground truth
(a query seeded from resource R in cluster C → relevant set = other resources
in cluster C). This is the standard "cluster-as-relevance" IR eval setup.

Usage:  venv/bin/python3 scripts/benchmark_nexus.py [--queries 200] [--top-k 10]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so server/router.py can `import core`
sys.path.insert(0, str(ROOT / "server"))
DB = ROOT / "db" / "nexus.db"


# ── query generation (weak ground truth) ────────────────────────────────────
def build_eval_corpus(conn, n, seed=42):
    """Build queries seeded from clustered resources: query=text, relevant=cluster.

    Each query is seeded from a real resource's name+description. Its ground-
    truth relevant set = all OTHER resources in the same cluster (the cluster is
    the curated domain label, so same-cluster = topically relevant by construction).
    Resources with no cluster or a singleton cluster are skipped (no relevant set).
    """
    rng = random.Random(seed)
    rows = conn.execute(
        "SELECT id, name, cluster, description FROM resources "
        "WHERE cluster IS NOT NULL AND name IS NOT NULL AND length(name) >= 4 "
        "ORDER BY id"
    ).fetchall()
    # group by cluster to confirm non-singleton
    by_cluster: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_cluster.setdefault(r["cluster"], []).append(r)
    valid = [r for r in rows if len(by_cluster.get(r["cluster"], [])) >= 3]
    rng.shuffle(valid)
    corpus = []
    for r in valid[:n]:
        name = (r["name"] or "").replace("-", " ").replace("_", " ")
        desc = (r["description"] or "").strip()
        seed_text = " ".join((name.split() + desc.split())[:10])[:140]
        cluster = r["cluster"]
        # relevant set = all resources in this cluster EXCEPT the seed
        rel_ids = {x["id"] for x in by_cluster[cluster] if x["id"] != r["id"]}
        corpus.append({"query": seed_text, "cluster": cluster,
                       "relevant": rel_ids, "seed_id": r["id"]})
    return corpus


# ── IR metrics (standard definitions) ───────────────────────────────────────
def precision_at_k(retrieved, relevant, k):
    if k <= 0:
        return 0.0
    topk = retrieved[:k]
    hit = sum(1 for x in topk if x in relevant)
    return hit / k


def recall_at_k(retrieved, relevant, k):
    if not relevant:
        return 0.0
    topk = retrieved[:k]
    hit = sum(1 for x in topk if x in relevant)
    return hit / len(relevant)


def mrr(retrieved, relevant):
    for i, x in enumerate(retrieved, start=1):
        if x in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved, relevant, k):
    """Binary-relevance NDCG@k. rel=1 if retrieved[i] in relevant else 0."""
    dcg = 0.0
    for i, x in enumerate(retrieved[:k], start=1):
        if x in relevant:
            dcg += 1.0 / math.log2(i + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


# ── intent-aware metrics (the methodology-trap fix) ─────────────────────────
# v4 routes by INTENT ("what task type?") and returns named skills; unified
# retrieves by TOPIC similarity. The topic eval above uses relevant = OTHER
# resources in the seed cluster (diversity yardstick), which MIS-measures an
# intent router: returning the exact right skill (the seed itself) scores 0.
# The intent-aware yardstick: a returned resource is relevant if its CLUSTER
# matches the seed's cluster — the curated domain label = intent ground truth.
# Seed is INCLUDED (an intent router returning the canonical skill is correct).
# Compare v4 vs unified on THIS yardstick to judge intent routing fairly.
_CLUSTER_CACHE: dict[int, str] = {}


def _cluster_of(conn, rid: int) -> str | None:
    c = _CLUSTER_CACHE.get(rid)
    if c is None:
        row = conn.execute("SELECT cluster FROM resources WHERE id=?", (rid,)).fetchone()
        c = row[0] if row else ""
        _CLUSTER_CACHE[rid] = c
    return c or None


def intent_precision_at_k(retrieved, seed_cluster, conn, k):
    """Fraction of top-k whose cluster == seed_cluster (seed-inclusive)."""
    if k <= 0 or not seed_cluster:
        return 0.0
    topk = retrieved[:k]
    if not topk:
        return 0.0
    hit = sum(1 for rid in topk
              if _cluster_of(conn, rid) == seed_cluster)
    return hit / k


def intent_mrr(retrieved, seed_cluster, conn):
    """Rank of first in-cluster result (seed-inclusive). 0 if none."""
    for i, rid in enumerate(retrieved, start=1):
        if _cluster_of(conn, rid) == seed_cluster:
            return 1.0 / i
    return 0.0


# ── systems under test ──────────────────────────────────────────────────────
def sys_nexus_unified(query, top_k):
    # AC2a OFF arm: vec=True, use_chunks=False. use_chunks is the ONLY variable
    # that differs from sys_nexus_unified_chunks (vec=True, use_chunks=True) so
    # the OFF-vs-ON deltas isolate the chunk stream's marginal contribution.
    from router_v2 import unified_route
    out = unified_route(query, top_k=top_k, cold_fraction=0.3, vec=True)
    return [x.get("resource_id") for x in out.get("merged", []) if x.get("resource_id")]


def sys_nexus_unified_chunks(query, top_k):
    """unified_route WITH the chunk stream (66k chunks actively participating).

    Same as sys_nexus_unified but vec=True AND use_chunks=True so the chunk
    FTS + chunk semantic streams contribute to the resource-keyed RRF. This is
    the AC2a comparison arm: chunks-OFF (sys_nexus_unified) vs chunks-ON (here).
    """
    from router_v2 import unified_route
    out = unified_route(query, top_k=top_k, cold_fraction=0.3,
                        vec=True, use_chunks=True)
    return [x.get("resource_id") for x in out.get("merged", []) if x.get("resource_id")]


_NAME2ID: dict[str, int] | None = None
def _name_to_id_map(conn):
    global _NAME2ID
    if _NAME2ID is None:
        _NAME2ID = {r["name"]: r["id"] for r in conn.execute(
            "SELECT id, name FROM resources WHERE name IS NOT NULL").fetchall()}
    return _NAME2ID


def sys_nexus_v4(query, top_k, conn):
    """The live v4 route_task path. Returns skills by name (no resource_id),
    so resolve name -> resource_id via the resources table. Skills whose name
    isn't a resource (e.g. generic 'investigate') count as a miss for IR eval —
    v4 operates at skill granularity, unified at resource granularity."""
    from router import route_task
    res = route_task(query, project="default", use_embedding=True)
    skills = res.get("skills", []) if isinstance(res, dict) else []
    n2i = _name_to_id_map(conn)
    out = []
    for s in skills[:top_k]:
        rid = n2i.get(s.get("name")) if isinstance(s, dict) else None
        if rid is not None:
            out.append(rid)
    return out


def sys_nexus_v5(query, top_k, conn, fusion_mode=None):
    """The v5 HGMF unifier: L1 intent (route_task) → L2 cluster-constrained topic
    (unified_route) → L3 hand-tuned rerank. Returns resource_ids (same granularity
    as unified), so it's directly comparable to unified on the topic yardstick,
    and its L3 skill_match term inherits v4's intent signal for the intent yardstick.

    ``fusion_mode`` (None → module default from NEXUS_V5_FUSION, else 'convex'/'wrrf')
    lets the P5-bench fusion comparison drive both modes from one process without
    an env/reload dance."""
    from router_v2.v5 import unified_route_v5
    res = unified_route_v5(query, top_k=top_k, db_path=str(DB), autocut=False,
                           fusion_mode=fusion_mode)
    return [x.get("resource_id") for x in res.get("merged", [])
            if x.get("resource_id") is not None]


def sys_lexical(query, top_k, conn):
    """Baseline: pure lexical resource search (no semantic, no EFC, no UCB)."""
    import re as _re
    stop = {"the", "a", "an", "in", "on", "of", "to", "for", "and", "or",
            "is", "are", "with", "this", "that", "it", "by", "at", "be", "fix"}
    toks = [t for t in _re.split(r"\s+", _re.sub(r"[^a-z0-9 ]", " ", query.lower()))
            if t and t not in stop and len(t) > 1]
    if not toks:
        return []
    rows = conn.execute(
        "SELECT id, name, description FROM resources WHERE status='active'"
    ).fetchall()
    scored = []
    for r in rows:
        text = ((r["name"] or "") + " " + (r["description"] or "")).lower()
        hits = sum(1 for t in toks if t in text)
        if hits:
            scored.append((hits, r["id"]))
    scored.sort(reverse=True)
    return [rid for _, rid in scored[:top_k]]


def sys_random(query, top_k, conn, rng):
    """Baseline: random active resources."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM resources WHERE status='active' ORDER BY id").fetchall()]
    rng.shuffle(ids)
    return ids[:top_k]


# ── latency helper ──────────────────────────────────────────────────────────
def percentiles(xs):
    if not xs:
        return {"p50": 0, "p95": 0, "p99": 0, "mean": 0}
    xs2 = sorted(xs)
    def pct(p):
        return xs2[min(len(xs2) - 1, int(p * len(xs2)))]
    return {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99), "mean": mean(xs2)}


# ── main ────────────────────────────────────────────────────────────────────
def run_fusion_compare(conn, corpus, top_k):
    """P5-bench (Bruch et al. arXiv:2210.11934): verify that the tuned CONVEX
    combination OUTPERFORMS weighted-RRF on NEXUS data. Runs v5 under
    fusion_mode='convex' (default production) vs 'wrrf' (Cormack 2009 baseline)
    on the SAME candidate set per query, reports P@k/MRR/NDCG@k for both, and
    the overlap@5 agreement between the two rankings (shadow-cutover signal).

    READ-ONLY: never writes the DB. The two modes share one candidate set per
    query (L1+L2 are deterministic; only L3 fusion differs), so the comparison
    isolates the fusion function — the cleanest possible Bruch replication."""
    from router_v2.fusion import v5_agreement
    metrics = {m: [] for m in ("convex", "wrrf")}
    retrieved = {m: [] for m in ("convex", "wrrf")}
    for q in corpus:
        rel = q["relevant"]
        for mode in ("convex", "wrrf"):
            r = sys_nexus_v5(q["query"], top_k, conn, fusion_mode=mode)
            retrieved[mode].append(r)
            metrics[mode].append({
                "p@k": precision_at_k(r, rel, top_k),
                "mrr": mrr(r, rel),
                "ndcg": ndcg_at_k(r, rel, top_k),
            })
    # agreement@5 between the two rankings per query
    agmts = []
    for rc, rw in zip(retrieved["convex"], retrieved["wrrf"]):
        rc5 = [rid for rid in rc[:5] if rid is not None]
        rw5 = [rid for rid in rw[:5] if rid is not None]
        agmts.append(v5_agreement(rc5, rw5, k=5))
    print("\n" + "=" * 78)
    print(f"P5-BENCH: CONVEX vs WEIGHTED-RRF (Bruch et al. 2022), k={top_k}, n={len(corpus)}")
    print("=" * 78)
    print(f"{'fusion':<10} {'P@k':>8} {'MRR':>8} {'NDCG@k':>8}")
    print("-" * 78)
    out = {}
    for mode in ("convex", "wrrf"):
        avg = {m: mean(x[m] for x in metrics[mode]) for m in ("p@k", "mrr", "ndcg")}
        out[mode] = avg
        print(f"{mode:<10} {avg['p@k']:>8.3f} {avg['mrr']:>8.3f} {avg['ndcg']:>8.3f}")
    delta = out["convex"]["ndcg"] - out["wrrf"]["ndcg"]
    print(f"\n  convex NDCG - wrrf NDCG = {delta:+.3f}  "
          f"({'Bruch holds: convex >= wrrf' if delta >= 0 else 'CONTRADICTS Bruch: wrrf > convex'})")
    print(f"  overlap@5 agreement (convex vs wrrf): {mean(agmts):.3f}")
    return {"convex": out["convex"], "wrrf": out["wrrf"],
            "ndcg_delta": delta, "agreement_at_5": mean(agmts)}


def run_cold_start_sim(conn, corpus, top_k):
    """P5-bench cold-start simulation: exercise the TS-v2 faithful sample path
    (``thompson_sample_resource`` — where the P-v2faithful IDS + Meta-TS wiring
    lives) directly, under NEXUS_TS_V2 ON (Meta-TS posterior sampling + IDS re-rank)
    vs OFF (legacy empirical-Bayes TS). For each query the base candidate set is
    retrieved once via unified_route (deterministic; unaffected by the flag), then
    re-ranked by the TS path under each config. Reports cold-resource (never-
    triggered) coverage in the TS-ranked top-k, cluster-hit-rate, and P@k/NDCG@k
    vs the query's relevant set.

    READ-ONLY: never writes the DB. The TS sample path only reads beliefs; the
    meta-posterior UPDATE lives in _update_thompson_belief (never called by
    routing), so flag-OFF stays byte-identical and flag-ON performs zero writes."""
    from server.learning import thompson_sample_resource

    def id_to_name(rid):
        row = conn.execute("SELECT name FROM resources WHERE id=?", (rid,)).fetchone()
        return row[0] if row else None

    def cold_frac(retrieved_names):
        cold_hits, total = 0, 0
        for name in retrieved_names:
            if name is None:
                continue
            total += 1
            trig = conn.execute(
                "SELECT times_triggered FROM resources WHERE name=?", (name,)
            ).fetchone()
            if trig and (trig[0] or 0) == 0:
                cold_hits += 1
        return (cold_hits / total) if total else 0.0

    def cluster_hit(retrieved_names, seed_cluster):
        if not retrieved_names or not seed_cluster:
            return 0.0
        ph = ",".join("?" * len(retrieved_names))
        in_c = conn.execute(
            f"SELECT COUNT(*) FROM resources WHERE name IN ({ph}) AND cluster=?",
            tuple(retrieved_names + [seed_cluster])
        ).fetchone()[0]
        return 1.0 if in_c > 0 else 0.0

    def names_to_ids(names):
        ids = []
        for nm in names:
            if nm is None:
                ids.append(None); continue
            row = conn.execute("SELECT id FROM resources WHERE name=?", (nm,)).fetchone()
            ids.append(row[0] if row else None)
        return ids

    results = {}
    cand_pool = max(top_k * 3, 30)  # over-pull candidates so TS re-rank has room
    for label, on in (("ts_v2_off", False), ("ts_v2_on", True)):
        os.environ["NEXUS_TS_V2"] = "true" if on else "false"
        colds, chits, ps, ndcgs = [], [], [], []
        for q in corpus:
            # deterministic base retrieval — same candidate set for both configs
            base_ids = sys_nexus_unified(q["query"], cand_pool)
            cands = []
            for rid in base_ids:
                nm = id_to_name(rid)
                if nm:
                    cands.append({"name": nm, "score": 0.5, "resource_id": rid})
            try:
                ranked = thompson_sample_resource(conn, q["query"], cands)
            except Exception:
                ranked = cands
            top_names = [c.get("name") for c in ranked[:top_k]]
            top_ids = names_to_ids(top_names)
            colds.append(cold_frac(top_names))
            chits.append(cluster_hit(top_names, q["cluster"]))
            ps.append(precision_at_k(top_ids, q["relevant"], top_k))
            ndcgs.append(ndcg_at_k(top_ids, q["relevant"], top_k))
        results[label] = {"cold_fraction": mean(colds), "cluster_hit": mean(chits),
                          "p@k": mean(ps), "ndcg": mean(ndcgs)}
    os.environ.pop("NEXUS_TS_V2", None)  # restore (don't leak flag into caller)
    print("\n" + "=" * 78)
    print(f"P5-BENCH: COLD-START SIMULATION (TS-v2 faithful sample path on vs off), "
          f"k={top_k}, n={len(corpus)}")
    print("=" * 78)
    print(f"{'config':<12} {'cold_frac':>11} {'cluster_hit':>13} {'P@k':>8} {'NDCG@k':>8}")
    print("-" * 78)
    for label in ("ts_v2_off", "ts_v2_on"):
        r = results[label]
        print(f"{label:<12} {r['cold_fraction']:>11.3f} {r['cluster_hit']:>13.3f} "
              f"{r['p@k']:>8.3f} {r['ndcg']:>8.3f}")
    lift_c = results["ts_v2_on"]["cold_fraction"] - results["ts_v2_off"]["cold_fraction"]
    lift_h = results["ts_v2_on"]["cluster_hit"] - results["ts_v2_off"]["cluster_hit"]
    lift_n = results["ts_v2_on"]["ndcg"] - results["ts_v2_off"]["ndcg"]
    print(f"\n  cold-resource coverage lift (on - off): {lift_c:+.3f}")
    print(f"  cluster-hit-rate lift (on - off):        {lift_h:+.3f}")
    print(f"  NDCG@k lift (on - off):                  {lift_n:+.3f}  "
          f"({'faithful path moves the ranking' if (lift_c + lift_h + lift_n) != 0 else 'no measurable movement at current outcome-data volume'})")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only-unified", action="store_true",
                    help="run only the two AC2a arms (unified OFF vs chunks ON); "
                         "skip v4/v5/lexical/random. Avoids the v4 per-call "
                         "embedder reload (137 reloads/200q) that makes the full "
                         "run slow/fragile. Use for the definitive AC2a gate.")
    ap.add_argument("--fusion-compare", action="store_true",
                    help="P5-bench: run ONLY the convex-vs-wrrf fusion comparison "
                         "(Bruch et al. 2022 replication) and exit. READ-ONLY.")
    ap.add_argument("--cold-start-sim", action="store_true",
                    help="P5-bench: run ONLY the cold-start simulation (TS-v2 on vs "
                         "off, never-triggered coverage in top-k) and exit. READ-ONLY.")
    args = ap.parse_args()
    ou = args.only_unified

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row

    # ── P5-bench focused modes: run one comparison and exit (READ-ONLY) ──────
    if args.fusion_compare or args.cold_start_sim:
        corpus = build_eval_corpus(conn, args.queries, args.seed)
        if not corpus:
            print("ERROR: empty corpus"); conn.close(); return 1
        # warmup the lazy embedder so it doesn't land in the first timed sample
        try:
            sys_nexus_v5(corpus[0]["query"], args.top_k, conn, fusion_mode="convex")
        except Exception:
            pass
        if args.fusion_compare:
            run_fusion_compare(conn, corpus, args.top_k)
        if args.cold_start_sim:
            run_cold_start_sim(conn, corpus, args.top_k)
        conn.close()
        return 0

    corpus = build_eval_corpus(conn, args.queries, args.seed)
    print(f"eval corpus: {len(corpus)} queries (cluster-weak-ground-truth), top_k={args.top_k}")
    if not corpus:
        print("ERROR: empty corpus"); conn.close(); return 1

    # warmup: run both unified arms once (untimed) so the LazyEmbedder cold
    # load (~14s) + ChunkIndex vec0 open land here, not in the p95 sample.
    # The gate is "p95 < 1500ms warm" — warmup makes every timed sample warm.
    try:
        sys_nexus_unified(corpus[0]["query"], args.top_k)
        sys_nexus_unified_chunks(corpus[0]["query"], args.top_k)
    except Exception:
        pass

    rng = random.Random(args.seed)
    systems = {
        "nexus_unified_v2": [],
        "nexus_unified_chunks": [],
        "nexus_v4": [],
        "nexus_v5": [],
        "lexical_baseline": [],
        "random_baseline": [],
    }
    latencies = {k: [] for k in systems}
    retrieved_ids = {k: [] for k in systems}  # per-query retrieved id lists

    for i, q in enumerate(corpus, 1):
        rel = q["relevant"]
        # NEXUS unified
        t0 = time.perf_counter()
        try:
            r = sys_nexus_unified(q["query"], args.top_k)
        except Exception as e:
            r = []
        latencies["nexus_unified_v2"].append((time.perf_counter() - t0) * 1000)
        retrieved_ids["nexus_unified_v2"].append(r)
        systems["nexus_unified_v2"].append({
            "p@k": precision_at_k(r, rel, args.top_k),
            "r@k": recall_at_k(r, rel, args.top_k),
            "mrr": mrr(r, rel),
            "ndcg": ndcg_at_k(r, rel, args.top_k),
        })
        # NEXUS unified + chunks (AC2a arm: 66k chunks actively participating)
        t0 = time.perf_counter()
        try:
            r = sys_nexus_unified_chunks(q["query"], args.top_k)
        except Exception:
            r = []
        latencies["nexus_unified_chunks"].append((time.perf_counter() - t0) * 1000)
        retrieved_ids["nexus_unified_chunks"].append(r)
        systems["nexus_unified_chunks"].append({
            "p@k": precision_at_k(r, rel, args.top_k),
            "r@k": recall_at_k(r, rel, args.top_k),
            "mrr": mrr(r, rel),
            "ndcg": ndcg_at_k(r, rel, args.top_k),
        })
        # NEXUS v4 (live path)
        if not ou:
            t0 = time.perf_counter()
            try:
                r = sys_nexus_v4(q["query"], args.top_k, conn)
            except Exception as e:
                r = []
            latencies["nexus_v4"].append((time.perf_counter() - t0) * 1000)
            retrieved_ids["nexus_v4"].append(r)
            systems["nexus_v4"].append({
                "p@k": precision_at_k(r, rel, args.top_k),
                "r@k": recall_at_k(r, rel, args.top_k),
                "mrr": mrr(r, rel),
                "ndcg": ndcg_at_k(r, rel, args.top_k),
            })
        # NEXUS v5 (HGMF unifier — L1 intent + L2 topic + L3 rerank)
        if not ou:
            t0 = time.perf_counter()
            try:
                r = sys_nexus_v5(q["query"], args.top_k, conn)
            except Exception as e:
                r = []
            latencies["nexus_v5"].append((time.perf_counter() - t0) * 1000)
            retrieved_ids["nexus_v5"].append(r)
            systems["nexus_v5"].append({
                "p@k": precision_at_k(r, rel, args.top_k),
                "r@k": recall_at_k(r, rel, args.top_k),
                "mrr": mrr(r, rel),
                "ndcg": ndcg_at_k(r, rel, args.top_k),
            })
        # lexical
        if not ou:
            t0 = time.perf_counter()
            r = sys_lexical(q["query"], args.top_k, conn)
            latencies["lexical_baseline"].append((time.perf_counter() - t0) * 1000)
            retrieved_ids["lexical_baseline"].append(r)
            systems["lexical_baseline"].append({
                "p@k": precision_at_k(r, rel, args.top_k),
                "r@k": recall_at_k(r, rel, args.top_k),
                "mrr": mrr(r, rel),
                "ndcg": ndcg_at_k(r, rel, args.top_k),
            })
        # random
        if not ou:
            t0 = time.perf_counter()
            r = sys_random(q["query"], args.top_k, conn, rng)
            latencies["random_baseline"].append((time.perf_counter() - t0) * 1000)
            retrieved_ids["random_baseline"].append(r)
            systems["random_baseline"].append({
                "p@k": precision_at_k(r, rel, args.top_k),
                "r@k": recall_at_k(r, rel, args.top_k),
                "mrr": mrr(r, rel),
                "ndcg": ndcg_at_k(r, rel, args.top_k),
            })
        if i % 50 == 0:
            print(f"  [{i}/{len(corpus)}] queries done")

    # ── routing accuracy from historical outcomes ──────────────────────────
    acc = conn.execute(
        "SELECT SUM(times_correct), SUM(times_wrong) FROM resources "
        "WHERE times_correct + times_wrong > 0"
    ).fetchone()
    tc, tw = acc[0] or 0, acc[1] or 0
    routing_acc = tc / (tc + tw) if (tc + tw) else 0.0

    # Graded-relevance metrics (Järvelin & Kekäläinen 2002; Deliu 2024) computed
    # from routing_log. The legacy routing_acc above is resource-counter based
    # and binary; these add honest graded accuracy + NDCG@5 over per-query logs.
    from server.metrics.graded import weighted_accuracy, ndcg_at_k as graded_ndcg_at_k
    graded_w_acc = weighted_accuracy(conn, partial_weight=0.5)
    graded_ndcg_5 = graded_ndcg_at_k(conn, k=5)

    # ── cold-start coverage ────────────────────────────────────────────────
    nt_total = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE times_triggered = 0 OR times_triggered IS NULL"
    ).fetchone()[0]
    total_res = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    # count how many never-triggered resources appear in unified cold tier
    surfaced_nt = set()
    for q in corpus:
        out = sys_nexus_unified(q["query"], args.top_k)
        # cheap re-check via DB for cold tier would need unified_route again;
        # use the merged results' never-triggered members
        for rid in retrieved_ids["nexus_unified_v2"][corpus.index(q)]:
            trig = conn.execute(
                "SELECT times_triggered FROM resources WHERE id=?", (rid,)).fetchone()
            if trig and (trig[0] or 0) == 0:
                surfaced_nt.add(rid)
    cold_cov = len(surfaced_nt) / max(nt_total, 1)

    # ── token efficiency (Tool Attention) ──────────────────────────────────
    full_schema_count = total_res
    active_set = args.top_k  # tools injected per query
    token_saving_pct = 100 * (1 - active_set / full_schema_count)

    # ── IDE output boost proxy: cluster-hit-rate ───────────────────────────
    # For each query, did the system return ≥1 result in the correct cluster?
    def cluster_hit_rate(retrieved_lists, corpus):
        hits = 0
        for rids, q in zip(retrieved_lists, corpus):
            in_cluster = conn.execute(
                f"SELECT COUNT(*) FROM resources WHERE id IN ({','.join('?'*len(rids))}) "
                f"AND cluster=?", tuple(rids + [q["cluster"]])
            ).fetchone()[0] if rids else 0
            if in_cluster > 0:
                hits += 1
        return hits / len(corpus)
    def top1_cluster_acc(retrieved_lists, corpus):
        """Stricter than cluster-hit-rate: is the SINGLE top result in the correct cluster?
        k=10 over 8 clusters has a ceiling effect (~0.73 by chance); top-1 has chance ~1/8=0.125,
        so this is the metric that actually discriminates IDE-top-suggestion quality."""
        hits = 0
        for rids, q in zip(retrieved_lists, corpus):
            if not rids:
                continue
            row = conn.execute(
                "SELECT cluster FROM resources WHERE id=?", (rids[0],)).fetchone()
            if row and row[0] == q["cluster"]:
                hits += 1
        return hits / len(corpus)
    chr_unified = cluster_hit_rate(retrieved_ids["nexus_unified_v2"], corpus)
    chr_v4 = cluster_hit_rate(retrieved_ids["nexus_v4"], corpus) if not ou else 0.0
    chr_v5 = cluster_hit_rate(retrieved_ids["nexus_v5"], corpus) if not ou else 0.0
    chr_lex = cluster_hit_rate(retrieved_ids["lexical_baseline"], corpus) if not ou else 0.0
    chr_rand = cluster_hit_rate(retrieved_ids["random_baseline"], corpus) if not ou else 0.0
    chr_chunks = cluster_hit_rate(retrieved_ids["nexus_unified_chunks"], corpus)
    t1_unified = top1_cluster_acc(retrieved_ids["nexus_unified_v2"], corpus)
    t1_v4 = top1_cluster_acc(retrieved_ids["nexus_v4"], corpus) if not ou else 0.0
    t1_v5 = top1_cluster_acc(retrieved_ids["nexus_v5"], corpus) if not ou else 0.0
    t1_lex = top1_cluster_acc(retrieved_ids["lexical_baseline"], corpus) if not ou else 0.0
    t1_rand = top1_cluster_acc(retrieved_ids["random_baseline"], corpus) if not ou else 0.0
    t1_chunks = top1_cluster_acc(retrieved_ids["nexus_unified_chunks"], corpus)

    # ── intent-aware eval (the methodology-trap fix) ───────────────────────
    # Measures v4 FAIRLY: did its returned skills land in the seed's domain
    # cluster? Seed-inclusive (an intent router returning the canonical skill
    # is correct). This is the yardstick that matches an intent router's job.
    def intent_metrics(retrieved_lists, corpus, k):
        ps, ms = [], []
        for rids, q in zip(retrieved_lists, corpus):
            ps.append(intent_precision_at_k(rids, q["cluster"], conn, k))
            ms.append(intent_mrr(rids, q["cluster"], conn))
        if not ps:
            return {"p@k": 0.0, "mrr": 0.0}
        return {"p@k": mean(ps), "mrr": mean(ms)}
    intent_k = args.top_k
    intent_v4 = intent_metrics(retrieved_ids["nexus_v4"], corpus, intent_k) if not ou else {"p@k": 0.0, "mrr": 0.0}
    intent_v5 = intent_metrics(retrieved_ids["nexus_v5"], corpus, intent_k) if not ou else {"p@k": 0.0, "mrr": 0.0}
    intent_unified = intent_metrics(retrieved_ids["nexus_unified_v2"], corpus, intent_k)
    intent_chunks = intent_metrics(retrieved_ids["nexus_unified_chunks"], corpus, intent_k)
    intent_lex = intent_metrics(retrieved_ids["lexical_baseline"], corpus, intent_k) if not ou else {"p@k": 0.0, "mrr": 0.0}
    intent_rand = intent_metrics(retrieved_ids["random_baseline"], corpus, intent_k) if not ou else {"p@k": 0.0, "mrr": 0.0}

    # ── report ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("RETRIEVAL QUALITY (cluster-weak-ground-truth, k={})".format(args.top_k))
    print("=" * 78)
    print(f"{'System':<22} {'P@k':>7} {'R@k':>7} {'MRR':>7} {'NDCG@k':>7}")
    print("-" * 78)
    _report_names = [n for n in ["random_baseline", "lexical_baseline", "nexus_v4",
                                 "nexus_v5", "nexus_unified_v2", "nexus_unified_chunks"]
                     if systems[n]]
    for name in _report_names:
        ms = systems[name]
        avg = {m: mean(x[m] for x in ms) for m in ["p@k", "r@k", "mrr", "ndcg"]}
        print(f"{name:<22} {avg['p@k']:>7.3f} {avg['r@k']:>7.3f} {avg['mrr']:>7.3f} {avg['ndcg']:>7.3f}")

    print("\n" + "=" * 78)
    print("LATENCY (ms, over {} queries)".format(len(corpus)))
    print("=" * 78)
    print(f"{'System':<22} {'p50':>8} {'p95':>8} {'p99':>8} {'mean':>8}")
    print("-" * 78)
    for name in _report_names:
        p = percentiles(latencies[name])
        print(f"{name:<22} {p['p50']:>8.1f} {p['p95']:>8.1f} {p['p99']:>8.1f} {p['mean']:>8.1f}")

    print("\n" + "=" * 78)
    print("ROUTING ACCURACY & COLD-START (from live DB)")
    print("=" * 78)
    print(f"  routing accuracy (correct/(correct+wrong)): {routing_acc:.3f}  ({tc}/{tc+tw})")
    print(f"  weighted_accuracy (graded, partial@0.5):     {graded_w_acc:.3f}")
    print(f"  ndcg@5 (graded, per-query routing_log):      {graded_ndcg_5:.3f}")
    print(f"  cold-start coverage (never-triggered surfaced): {cold_cov:.3f}  "
          f"({len(surfaced_nt)}/{nt_total} of {total_res})")
    print(f"  token efficiency (Tool Attention): {token_saving_pct:.1f}% schema reduction "
          f"({active_set}/{full_schema_count} tools injected)")

    print("\n" + "=" * 78)
    print("IDE OUTPUT BOOST PROXY — cluster-hit-rate (top-k in correct domain)")
    print("=" * 78)
    print(f"  random baseline:   {chr_rand:.3f}")
    print(f"  lexical baseline:  {chr_lex:.3f}")
    print(f"  NEXUS v4 (live):    {chr_v4:.3f}")
    print(f"  NEXUS v5 (unifier): {chr_v5:.3f}")
    print(f"  NEXUS unified v2:   {chr_unified:.3f}")
    boost_vs_lex = (chr_unified - chr_lex)
    boost_vs_rand = (chr_unified - chr_rand)
    print(f"  → NEXUS-unified lift vs lexical: {boost_vs_lex:+.3f}  "
          f"({100*boost_vs_lex/max(chr_lex,1e-9):+.1f}%)")
    print(f"  → NEXUS-unified lift vs random:  {boost_vs_rand:+.3f}  "
          f"({100*boost_vs_rand/max(chr_rand,1e-9):+.1f}%)")
    print(f"  → NEXUS-v5 lift vs unified:      {chr_v5 - chr_unified:+.3f}  "
          f"(v5 should match unified on topic yardstick)")

    print("\n" + "=" * 78)
    print("TOP-1 CLUSTER ACCURACY (strict — chance baseline = 1/8 = 0.125)")
    print("=" * 78)
    print(f"  random baseline:   {t1_rand:.3f}")
    print(f"  lexical baseline:  {t1_lex:.3f}")
    print(f"  NEXUS v4 (live):    {t1_v4:.3f}")
    print(f"  NEXUS v5 (unifier): {t1_v5:.3f}")
    print(f"  NEXUS unified v2:   {t1_unified:.3f}")
    lift_t1_lex = (t1_unified - t1_lex)
    lift_t1_rand = (t1_unified - t1_rand)
    print(f"  → NEXUS-unified lift vs lexical: {lift_t1_lex:+.3f}  "
          f"({100*lift_t1_lex/max(t1_lex,1e-9):+.1f}%)")
    print(f"  → NEXUS-unified lift vs random:  {lift_t1_rand:+.3f}  "
          f"({100*lift_t1_rand/max(t1_rand,1e-9):+.1f}%)")

    print("\n" + "=" * 78)
    print(f"INTENT-AWARE EVAL (cluster-match yardstick, k={intent_k}, seed-inclusive)")
    print("  → the fair yardstick for an intent router (v4/v5); compare to topic eval above")
    print("=" * 78)
    print(f"{'System':<22} {'iP@k':>8} {'iMRR':>8}")
    print("-" * 78)
    for name, lbl in [("random_baseline", "random_baseline"),
                     ("lexical_baseline", "lexical_baseline"),
                     ("nexus_v4", "nexus_v4"),
                     ("nexus_v5", "nexus_v5"),
                     ("nexus_unified_v2", "nexus_unified_v2")]:
        m = {"random_baseline": intent_rand, "lexical_baseline": intent_lex,
             "nexus_v4": intent_v4, "nexus_v5": intent_v5,
             "nexus_unified_v2": intent_unified}[name]
        print(f"{lbl:<22} {m['p@k']:>8.3f} {m['mrr']:>8.3f}")
    iv_lift = intent_v4["mrr"] - intent_unified["mrr"]
    v5_lift = intent_v5["mrr"] - intent_unified["mrr"]
    print(f"  → v4 intent-MRR vs unified: {iv_lift:+.3f}  "
          f"(positive = v4 wins on intent yardstick)")
    print(f"  → v5 intent-MRR vs unified: {v5_lift:+.3f}  "
          f"(v5 should match v4 on intent yardstick)")

    # ── AC2a: ACTIVE CHUNKS LIFT (chunks-OFF vs chunks-ON) ─────────────────
    # The gate for the "active chunks" plan: did wiring the 66k chunk stream
    # into unified_route (use_chunks=True) lift recall without a latency cost?
    print("\n" + "=" * 78)
    print("AC2a — ACTIVE CHUNKS LIFT (nexus_unified_v2 OFF vs nexus_unified_chunks ON)")
    print("=" * 78)
    off = {m: mean(x[m] for x in systems["nexus_unified_v2"]) for m in ["p@k", "r@k", "mrr", "ndcg"]}
    on = {m: mean(x[m] for x in systems["nexus_unified_chunks"]) for m in ["p@k", "r@k", "mrr", "ndcg"]}
    lon = percentiles(latencies["nexus_unified_chunks"])
    loff = percentiles(latencies["nexus_unified_v2"])
    print(f"  P@k      OFF {off['p@k']:.3f}  → ON {on['p@k']:.3f}   delta {on['p@k']-off['p@k']:+.3f}")
    print(f"  R@k      OFF {off['r@k']:.3f}  → ON {on['r@k']:.3f}   delta {on['r@k']-off['r@k']:+.3f}")
    print(f"  MRR      OFF {off['mrr']:.3f}  → ON {on['mrr']:.3f}   delta {on['mrr']-off['mrr']:+.3f}")
    print(f"  NDCG@k   OFF {off['ndcg']:.3f}  → ON {on['ndcg']:.3f}   delta {on['ndcg']-off['ndcg']:+.3f}")
    print(f"  CHR      OFF {chr_unified:.3f}  → ON {chr_chunks:.3f}   delta {chr_chunks-chr_unified:+.3f}")
    print(f"  Top1     OFF {t1_unified:.3f}  → ON {t1_chunks:.3f}   delta {t1_chunks-t1_unified:+.3f}")
    print(f"  iP@k     OFF {intent_unified['p@k']:.3f}  → ON {intent_chunks['p@k']:.3f}   delta {intent_chunks['p@k']-intent_unified['p@k']:+.3f}")
    print(f"  iMRR     OFF {intent_unified['mrr']:.3f}  → ON {intent_chunks['mrr']:.3f}   delta {intent_chunks['mrr']-intent_unified['mrr']:+.3f}")
    print(f"  lat p50  OFF {loff['p50']:.1f}ms → ON {lon['p50']:.1f}ms   delta {lon['p50']-loff['p50']:+.1f}ms")
    print(f"  lat p95  OFF {loff['p95']:.1f}ms → ON {lon['p95']:.1f}ms   delta {lon['p95']-loff['p95']:+.1f}ms")
    r_gate = on["r@k"] >= off["r@k"]
    p95_gate = lon["p95"] < 1500.0
    print("-" * 78)
    print(f"  GATE R@k(ON)>=R@k(OFF):  {'PASS' if r_gate else 'FAIL'}")
    print(f"  GATE p95 < 1500ms warm:  {'PASS' if p95_gate else 'FAIL'}  (ON p95={lon['p95']:.1f}ms)")

    # ── machine-readable summary ───────────────────────────────────────────
    summary = {
        "queries": len(corpus), "top_k": args.top_k,
        "retrieval": {name: {m: mean(x[m] for x in systems[name]) for m in ["p@k", "r@k", "mrr", "ndcg"]}
                      for name in systems if systems[name]},
        "latency_ms": {name: percentiles(latencies[name]) for name in systems if systems[name]},
        "routing_accuracy": routing_acc,
        "weighted_accuracy": graded_w_acc,
        "ndcg_at_5": graded_ndcg_5,
        "cold_start_coverage": cold_cov,
        "token_saving_pct": token_saving_pct,
        "cluster_hit_rate": {"random": chr_rand, "lexical": chr_lex,
                             "nexus_v4": chr_v4, "nexus_v5": chr_v5,
                             "nexus_unified": chr_unified,
                             "nexus_unified_chunks": chr_chunks},
        "intent_aware": {"random": intent_rand, "lexical": intent_lex,
                         "nexus_v4": intent_v4, "nexus_v5": intent_v5,
                         "nexus_unified": intent_unified,
                         "nexus_unified_chunks": intent_chunks},
        "ac2a_active_chunks": {
            "off": off, "on": on,
            "chr": {"off": chr_unified, "on": chr_chunks},
            "top1": {"off": t1_unified, "on": t1_chunks},
            "intent": {"off": intent_unified, "on": intent_chunks},
            "latency_ms": {"off": loff, "on": lon},
            "gate_r_on_ge_off": bool(r_gate),
            "gate_p95_lt_1500ms": bool(p95_gate),
        },
    }
    Path(ROOT / "logs" / "benchmark_results.json").write_text(json.dumps(summary, indent=2))
    conn.close()
    print(f"\nresults written to logs/benchmark_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())