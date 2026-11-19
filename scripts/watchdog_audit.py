"""S6 watchdog — shadow audit CLI. Writes ONLY to watchdog_events."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--window", type=int, default=3500)
    ap.add_argument("--eval", action="store_true",
                    help="also run the constructed-detour report (no writes)")
    args = ap.parse_args()

    import sqlite3

    def _db_path() -> str:
        if args.db:
            return args.db
        try:
            from server.db import get_db_path
            return str(get_db_path())
        except Exception:
            return r"D:/nexus-data/db/nexus.db"

    from server.watchdog import audit_routing_log, evaluate_detector

    con = sqlite3.connect(_db_path(), timeout=60)
    res = audit_routing_log(con, window=args.window, write_events=True)
    print(f"shadow audit: scanned {res['scanned']} routes, "
          f"{res['anomalies']} anomalies, {res['groups']} intent groups")
    usable = con.execute(
        "SELECT COUNT(*) FROM routing_log WHERE latency_ms IS NOT NULL "
        "AND latency_ms != 0").fetchone()[0]
    print(f"telemetry rows with latency_ms: {usable} "
          f"({'watchdog live-eval possible' if usable >= 100 else 'ACCUMULATING — live eval deferred'})")
    if args.eval and usable >= 100:
        ev = evaluate_detector(con, n_detours=100)
        print(f"detour report: {ev['detected']}/{ev['detours']} "
              f"(CI {ev['detection_ci']}) | healthy FPR {ev['fpr']} "
              f"(CI {ev['fpr_ci']}) — report-only, no gate, no belief writes")
    events = con.execute("SELECT COUNT(*) FROM watchdog_events").fetchone()[0]
    con.close()
    print(f"watchdog_events total: {events} (shadow writes only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
