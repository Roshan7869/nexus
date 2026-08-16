"""Phase 7 — Router v5 production cutover (I-05).

Centralizes ``USE_UNIFIED_ROUTER`` dispatch and offline A/B verification so
MCP ``find_skills``, HTTP ``/route``, and ``/nexus/route`` share one path.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

NEXUS_ROOT = Path(__file__).resolve().parent.parent
SERVER = NEXUS_ROOT / "server"
if str(NEXUS_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXUS_ROOT))
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

# Production default after Phase 7 gate (override via env for rollback)
DEFAULT_ROUTER_FLAG = "2"
TOP1_AGREEMENT_TARGET = 0.95

STANDARD_AB_QUERIES: tuple[str, ...] = (
    "fix a login registration bug",
    "design a landing page for a SaaS product",
    "write PostgreSQL query for user analytics",
    "build a React component for data table",
    "deploy Docker container to production",
    "test API endpoint for authentication",
    "write documentation for REST API",
    "implement OAuth2 flow",
    "configure CI/CD pipeline with GitHub Actions",
    "migrate database schema for users table",
    "debug memory leak in Python service",
    "review pull request for SQL injection risks",
    "optimize slow database query with indexes",
    "create unit tests for authentication middleware",
    "set up monitoring and alerting for production",
    "refactor monolith into microservices",
    "implement caching layer with Redis",
    "design API rate limiting strategy",
    "investigate 500 error in payment webhook",
    "plan architecture for multi-tenant SaaS",
)

ROUTER_MODES = {
    "0": {"name": "v4_legacy", "path": "route_task", "production": False},
    "1": {"name": "unified_v2", "path": "unified_route", "production": False},
    "2": {"name": "unified_v5", "path": "unified_route_v5", "production": True},
}


def get_router_flag() -> str:
    """Return active router flag (0=v4, 1=v2, 2=v5)."""
    raw = os.environ.get("USE_UNIFIED_ROUTER", DEFAULT_ROUTER_FLAG).strip()
    return raw if raw in ROUTER_MODES else "0"


def get_router_status() -> dict[str, Any]:
    flag = get_router_flag()
    mode = ROUTER_MODES[flag]
    return {
        "issue": "I-05" if flag == "0" else None,
        "use_unified_router": int(flag),
        "mode": mode["name"],
        "path": mode["path"],
        "production_sot": mode["production"],
        "default_flag": int(DEFAULT_ROUTER_FLAG),
        "cold_fraction": 0.3,
        "top1_agreement_target": TOP1_AGREEMENT_TARGET,
        "env_source": "USE_UNIFIED_ROUTER",
    }


def _shape_v4(task: str, project: str, max_results: int) -> dict[str, Any]:
    from server.router import route_task

    ide = os.environ.get("NEXUS_IDE", "claude-code")
    result = route_task(task, project, use_embedding=True, ide=ide)
    result["method"] = result.get("method", "route_task_v4")
    result["skills"] = result.get("skills", [])[:max_results]
    return result


def _shape_v2(task: str, project: str, max_results: int) -> dict[str, Any]:
    from router_v2 import unified_route

    top_k = max(max_results, 10)
    ur = unified_route(task, top_k=top_k, cold_fraction=0.3, vec=True)
    skills = []
    for x in ur.get("merged", []):
        name = x.get("name")
        if not name:
            continue
        skills.append({
            "name": name,
            "score": round(x.get("final_score", 0.0), 4),
            "category": "INVOKE",
            "success_rate": 0,
            "type": "skill",
            "_source": "unified_router",
            "cluster": x.get("cluster"),
            "rrf_score": round(x.get("rrf_score", 0.0), 4),
        })
    return {
        "task": task,
        "project": project,
        "tier": 2,
        "tier_name": "Unified",
        "model": "sonnet",
        "token_limit": 200000,
        "skills": skills[:max_results],
        "method": "unified_router_v2",
        "cluster": skills[0]["cluster"] if skills else None,
        "pipeline": ["unified_route", "rrf_resource", "blend_4term", "two_tier"],
        "used_dense": ur.get("used_dense", False),
        "vec_reason": ur.get("vec_reason", "disabled"),
        "weights": ur.get("weights"),
        "latency_ms": None,
    }


def _shape_v5(task: str, project: str, max_results: int) -> dict[str, Any]:
    from router_v2.v5 import unified_route_v5

    top_k = max(max_results, 10)
    v5 = unified_route_v5(task, top_k=top_k, cold_fraction=0.3)
    skills = []
    for x in v5.get("merged", []):
        name = x.get("name")
        if not name:
            continue
        skills.append({
            "name": name,
            "score": round(x.get("final_score", 0.0), 4),
            "category": "INVOKE",
            "success_rate": 0,
            "type": "skill",
            "_source": "unified_router_v5",
            "cluster": x.get("cluster"),
            "l1_match": x.get("l1_match", False),
            "intent_score": round(x.get("intent_score", 0.0), 4),
        })
    return {
        "task": task,
        "project": project,
        "tier": 2,
        "tier_name": "Unified v5",
        "model": "sonnet",
        "token_limit": 200000,
        "skills": skills[:max_results],
        "method": "unified_router_v5",
        "cluster": skills[0]["cluster"] if skills else None,
        "pipeline": ["intent_route", "cluster_route", "topic_retrieve", "l3_rerank", "l4_autocut"],
        "weights": v5.get("weights"),
        "l1_short_circuit": v5.get("l1_short_circuit", False),
        "agreement": v5.get("agreement"),
        "latency_ms": v5.get("latency_ms"),
    }


# Matches routing_log CHECK constraint on ide column
_ALLOWED_IDES = frozenset({
    "opencode", "claude-code", "unknown", "hermes", "codex",
    "mcp", "auto_invoke", "http", "eval", "pytest",
})


def _sanitize_ide(ide: str | None) -> str:
    raw = (ide or os.environ.get("NEXUS_IDE") or "unknown").strip().lower()
    if raw in _ALLOWED_IDES:
        return raw
    # Common aliases
    aliases = {
        "claude": "claude-code",
        "claude_code": "claude-code",
        "open-code": "opencode",
        "test": "unknown",
        "pytest": "unknown",
        "unit": "unknown",
    }
    return aliases.get(raw, "unknown")


def log_session_open(
    task: str,
    project: str,
    result: dict[str, Any],
    *,
    ide: str | None = None,
) -> int | None:
    """P1 session.open: write routing_log with non-terminal ``routed`` only.

    Never calls record_outcome / Thompson. Returns routing_log row id or None.
    """
    try:
        from server.db import db_conn
    except Exception:
        try:
            from db import db_conn  # type: ignore
        except Exception:
            return None

    ide = _sanitize_ide(ide)
    skills = result.get("skills") or []
    skill_names = [
        s.get("name") for s in skills if isinstance(s, dict) and s.get("name")
    ]
    top = skill_names[0] if skill_names else None
    try:
        with db_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO routing_log(
                    task_summary, project, matched_tier, matched_model,
                    matched_skills, outcome, skill_name, ide
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    (task or "")[:200],
                    project or "default",
                    result.get("tier", 0),
                    result.get("model", "unknown"),
                    json.dumps(skill_names)[:500],
                    "routed",
                    top,
                    ide,
                ),
            )
            rowid = cur.lastrowid
            try:
                conn.commit()
            except Exception:
                pass
            return int(rowid) if rowid else None
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("log_session_open failed: %s", exc)
        return None


def route_for_task(
    task: str,
    project: str = "default",
    max_results: int = 5,
    *,
    flag: str | None = None,
    open_session: bool = True,
    ide: str | None = None,
) -> dict[str, Any]:
    """Dispatch routing based on ``USE_UNIFIED_ROUTER`` (or explicit flag).

    When ``open_session`` is True (default for production callers), append a
    non-terminal ``routed`` row to routing_log without belief updates (P1).
    Offline A/B comparison should pass open_session=False.
    """
    active = flag if flag is not None else get_router_flag()
    if active == "2":
        result = _shape_v5(task, project, max_results)
    elif active == "1":
        result = _shape_v2(task, project, max_results)
    else:
        result = _shape_v4(task, project, max_results)

    result["router_flag"] = int(active) if str(active).isdigit() else active
    result["router_mode"] = ROUTER_MODES.get(str(active), {}).get("name", "unknown")

    # P3: harness plan + toolsets on all cutover paths (v5 L1 may already attach
    # via pure route_task; re-attach is idempotent and strips legacy skill rows).
    try:
        from server.harness_engine.attach import attach_harness_to_result
        # Production ranking path: plan only by default (no network in hot path).
        # Pass execute_research=True via env for eager research.
        eager = os.environ.get("NEXUS_HARNESS_EAGER", "false").lower() in (
            "1", "true", "yes",
        )
        result = attach_harness_to_result(
            result, task, execute_research=eager,
        )
    except Exception:
        pass

    if open_session:
        rowid = log_session_open(task, project, result, ide=ide)
        if rowid is not None:
            result["session_log_id"] = rowid
            result["session_outcome"] = "routed"
    return result


def _skill_names(result: dict[str, Any], top_k: int) -> list[str]:
    return [
        s.get("name")
        for s in result.get("skills", [])[:top_k]
        if isinstance(s, dict) and s.get("name")
    ]


def compare_routers(
    task: str,
    project: str = "default",
    top_k: int = 10,
) -> dict[str, Any]:
    """Offline A/B: v4 vs v2 vs v5 on one query (no DB writes)."""
    results: dict[str, Any] = {"task": task, "project": project, "top_k": top_k}
    latencies: dict[str, float] = {}
    names: dict[str, list[str]] = {}

    for flag, label in (("0", "v4"), ("1", "v2"), ("2", "v5")):
        t0 = time.perf_counter()
        try:
            # Offline A/B: never write session/learning rows
            out = route_for_task(
                task, project, top_k, flag=flag, open_session=False,
            )
            names[label] = _skill_names(out, top_k)
            results[f"{label}_method"] = out.get("method")
            if label == "v5":
                results["v5_l1_short_circuit"] = out.get("l1_short_circuit")
        except Exception as exc:
            names[label] = []
            results[f"{label}_error"] = str(exc)
        latencies[label] = round((time.perf_counter() - t0) * 1000, 1)

    v4_names = names.get("v4", [])
    v5_names = names.get("v5", [])
    v2_names = names.get("v2", [])
    inter_v4_v5 = sorted(set(v4_names) & set(v5_names))
    union_v4_v5 = sorted(set(v4_names) | set(v5_names))
    top1_match = bool(v4_names and v5_names and v4_names[0] == v5_names[0])

    results.update({
        "v4_names": v4_names,
        "v2_names": v2_names,
        "v5_names": v5_names,
        "v4_v5_intersection": inter_v4_v5,
        "v4_v5_union": union_v4_v5,
        "overlap_at_k": len(inter_v4_v5),
        "agreement": round(len(inter_v4_v5) / max(len(union_v4_v5), 1), 3),
        "top1_match": top1_match,
        "latencies_ms": latencies,
    })
    return results


def run_ab_suite(
    queries: list[str] | None = None,
    project: str = "default",
    top_k: int = 5,
) -> dict[str, Any]:
    """20-query offline suite — gate: top-1 agreement ≥95% v4 vs v5."""
    suite = list(queries or STANDARD_AB_QUERIES)
    rows = []
    top1_hits = 0
    overlaps = []
    v4_lat: list[float] = []
    v5_lat: list[float] = []

    for q in suite:
        row = compare_routers(q, project=project, top_k=top_k)
        if row.get("top1_match"):
            top1_hits += 1
        overlaps.append(row.get("overlap_at_k", 0))
        v4_lat.append(row["latencies_ms"].get("v4", 0.0))
        v5_lat.append(row["latencies_ms"].get("v5", 0.0))
        rows.append({
            "query": q,
            "v4_top1": (row.get("v4_names") or [None])[0],
            "v5_top1": (row.get("v5_names") or [None])[0],
            "top1_match": row.get("top1_match"),
            "overlap_at_k": row.get("overlap_at_k"),
            "latencies_ms": row.get("latencies_ms"),
        })

    n = len(suite)
    top1_rate = top1_hits / max(n, 1)
    return {
        "phase": 7,
        "issue": "I-05",
        "queries_run": n,
        "top_k": top_k,
        "top1_agreement": round(top1_rate, 3),
        "top1_agreement_pct": round(top1_rate * 100, 1),
        "top1_hits": top1_hits,
        "top1_target": TOP1_AGREEMENT_TARGET,
        "gate_pass": top1_rate >= TOP1_AGREEMENT_TARGET,
        "avg_overlap_at_k": round(sum(overlaps) / max(n, 1), 2),
        "avg_v4_latency_ms": round(sum(v4_lat) / max(n, 1), 1),
        "avg_v5_latency_ms": round(sum(v5_lat) / max(n, 1), 1),
        "router_status": get_router_status(),
        "results": rows,
    }