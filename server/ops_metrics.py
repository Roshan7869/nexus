"""P5 Ops & eval metrics — terminal close rate, gates, harness FTSR/MRC/TLT/CDR.

Targets from NEXUS_SYSTEM_DESIGN_PLAN_v1:
  - terminal_close_rate ≥ 80%
  - live router_mode matches config
  - non-terminal must not update Thompson (structural; reported as gate)
  - catalog: zero_token_active == 0, cluster_divergence == 0
  - A/B top-1 agreement target 0.95 (optional expensive suite)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NEXUS_ROOT = Path(__file__).resolve().parent.parent

# Gates (plan targets)
TARGET_TERMINAL_CLOSE_PCT = float(os.environ.get("NEXUS_TARGET_CLOSE_RATE", "80"))
TARGET_NEVER_TRIGGERED_PCT = float(os.environ.get("NEXUS_TARGET_COLD_PCT", "35"))
TARGET_AB_TOP1 = float(os.environ.get("NEXUS_TARGET_AB_TOP1", "0.95"))


def _db_conn():
    from server.db import db_conn
    return db_conn()


def routing_coverage(conn=None) -> dict[str, Any]:
    """Terminal close rate on routing_log + recent window."""
    owns = conn is None
    if owns:
        from server.db import get_connection
        conn = get_connection()
        conn.row_factory = __import__("sqlite3").Row
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM routing_log").fetchone()["n"]
        by = {
            (r["outcome"] if r["outcome"] is not None else "NULL"): r["n"]
            for r in conn.execute(
                "SELECT outcome, COUNT(*) AS n FROM routing_log GROUP BY outcome"
            )
        }
        terminal = sum(
            by.get(k, 0) for k in ("correct", "wrong", "partial", "skip")
        )
        # NULL key stored as string 'NULL' from the group-by mapping above
        non_terminal = by.get("NULL", 0) + by.get("routed", 0) + by.get("pending", 0)

        correct = by.get("correct", 0)
        wrong = by.get("wrong", 0)
        partial = by.get("partial", 0)

        week_total = conn.execute(
            "SELECT COUNT(*) AS n FROM routing_log "
            "WHERE created_at > datetime('now', '-7 days')"
        ).fetchone()["n"]
        week_terminal = conn.execute(
            """
            SELECT COUNT(*) AS n FROM routing_log
            WHERE created_at > datetime('now', '-7 days')
              AND outcome IN ('correct','wrong','partial','skip')
            """
        ).fetchone()["n"]

        close_pct = round(100.0 * terminal / max(total, 1), 2)
        week_close_pct = round(100.0 * week_terminal / max(week_total, 1), 2)
        binary_acc = round(
            100.0 * correct / max(correct + wrong, 1), 2
        )
        weighted = round(
            100.0 * (correct + 0.5 * partial) / max(terminal, 1), 2
        )

        return {
            "total_routes": total,
            "by_outcome": by,
            "terminal_count": terminal,
            "non_terminal_count": non_terminal,
            "terminal_close_rate_pct": close_pct,
            "terminal_close_target_pct": TARGET_TERMINAL_CLOSE_PCT,
            "terminal_close_gate_pass": close_pct >= TARGET_TERMINAL_CLOSE_PCT,
            "binary_accuracy_excl_partial_pct": binary_acc,
            "weighted_accuracy_pct": weighted,
            "window_7d": {
                "total": week_total,
                "terminal": week_terminal,
                "terminal_close_rate_pct": week_close_pct,
            },
            "tool_sessions": _session_coverage(conn),
        }
    finally:
        if owns:
            conn.close()


def _session_coverage(conn) -> dict[str, Any]:
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM tool_sessions").fetchone()["n"]
        closed = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_sessions "
            "WHERE status IN ('completed','abandoned','failed')"
        ).fetchone()["n"]
        correct = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_sessions WHERE outcome='correct'"
        ).fetchone()["n"]
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_sessions WHERE status='active'"
        ).fetchone()["n"]
        return {
            "total": total,
            "closed": closed,
            "active": active,
            "correct": correct,
            "close_rate_pct": round(100.0 * closed / max(total, 1), 2),
            "success_rate_pct": round(100.0 * correct / max(closed, 1), 2),
        }
    except Exception:
        return {"total": 0, "closed": 0, "active": 0}


def catalog_ops(conn=None) -> dict[str, Any]:
    owns = conn is None
    if owns:
        from server.db import get_connection
        conn = get_connection()
        conn.row_factory = __import__("sqlite3").Row
    try:
        from server.catalog_lifecycle import catalog_metrics
        m = catalog_metrics(conn)
        # Legacy all-active cold (informational)
        m["never_triggered_target_pct"] = TARGET_NEVER_TRIGGERED_PCT
        m["never_triggered_gate_pass"] = (
            m.get("never_triggered_pct", 100) <= TARGET_NEVER_TRIGGERED_PCT
        )
        # Gap 1 primary KPI: invoke-type cold only
        m["invoke_cold_gate_pass"] = bool(m.get("invoke_cold_gate_pass"))
        m["zero_token_gate_pass"] = m.get("zero_token_active", 1) == 0
        m["divergence_gate_pass"] = m.get("cluster_divergence", 1) == 0
        return m
    finally:
        if owns:
            conn.close()


def router_ops() -> dict[str, Any]:
    from server.router_cutover import get_router_status
    from server.execution_plane import health_v1

    st = get_router_status()
    hv = health_v1()
    expected = str(os.environ.get("USE_UNIFIED_ROUTER", st.get("default_flag", "2")))
    actual = str(st.get("use_unified_router", ""))
    return {
        "router_status": st,
        "health_v1": {
            "router_mode": hv.get("router_mode"),
            "router_flag": hv.get("router_flag"),
            "db_ok": hv.get("db_ok"),
            "git_sha": hv.get("git_sha"),
            "contract": hv.get("contract"),
        },
        "expected_flag": expected,
        "actual_flag": actual,
        "router_mode_match": expected == actual,
        "production_sot": st.get("production_sot"),
    }


def harness_ops(project: str | None = None) -> dict[str, Any]:
    try:
        from server.harness_metrics import (
            compute_global_metrics,
            compute_project_metrics,
            build_harness_dashboard,
        )
        if project:
            metrics = compute_project_metrics(project)
        else:
            metrics = compute_global_metrics()
        dash = build_harness_dashboard(project)
        return {"metrics": metrics, "dashboard": dash}
    except Exception as e:
        logger.warning("harness metrics failed: %s", e)
        return {"error": str(e)}


def learning_integrity(conn=None) -> dict[str, Any]:
    """Structural checks: non-terminal should not dominate recent belief spam."""
    owns = conn is None
    if owns:
        from server.db import get_connection
        conn = get_connection()
        conn.row_factory = __import__("sqlite3").Row
    try:
        recent_routed = conn.execute(
            """
            SELECT COUNT(*) AS n FROM routing_log
            WHERE outcome = 'routed'
              AND created_at > datetime('now', '-1 days')
            """
        ).fetchone()["n"]
        recent_terminal = conn.execute(
            """
            SELECT COUNT(*) AS n FROM routing_log
            WHERE outcome IN ('correct','wrong','partial')
              AND created_at > datetime('now', '-1 days')
            """
        ).fetchone()["n"]
        # High confidence beliefs
        high = conn.execute(
            "SELECT COUNT(*) AS n FROM model_beliefs WHERE total_requests >= 10"
        ).fetchone()["n"]
        beliefs = conn.execute(
            "SELECT COUNT(*) AS n FROM model_beliefs"
        ).fetchone()["n"]
        return {
            "recent_24h_routed": recent_routed,
            "recent_24h_terminal": recent_terminal,
            "beliefs_total": beliefs,
            "beliefs_high_confidence": high,
            "note": (
                "Non-terminal 'routed' rows are expected from session.open; "
                "they must not update Thompson (P0 guard)."
            ),
        }
    finally:
        if owns:
            conn.close()


def build_ops_report(
    *,
    project: str | None = None,
    include_ab: bool = False,
    ab_quick: bool = True,
) -> dict[str, Any]:
    """Full P5 ops report with gate matrix."""
    t0 = time.perf_counter()
    from server.db import get_connection
    import sqlite3

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        coverage = routing_coverage(conn)
        catalog = catalog_ops(conn)
        learning = learning_integrity(conn)
    finally:
        conn.close()

    router = router_ops()
    harness = harness_ops(project)

    # Gap 2: process topology (MCP/HTTP STOPPED under supervisor is intentional)
    process_status: dict[str, Any] = {}
    try:
        from server.process_supervisor import get_process_status

        process_status = get_process_status()
    except Exception as exc:
        process_status = {"gate_pass": False, "error": str(exc)}

    # Gap 3: co-trigger edge learning
    edge_stats: dict[str, Any] = {}
    try:
        from server.learning import edge_learning_stats

        edge_stats = edge_learning_stats()
    except Exception as exc:
        edge_stats = {"edge_learning_gate_pass": False, "error": str(exc)}

    # Gap 4: IDE event loop
    ide_stats: dict[str, Any] = {}
    try:
        from server.rl_feedback_loop import ide_event_stats

        ide_stats = ide_event_stats()
    except Exception as exc:
        ide_stats = {"ide_event_loop_gate_pass": False, "error": str(exc)}

    # Gap 5: OKF residual (cold without sections)
    okf_stats: dict[str, Any] = {}
    try:
        cold_wo = catalog.get("cold_without_sections")
        if cold_wo is None:
            cold_wo = catalog.get("never_triggered_active")  # fallback
        okf_stats = {
            "cold_without_sections": cold_wo,
            "okf_residual_gate_pass": int(cold_wo or 0) <= 20,
        }
    except Exception as exc:
        okf_stats = {"okf_residual_gate_pass": False, "error": str(exc)}

    # Gap 6: chunk → resource link
    chunk_link: dict[str, Any] = {}
    try:
        from server.chunk_resource_link import chunk_resource_stats

        chunk_link = chunk_resource_stats()
    except Exception as exc:
        chunk_link = {"chunk_resource_link_gate_pass": False, "error": str(exc)}

    gates = {
        "terminal_close_rate": {
            "pass": coverage["terminal_close_gate_pass"],
            "value": coverage["terminal_close_rate_pct"],
            "target": coverage["terminal_close_target_pct"],
        },
        "router_mode_match": {
            "pass": router["router_mode_match"],
            "value": router["actual_flag"],
            "target": router["expected_flag"],
        },
        "zero_token_active": {
            "pass": catalog.get("zero_token_gate_pass", False),
            "value": catalog.get("zero_token_active"),
            "target": 0,
        },
        "cluster_divergence": {
            "pass": catalog.get("divergence_gate_pass", False),
            "value": catalog.get("cluster_divergence"),
            "target": 0,
        },
        "db_ok": {
            "pass": bool(router.get("health_v1", {}).get("db_ok")),
            "value": router.get("health_v1", {}).get("db_ok"),
            "target": True,
        },
        # Gap 1: invoke-type cold inventory (reference types excluded)
        "invoke_cold_rate": {
            "pass": bool(catalog.get("invoke_cold_gate_pass")),
            "value": catalog.get("invoke_never_triggered_pct"),
            "target": catalog.get("invoke_cold_target_pct", TARGET_NEVER_TRIGGERED_PCT),
        },
        # Gap 2: required long-lived owners only (HTTP+dashboard+supervisord)
        "process_topology": {
            "pass": bool(process_status.get("gate_pass")),
            "value": process_status.get("health_pct"),
            "target": 100.0,
            "detail": process_status.get("topology", {}).get("summary")
            or process_status.get("error"),
        },
        # Gap 3: co-trigger graph has live learning signal
        "edge_learning": {
            "pass": bool(edge_stats.get("edge_learning_gate_pass")),
            "value": edge_stats.get("co_triggered_with_count"),
            "target": 10,
            "detail": (
                f"co_triggered={edge_stats.get('co_triggered')} "
                f"with_count={edge_stats.get('co_triggered_with_count')}"
            ),
        },
        # Gap 4: IDE events on resource brain (non-fixture)
        "ide_event_loop": {
            "pass": bool(ide_stats.get("ide_event_loop_gate_pass")),
            "value": ide_stats.get("ide_events_real_30d"),
            "target": 5,
            "detail": f"7d={ide_stats.get('ide_events_7d')} total={ide_stats.get('ide_events_total')}",
        },
        # Gap 5: cold inventory without OKF/catalog sections
        "okf_residual": {
            "pass": bool(okf_stats.get("okf_residual_gate_pass")),
            "value": okf_stats.get("cold_without_sections"),
            "target": 20,
        },
        # Gap 6: chunk embeddings linked to resources
        "chunk_resource_link": {
            "pass": bool(chunk_link.get("chunk_resource_link_gate_pass")),
            "value": chunk_link.get("linked_pct"),
            "target": 50.0,
            "detail": f"linked={chunk_link.get('linked')}/{chunk_link.get('total_embeddings')}",
        },
    }

    ab_result = None
    if include_ab:
        ab_result = run_ab_eval(quick=ab_quick)
        gates["ab_top1_agreement"] = {
            "pass": ab_result.get("gate_pass", False),
            "value": ab_result.get("top1_agreement"),
            "target": ab_result.get("top1_target", TARGET_AB_TOP1),
        }

    all_pass = all(g["pass"] for g in gates.values())
    return {
        "phase": "P5",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "overall_pass": all_pass,
        "gates": gates,
        "routing_coverage": coverage,
        "catalog": catalog,
        "router": router,
        "harness": harness,
        "learning_integrity": learning,
        "process_status": process_status,
        "edge_learning": edge_stats,
        "ide_events": ide_stats,
        "okf_residual": okf_stats,
        "chunk_resource_link": chunk_link,
        "ab_suite": ab_result,
    }


def run_ab_eval(*, quick: bool = True, top_k: int = 5) -> dict[str, Any]:
    """Run router A/B suite. quick=True uses first 5 STANDARD queries."""
    from server.router_cutover import run_ab_suite, STANDARD_AB_QUERIES

    queries = list(STANDARD_AB_QUERIES)
    if quick:
        queries = queries[:5]
    t0 = time.perf_counter()
    result = run_ab_suite(queries=queries, top_k=top_k)
    result["mode"] = "quick" if quick else "full"
    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    # Soften gate for quick mode (informational if n < 20)
    if quick and result.get("queries_run", 0) < 20:
        result["gate_note"] = "quick mode — gate is informational; use --full for ship gate"
    return result


def write_report(path: Path | str, report: dict[str, Any]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    return str(path)


def run_cli(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="NEXUS P5 ops eval")
    p.add_argument("--json", action="store_true")
    p.add_argument("--ab", action="store_true", help="Include A/B router suite")
    p.add_argument("--full-ab", action="store_true", help="Full 20-query A/B suite")
    p.add_argument("--project", default=None)
    p.add_argument(
        "--out",
        default=str(NEXUS_ROOT / "logs" / "ops_eval_latest.json"),
        help="Write JSON report path",
    )
    p.add_argument("--fail-on-gate", action="store_true", help="Exit 1 if any gate fails")
    args = p.parse_args(argv)

    report = build_ops_report(
        project=args.project,
        include_ab=args.ab or args.full_ab,
        ab_quick=not args.full_ab,
    )
    write_report(args.out, report)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("=== NEXUS P5 Ops Eval ===")
        print(f"overall_pass={report['overall_pass']}")
        for name, g in report["gates"].items():
            status = "PASS" if g["pass"] else "FAIL"
            print(f"  [{status}] {name}: {g['value']} (target {g['target']})")
        cov = report["routing_coverage"]
        print(
            f"close_rate={cov['terminal_close_rate_pct']}% "
            f"binary_acc={cov['binary_accuracy_excl_partial_pct']}% "
            f"weighted={cov['weighted_accuracy_pct']}%"
        )
        if report.get("ab_suite"):
            ab = report["ab_suite"]
            print(
                f"AB top1={ab.get('top1_agreement_pct')}% "
                f"gate={ab.get('gate_pass')} mode={ab.get('mode')}"
            )
        print(f"wrote {args.out}")

    if args.fail_on_gate and not report["overall_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
