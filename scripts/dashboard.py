#!/usr/bin/env python3
"""NEXUS Routing Dashboard — health overview in one shot.

Metrics:
  - Routing coverage %       : unique resources triggered / total active
  - Fallback rate            : fallback queries / total routing logs
  - Close rate               : routing logs with outcome / total
  - TS belief count          : learned model beliefs
  - Top triggered resources  : most-routed skills
  - Cluster distribution     : resources per cluster
  - Latency percentiles      : routing speed (last 48h)
  - Recent activity          : last 50 routing decisions
"""

import os, sys, json, time
sys.path.insert(0, os.path.expanduser("~/nexus"))

import sqlite3
from pathlib import Path
from collections import Counter

DB_PATH = Path.home() / "nexus/db/nexus.db"


def fmt(n):
    if isinstance(n, float):
        return f"{n:.1f}"
    return str(n)


def get_dashboard() -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    total_active = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
    triggered = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active' AND times_triggered > 0").fetchone()[0]
    total_logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    fallbacks = conn.execute("SELECT COUNT(*) FROM routing_log WHERE matched_tier = 1").fetchone()[0]
    with_outcome = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IS NOT NULL").fetchone()[0]
    null_outcome = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IS NULL").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='correct'").fetchone()[0]
    wrong = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='wrong'").fetchone()[0]
    beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
    reflections = conn.execute("SELECT COUNT(*) FROM reflection_fixes").fetchone()[0]

    # Top triggered
    top_resources = conn.execute(
        "SELECT name, times_triggered, cluster, cluster_confidence "
        "FROM resources WHERE status='active' AND times_triggered > 0 "
        "ORDER BY times_triggered DESC LIMIT 10"
    ).fetchall()

    # Recent routing (last 50)
    recent = conn.execute(
        "SELECT task_summary, matched_skills, outcome, "
        "       matched_tier, created_at "
        "FROM routing_log ORDER BY id DESC LIMIT 50"
    ).fetchall()

    # Per-cluster trigger stats
    cluster_stats = conn.execute(
        "SELECT r.cluster, COUNT(DISTINCT r.id) as total, "
        "       SUM(CASE WHEN r.times_triggered > 0 THEN 1 ELSE 0 END) as triggered, "
        "       ROUND(AVG(r.cluster_confidence), 3) as avg_conf "
        "FROM resources r WHERE r.status='active' AND r.cluster IS NOT NULL "
        "GROUP BY r.cluster ORDER BY total DESC"
    ).fetchall()

    # Latency percentiles (last 48h) — from process_turn log if available, else empty
    try:
        latency = conn.execute(
            "SELECT total_ms FROM process_turn_log "
            "WHERE created_at >= datetime('now', '-2 days')"
        ).fetchall()
        lat_vals = sorted([r["total_ms"] for r in latency])
    except Exception:
        lat_vals = []

    # Recent activity timeline (last 20)
    timeline = conn.execute(
        "SELECT task_summary, outcome, matched_tier, created_at "
        "FROM routing_log ORDER BY id DESC LIMIT 20"
    ).fetchall()

    conn.close()

    coverage = triggered / max(total_active, 1) * 100
    fallback_rate = fallbacks / max(total_logs, 1) * 100
    close_rate = with_outcome / max(total_logs, 1) * 100
    accuracy = correct / max(with_outcome, 1) * 100

    return {
        "coverage": {
            "total_active": total_active,
            "triggered": triggered,
            "pct": round(coverage, 1),
        },
        "routing_log": {
            "total": total_logs,
            "with_outcome": with_outcome,
            "pending_feedback": null_outcome,
            "correct": correct,
            "wrong": wrong,
            "fallbacks": fallbacks,
            "fallback_rate": round(fallback_rate, 1),
            "close_rate": round(close_rate, 1),
            "accuracy": round(accuracy, 1),
        },
        "learning": {
            "ts_beliefs": beliefs,
            "reflections": reflections,
        },
        "latency_ms": {
            "p50": lat_vals[len(lat_vals) // 2] if lat_vals else 0,
            "p90": lat_vals[int(len(lat_vals) * 0.9)] if lat_vals else 0,
            "p95": lat_vals[int(len(lat_vals) * 0.95)] if lat_vals else 0,
            "samples": len(lat_vals),
        },
        "clusters": {r["cluster"]: {"total": r["total"], "triggered": r["triggered"], "avg_conf": r["avg_conf"]} for r in cluster_stats},
        "top_triggered": [{"name": r["name"], "times": r["times_triggered"], "cluster": r["cluster"], "conf": r["cluster_confidence"]} for r in top_resources],
        "recent_timeline": [dict(r) for r in timeline],
    }


def print_dashboard(d: dict):
    print("=" * 72)
    print("  NEXUS v4 Routing Dashboard")
    print("=" * 72)

    c = d["coverage"]
    print(f"\n  Coverage:       {c['triggered']}/{c['total_active']} resources ({c['pct']}%)")

    rl = d["routing_log"]
    print(f"  Routing Log:    {rl['total']} total, {rl['pending_feedback']} pending")
    print(f"  Close Rate:     {rl['close_rate']}%  ({rl['with_outcome']}/{rl['total']})")
    print(f"  Accuracy:       {rl['accuracy']}%  ({rl['correct']} correct, {rl['wrong']} wrong)")
    print(f"  Fallback Rate:  {rl['fallback_rate']}%  ({rl['fallbacks']} fallbacks)")

    lr = d["learning"]
    print(f"  TS Beliefs:     {lr['ts_beliefs']}")
    print(f"  Reflections:    {lr['reflections']}")

    lat = d["latency_ms"]
    print(f"  Latency (ms):   p50={lat['p50']:.0f}  p90={lat['p90']:.0f}  p95={lat['p95']:.0f}  (n={lat['samples']})")

    print(f"\n  ── Cluster Coverage ──")
    for name, s in sorted(d["clusters"].items(), key=lambda x: -x[1]["total"]):
        pct = s["triggered"] / max(s["total"], 1) * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {name:22s} {s['triggered']:3d}/{s['total']:<3d} {bar} {pct:.0f}%  (conf={s['avg_conf']:.2f})")

    print(f"\n  ── Top Triggered Resources ──")
    for r in d["top_triggered"][:10]:
        print(f"  {r['name']:40s}  ×{r['times']:3d}  [{r['cluster']}]")

    print(f"\n  ── Recent Activity (last 20) ──")
    for row in d["recent_timeline"]:
        q = row["task_summary"][:50] if row["task_summary"] else ""
        fb = " ⚠" if row.get("matched_tier") == 1 else ""
        out = row["outcome"] or "?"
        print(f"  {out:8s}{fb}  {q}")

    print()


def print_json(d: dict):
    print(json.dumps(d, indent=2, default=str))


if __name__ == "__main__":
    import sys as _sys
    data = get_dashboard()
    if "--json" in _sys.argv:
        print_json(data)
    else:
        print_dashboard(data)
