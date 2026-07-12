#!/usr/bin/env python3
"""NEXUS Retrieval Quality Dashboard — Simple HTTP server.

Serves a single-page HTML dashboard with metrics from nexus.db:
- precision@5, outcome close_rate, p50/p95 latency
- Top 10 failing queries
- 7-day rolling window metrics

Usage:
    python3 server/dashboard/server.py          # Port 8765
    python3 server/dashboard/server.py --port 9000
"""
import json, os, sqlite3, sys, time
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

NEXUS_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("NEXUS_DB_PATH", str(NEXUS_ROOT / "db" / "nexus.db")))
PORT = int(os.environ.get("NEXUS_DASHBOARD_PORT", "8765"))


def get_metrics():
    # P5: prefer ops_metrics module when available
    try:
        sys.path.insert(0, str(NEXUS_ROOT))
        sys.path.insert(0, str(NEXUS_ROOT / "server"))
        from server.ops_metrics import build_ops_report
        report = build_ops_report(include_ab=False)
        # Keep legacy shape + embed full P5 report
        cov = report["routing_coverage"]
        cat = report["catalog"]
        return {
            "outcome_coverage": {
                "total_routes": cov["total_routes"],
                "closed": cov["terminal_count"],
                "correct": cov["by_outcome"].get("correct", 0),
                "wrong": cov["by_outcome"].get("wrong", 0),
                "partial": cov["by_outcome"].get("partial", 0),
                "routed": cov["by_outcome"].get("routed", 0),
                "close_rate": cov["terminal_close_rate_pct"],
                "accuracy": cov["binary_accuracy_excl_partial_pct"],
                "weighted_accuracy": cov["weighted_accuracy_pct"],
                "gate_pass": cov["terminal_close_gate_pass"],
                "target_pct": cov["terminal_close_target_pct"],
            },
            "7_day_window": cov.get("window_7d", {}),
            "resource_health": {
                "active": cat.get("active"),
                "archived": cat.get("archived"),
                "never_triggered_pct": cat.get("never_triggered_pct"),
                "zero_token_active": cat.get("zero_token_active"),
                "cluster_divergence": cat.get("cluster_divergence"),
            },
            "gates": report["gates"],
            "overall_pass": report["overall_pass"],
            "harness": report.get("harness"),
            "router": report.get("router"),
            "p5": report,
            "legacy": True,
        }
    except Exception as exc:
        # fall through to legacy path
        _ = exc

    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row

    # Outcome coverage
    total = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    closed = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IN ('correct','wrong')").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='correct'").fetchone()[0]
    wrong = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='wrong'").fetchone()[0]

    # 7-day window
    week_ago = time.time() - 7 * 86400
    week_total = conn.execute(
        "SELECT COUNT(*) FROM routing_log WHERE created_at > datetime('now', '-7 days')"
    ).fetchone()[0]
    week_closed = conn.execute(
        "SELECT COUNT(*) FROM routing_log WHERE outcome IN ('correct','wrong') AND created_at > datetime('now', '-7 days')"
    ).fetchone()[0]

    # Resource health
    active = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
    degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
    with_usage = conn.execute("SELECT COUNT(*) FROM resources WHERE times_triggered > 0").fetchone()[0]

    # Thompson beliefs
    beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
    high_conf = conn.execute("SELECT COUNT(*) FROM model_beliefs WHERE total_requests >= 10").fetchone()[0]

    # Top failing queries (wrong outcome)
    failing = conn.execute("""
        SELECT task_summary, skill_name, outcome, created_at
        FROM routing_log WHERE outcome='wrong'
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()

    # Top used skills
    top_skills = conn.execute("""
        SELECT name, times_triggered, times_correct, times_wrong,
               ROUND(COALESCE(times_correct*100.0/NULLIF(times_triggered,0), 0), 1) as success_rate
        FROM resources WHERE times_triggered > 0
        ORDER BY times_triggered DESC LIMIT 10
    """).fetchall()

    # Cluster affinity scores
    clusters = conn.execute("""
        SELECT r.cluster, COUNT(*) as total,
               SUM(CASE WHEN rl.outcome='correct' THEN 1 ELSE 0 END) as correct
        FROM routing_log rl
        JOIN resources r ON rl.skill_name = r.name
        WHERE rl.outcome IN ('correct','wrong') AND r.cluster IS NOT NULL
        GROUP BY r.cluster
        ORDER BY total DESC
    """).fetchall()

    # FAISS index info
    meta_path = DB_PATH.parent / 'faiss/resources.index.meta.json'
    faiss_info = {}
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        faiss_info = {
            "n_total": meta.get("n_total", 0),
            "built_at": meta.get("built_at", "unknown"),
            "checksum": meta.get("checksum_sha256", "unknown")[:16] + "...",
        }

    conn.close()

    return {
        "outcome_coverage": {
            "total_routes": total,
            "closed": closed,
            "correct": correct,
            "wrong": wrong,
            "close_rate": round(closed / max(total, 1) * 100, 1),
            "accuracy": round(correct / max(correct + wrong, 1) * 100, 1),
        },
        "7_day_window": {
            "total": week_total,
            "closed": week_closed,
            "close_rate": round(week_closed / max(week_total, 1) * 100, 1),
        },
        "resource_health": {
            "active": active,
            "degraded": degraded,
            "with_usage": with_usage,
            "coverage": round(with_usage / max(active, 1) * 100, 1),
        },
        "thompson": {
            "total_beliefs": beliefs,
            "high_confidence": high_conf,
        },
        "faiss": faiss_info,
        "failing_queries": [dict(r) for r in failing],
        "top_skills": [dict(r) for r in top_skills],
        "cluster_affinities": [
            {"cluster": r["cluster"], "total": r["total"], "correct": r["correct"],
             "affinity": round(r["correct"] / max(r["total"], 1), 3)}
            for r in clusters
        ],
    }


def get_ops():
    sys.path.insert(0, str(NEXUS_ROOT))
    sys.path.insert(0, str(NEXUS_ROOT / "server"))
    from server.process_supervisor import get_ops_snapshot
    from server.research_service import get_research_status
    snap = get_ops_snapshot()
    snap["research"] = get_research_status()
    return snap


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/metrics':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(get_metrics(), indent=2).encode())
        elif parsed.path == '/api/ops':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(get_ops(), indent=2).encode())
        elif parsed.path == '/api/p5' or parsed.path == '/api/ops/eval':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                sys.path.insert(0, str(NEXUS_ROOT))
                sys.path.insert(0, str(NEXUS_ROOT / "server"))
                from server.ops_metrics import build_ops_report
                payload = build_ops_report(include_ab=False)
            except Exception as e:
                payload = {"error": str(e)}
            self.wfile.write(json.dumps(payload, indent=2, default=str).encode())
        elif parsed.path == '/api/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            payload = {
                "status": "ok",
                "service": "nexus-dashboard",
                "port": PORT,
                "db": str(DB_PATH),
            }
            self.wfile.write(json.dumps(payload).encode())
        elif parsed.path == '/' or parsed.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            index_path = Path(__file__).parent / 'index.html'
            with open(index_path) as f:
                self.wfile.write(f.read().encode())
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # Suppress request logging


if __name__ == '__main__':
    port = PORT
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])

    print(f"NEXUS Dashboard → http://localhost:{port}")
    print(f"API: http://localhost:{port}/api/metrics")
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.server_close()
