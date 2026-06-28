#!/usr/bin/env python3
"""L7 — verified-state reconciliation. Pattern-backed (Great Expectations / dbt
tests), not paper-grounded.

Every NEXUS plan baseline in v2/v3 was built on STALE claims (EFC 0% claimed
but 100% real; 2117 isolated claimed but 0 real; "feedback 34.8%" that was
actually outcome_log EFC coverage, not routing feedback). This script forces
reconciliation before any plan is authored: it re-runs each metric's
source_query against the live DB, writes {value, verified_at, source_query,
drift_status} into state_snapshot, and flags drift vs the prior snapshot.

Read-only except the state_snapshot table. Never touches tables that feed
Thompson, never kills MCP. Idempotent.

Usage:  venv/bin/python3 scripts/verify_state.py [--tolerance 0.02] [--json]
Exit:   0 if no drift, 1 if any metric drifted (use as a CI/plan gate).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "nexus.db"
MIGRATION = ROOT / "migrations" / "008_state_snapshot.sql"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row
    # vec_chunks_content is a vec0 virtual table — load the extension so the
    # chunk-index-coverage metric query can run. Best-effort: if sqlite_vec is
    # unavailable the chunk metrics report an error and the rest still reconcile.
    try:
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        conn.enable_load_extension(False)
    conn.executescript(MIGRATION.read_text())  # CREATE TABLE IF NOT EXISTS
    return conn


# ── metric definitions: name -> (sql, tolerance) ────────────────────────────
# Each SQL returns a single scalar. Keep queries read-only and cheap.
# Tolerances are absolute (|value - prev| <= tol => ok).
METRICS: dict[str, tuple[str, float]] = {
    # R3 — feedback loop (the conflation trap lives here: routing_log vs outcome_log)
    "routing_feedback_coverage": (
        "SELECT 1.0 * COUNT(*) / MAX(1, (SELECT COUNT(*) FROM routing_log)) "
        "FROM routing_log WHERE outcome IN ('correct','wrong','partial')", 0.05),
    "outcome_log_efc_coverage": (
        "SELECT 1.0 * COUNT(efc_score) / MAX(1, COUNT(*)) FROM outcome_log", 0.05),
    "auto_closed_correct_rows": (
        "SELECT COUNT(*) FROM outcome_log WHERE notes LIKE '%auto-closed%' "
        "AND outcome='correct'", 0.0),  # must never grow — inflation artifact
    "routing_accuracy": (
        "SELECT 1.0 * SUM(times_correct) / MAX(1, SUM(times_correct)+SUM(times_wrong)) "
        "FROM resources WHERE times_correct+times_wrong > 0", 0.02),

    # R2 — cold start
    "cold_resource_pct": (
        "SELECT 1.0 * COUNT(*) / MAX(1, (SELECT COUNT(*) FROM resources)) "
        "FROM resources WHERE times_triggered = 0 OR times_triggered IS NULL", 0.03),

    # R1 — routing structure
    "unassigned_clusters": (
        "SELECT COUNT(*) FROM resources WHERE cluster IS NULL", 0.0),
    "isolated_resources": (
        "SELECT COUNT(*) FROM resources r WHERE NOT EXISTS "
        "(SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id)", 0.0),
    "cluster_count": (
        "SELECT COUNT(DISTINCT cluster) FROM resources WHERE cluster IS NOT NULL", 0.0),

    # R4 — data hygiene (chunks linked to resources)
    "resource_count": ("SELECT COUNT(*) FROM resources", 0.0),
    "outcome_log_rows": ("SELECT COUNT(*) FROM outcome_log", 0.0),

    # R5 — active chunks (the 66,495 ingestion_chunks actively participating)
    "chunk_total": ("SELECT COUNT(*) FROM ingestion_chunks", 0.0),
    "chunk_embedding_coverage": (
        "SELECT 1.0 * COUNT(*) / MAX(1, (SELECT COUNT(*) FROM ingestion_chunks)) "
        "FROM chunk_embeddings", 0.05),
    "chunk_index_coverage": (
        "SELECT 1.0 * COUNT(*) / MAX(1, (SELECT COUNT(*) FROM ingestion_chunks)) "
        "FROM vec_chunks_content", 0.05),
    "chunk_link_coverage": (
        "SELECT 1.0 * COUNT(resource_id) / MAX(1, COUNT(*)) "
        "FROM ingestion_chunks", 0.05),
}


def reconcile(conn: sqlite3.Connection, tolerance_default: float) -> dict:
    """Re-run every metric, write snapshot, flag drift. Returns report dict."""
    now = time.time()
    report: dict = {"metrics": {}, "drifted": [], "unverified": [], "ok": []}

    for metric, (sql, tol) in METRICS.items():
        try:
            value = float(conn.execute(sql).fetchone()[0] or 0.0)
        except Exception as e:
            report["metrics"][metric] = {"error": str(e), "source_query": sql}
            continue

        prev = conn.execute(
            "SELECT value FROM state_snapshot WHERE metric=?", (metric,)
        ).fetchone()
        prev_val = prev["value"] if prev else None

        if prev_val is None:
            status = "unverified"
            report["unverified"].append(metric)
        elif abs(value - float(prev_val)) <= tol:
            status = "ok"
            report["ok"].append(metric)
        else:
            status = "drifted"
            report["drifted"].append({
                "metric": metric, "prev": prev_val, "now": value,
                "delta": value - float(prev_val),
            })

        conn.execute(
            "INSERT INTO state_snapshot(metric, value, verified_at, source_query, "
            "drift_status, prev_value, tolerance) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(metric) DO UPDATE SET "
            "  value=excluded.value, verified_at=excluded.verified_at, "
            "  source_query=excluded.source_query, drift_status=excluded.drift_status, "
            "  prev_value=excluded.prev_value, tolerance=excluded.tolerance",
            (metric, value, now, sql, status, prev_val, tol),
        )
        report["metrics"][metric] = {
            "value": round(value, 6), "drift_status": status,
            "prev": prev_val, "tolerance": tol,
        }

    conn.commit()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit JSON instead of human report")
    args = ap.parse_args()

    conn = _get_conn()
    try:
        report = reconcile(conn, 0.02)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("=" * 70)
        print("NEXUS verified-state reconciliation (L7)")
        print("=" * 70)
        for metric, m in report["metrics"].items():
            if "error" in m:
                print(f"  {metric:<28} ERROR  {m['error']}")
                continue
            v = m["value"]
            print(f"  {metric:<28} {v:>12.4f}  [{m['drift_status']}]  "
                  f"tol={m['tolerance']} prev={m['prev']}")
        print("-" * 70)
        print(f"  ok: {len(report['ok'])}  drifted: {len(report['drifted'])}  "
              f"unverified: {len(report['unverified'])}")
        if report["drifted"]:
            print("  DRIFT DETECTED:")
            for d in report["drifted"]:
                print(f"    {d['metric']}: {d['prev']} -> {d['now']} (delta {d['delta']:+.4f})")

    return 1 if report["drifted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())