#!/usr/bin/env python3
"""Gap 5: OKF / section residual for cold resources.

Dry-run:
  python scripts/run_okf_residual.py

Apply:
  python scripts/run_okf_residual.py --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.okf_sections import backfill_cold_sections  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    db = ROOT / "db" / "nexus.db"
    con = sqlite3.connect(str(db), timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    before = con.execute(
        """
        SELECT COUNT(*) FROM resources r
        WHERE r.status='active' AND COALESCE(r.times_triggered,0)=0
          AND NOT EXISTS (SELECT 1 FROM resource_sections s WHERE s.resource_id=r.id)
        """
    ).fetchone()[0]
    out: dict = {"before_cold_without_sections": before}
    if args.apply:
        out["backfill"] = backfill_cold_sections(
            con, limit=args.limit, db_path=str(db)
        )
    else:
        out["backfill"] = {"skipped": "dry_run — pass --apply"}
        out["note"] = "Will file-OKF when path resolves, else synthesize from body_text"
    con.close()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print("=== Gap 5 OKF residual ===")
        print(json.dumps(out, indent=2, default=str))
    bf = out.get("backfill") or {}
    if args.apply and not bf.get("okf_residual_gate_pass", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
