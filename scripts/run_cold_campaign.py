#!/usr/bin/env python3
"""Gap 1 cold inventory campaign.

Dry-run (default):
  python scripts/run_cold_campaign.py

Apply + FAISS rebuild:
  python scripts/run_cold_campaign.py --apply

Optional OKF section backfill for remaining cold-without-sections:
  python scripts/run_cold_campaign.py --apply --okf-backfill
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.catalog_lifecycle import CatalogLifecycle  # noqa: E402


def _okf_backfill_cold(limit: int | None = None) -> dict:
    """Backfill resource_sections for active never-triggered without sections."""
    import sqlite3
    from server.ingest.okf_ingest import ingest_okf

    db = ROOT / "db" / "nexus.db"
    con = sqlite3.connect(str(db), timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    rows = con.execute(
        """
        SELECT r.id, r.name, r.file_path FROM resources r
        WHERE r.status='active' AND COALESCE(r.times_triggered,0)=0
          AND r.file_path IS NOT NULL AND r.file_path != ''
          AND NOT EXISTS (
            SELECT 1 FROM resource_sections rs WHERE rs.resource_id=r.id
          )
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]

    def resolve(fp: str) -> str | None:
        p = Path(fp)
        if p.is_file():
            return str(p.resolve())
        if p.is_dir() and (p / "SKILL.md").is_file():
            return str((p / "SKILL.md").resolve())
        # try home-relative
        home = Path.home()
        for base in (home, ROOT, home / ".claude", home / ".agents"):
            cand = base / fp if not Path(fp).is_absolute() else Path(fp)
            if cand.is_file():
                return str(cand.resolve())
            if cand.is_dir() and (cand / "SKILL.md").is_file():
                return str((cand / "SKILL.md").resolve())
        return None

    ok = fail = skip = sections = 0
    for r in rows:
        resolved = resolve(r["file_path"])
        if not resolved:
            skip += 1
            continue
        try:
            ingest_okf(r["id"], resolved, db_path=str(db))
            sid = con.execute(
                "SELECT id FROM ingestion_sources WHERE source_path=? AND source_type='okf'",
                (resolved,),
            ).fetchone()
            if not sid:
                ok += 1
                continue
            chunks = con.execute(
                "SELECT chunk_index, heading, content, token_count "
                "FROM ingestion_chunks WHERE source_id=? ORDER BY chunk_index",
                (sid["id"],),
            ).fetchall()
            con.execute("DELETE FROM resource_sections WHERE resource_id=?", (r["id"],))
            for ch in chunks:
                con.execute(
                    "INSERT INTO resource_sections"
                    "(resource_id, section_order, depth, heading, content, token_count) "
                    "VALUES (?,?,0,?,?,?)",
                    (r["id"], ch["chunk_index"], ch["heading"], ch["content"], ch["token_count"]),
                )
            con.commit()
            sections += len(chunks)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  OKF FAIL {r['name']}: {e}")
    con.close()
    return {
        "candidates": len(rows),
        "succeeded": ok,
        "failed": fail,
        "skipped_no_file": skip,
        "sections_written": sections,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Gap 1 cold inventory campaign")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--no-faiss", action="store_true")
    p.add_argument("--campaign-days", type=int, default=None)
    p.add_argument(
        "--aggressive",
        action="store_true",
        help="Archive all never-in-routing_log invoke types older than campaign-days "
        "(ignore token/bulk name filters)",
    )
    p.add_argument("--okf-backfill", action="store_true")
    p.add_argument("--okf-limit", type=int, default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    lc = CatalogLifecycle()
    report = lc.run_gap1_campaign(
        dry_run=not args.apply,
        campaign_days=args.campaign_days,
        rebuild_faiss=args.apply and not args.no_faiss,
        aggressive=args.aggressive,
    )
    out = report.to_dict()
    if args.okf_backfill and args.apply:
        out["okf_backfill"] = _okf_backfill_cold(limit=args.okf_limit)
    elif args.okf_backfill and not args.apply:
        out["okf_backfill"] = {"skipped": "dry_run — pass --apply"}

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print("=== Gap 1 Cold Campaign ===")
        print(f"dry_run={report.dry_run}")
        print("before:", report.metrics_before)
        print(
            f"archived: ref_cold={report.archived_reference_cold} "
            f"campaign={report.archived_campaign}"
        )
        print("after:", report.metrics_after)
        m = report.metrics_after or {}
        print(
            f"all_active_cold={m.get('never_triggered_pct')}%  "
            f"invoke_cold={m.get('invoke_never_triggered_pct')}% "
            f"(target ≤{m.get('invoke_cold_target_pct')}%) "
            f"gate={m.get('invoke_cold_gate_pass')}"
        )
        if "okf_backfill" in out:
            print("okf_backfill:", out["okf_backfill"])
        if report.errors:
            print("ERRORS:", report.errors)
            return 1
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
