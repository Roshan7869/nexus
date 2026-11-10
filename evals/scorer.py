"""Pure scoring functions for the NEXUS eval harness (Week1 card 3.3).

Rules (enforced by tests/test_eval_scorer.py):
    - no imports of server/, no network, no DB, no file I/O
    - fully deterministic: score(case, decision) is a pure function

Scoring against the ACTUAL route_for_task result shape (router_cutover._shape_v5):
    decision = {skills: [{name, score, cluster, ...}], cluster, tier,
                latency_ms, ...}

A check that the case doesn't ask for is reported as None (not counted).
"""
from __future__ import annotations

from typing import Any, Optional


def score_case(case: dict, decision: dict, default_top_k: int = 5) -> dict:
    """Score one golden case against one routing decision."""
    skills = [s.get("name") for s in (decision.get("skills") or []) if s.get("name")]
    result: dict[str, Any] = {
        "id": case.get("id"),
        "healthy": len(skills) > 0,
        "top_skill": skills[0] if skills else None,
        "n_skills": len(skills),
        "cluster": decision.get("cluster"),
        "tier": decision.get("tier"),
        "latency_ms": decision.get("latency_ms"),
        "skill_match": None,
        "cluster_ok": None,
        "tier_ok": None,
    }

    expected = case.get("expect_skills")
    if expected:  # omitted => no assertion; falsy-but-present is a schema error
        top = skills[: int(case.get("top_k", default_top_k))]
        result["skill_match"] = any(s in expected for s in top)

    accept_clusters = case.get("accept_clusters")
    if accept_clusters:
        result["cluster_ok"] = decision.get("cluster") in accept_clusters

    expect_tier = case.get("expect_tier")
    if expect_tier is not None:
        result["tier_ok"] = decision.get("tier") == expect_tier

    checks = [v for v in (result["skill_match"], result["cluster_ok"], result["tier_ok"]) if v is not None]
    # No requested checks (OOD health case) => pass iff healthy.
    result["passed"] = result["healthy"] and all(checks)
    return result


def _rate(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None


# ---------------------------------------------------------------------------
# Retrieval-arm scoring (Phase 0 / Phase 2, okf-native-cordis)
#
# Pure functions over retrieve_chunks()-shaped results:
#   results = [{resource_name, cluster, rrf_score, ...}, ...]  best-first
# Cases without expect_skills are health-only (recall/rr = None, excluded).
# ---------------------------------------------------------------------------

def score_retrieval(case: dict, results: list[dict], default_top_k: int = 5,
                    latency_ms: Optional[float] = None) -> dict:
    """Score one golden case against one retrieve_chunks() results list."""
    k = int(case.get("top_k", default_top_k))
    top = list(results or [])[:k]
    names = [r.get("resource_name") for r in top]
    out: dict[str, Any] = {
        "id": case.get("id"),
        "healthy": True,           # reaching the scorer at all = retrieval ran
        "n_results": len(results or []),
        "top_resource": names[0] if names else None,
        "recall_hit": None,
        "reciprocal_rank": None,
        "rank": None,
        "latency_ms": latency_ms,
    }
    expected = case.get("expect_skills")
    if expected:
        hit_rank = next((i for i, n in enumerate(names, start=1) if n in expected), None)
        out["recall_hit"] = hit_rank is not None
        out["rank"] = hit_rank
        out["reciprocal_rank"] = round(1.0 / hit_rank, 4) if hit_rank else 0.0
    return out


def summarize_retrieval(results: list[dict]) -> dict:
    """Aggregate recall@k + MRR + latency over scored retrieval cases."""
    n = len(results)
    latencies = [r["latency_ms"] for r in results if isinstance(r.get("latency_ms"), (int, float))]
    recalls = [r["recall_hit"] for r in results if r.get("recall_hit") is not None]
    rrs = [r["reciprocal_rank"] for r in results if r.get("reciprocal_rank") is not None]
    p50 = None
    if latencies:
        s = sorted(latencies)
        p50 = round(s[len(s) // 2], 2)
    return {
        "cases": n,
        "scored": len(recalls),
        "health_only": n - len(recalls),
        "recall_at_k": round(sum(1 for v in recalls if v) / len(recalls), 4) if recalls else None,
        "mrr": round(sum(rrs) / len(rrs), 4) if rrs else None,
        "p50_ms": p50,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
    }


def summarize(results: list[dict]) -> dict:
    """Aggregate pass rates. Cases with no active checks count via 'passed'."""
    n = len(results)
    latencies = [r["latency_ms"] for r in results if isinstance(r.get("latency_ms"), (int, float))]
    return {
        "cases": n,
        "passed": sum(1 for r in results if r.get("passed")),
        "pass_rate": round(sum(1 for r in results if r.get("passed")) / n, 4) if n else None,
        "skill_match_rate": _rate([r.get("skill_match") for r in results]),
        "cluster_ok_rate": _rate([r.get("cluster_ok") for r in results]),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
    }
