"""P6: Cold-start warming — surface never-triggered resources via the cold tier.

The unified router's two-tier split (Paper 3: ToolkenGPT) reserves 30% of
every result set for never-triggered resources (times_triggered == 0), ranked
by UCB1 + semantic similarity. UCB1 gives a 1.0 exploration bonus to
never-triggered resources (Paper 9: Bandit Tool Selection, arXiv:2406.09121),
so they surface alongside proven resources instead of being buried.

This script is a PROACTIVE exploration pass: it builds query seeds from the
never-triggered resources' own names/descriptions and runs each through
``router_v2.unified_route``, reporting which never-triggered resources get
surfaced in the cold tier. It is READ-ONLY by default — it does NOT write
outcomes (fake triggers would inflate Thompson beliefs; the
``_auto_close_routed`` heuristic was disabled for exactly that reason).

With ``--commit`` it writes a single ``routing_log`` row with
``outcome='routed'`` (intermediate state, not feedback) per surfaced cold
resource, recording first exposure so the never-triggered metric can be
tracked. Real outcome feedback must still come from ``record_outcome`` when
the resource is actually used.

GATING NOTE: the 66.6% never-triggered metric (1876/2815) only moves once
``USE_UNIFIED_ROUTER=1`` is flipped live on the MCP AND real queries flow
through the two-tier cold path. This script previews the warming that will
happen organically — it does not shortcut it.

Verification target (plan Phase 6): >50% of never-triggered resources get
≥1 trigger within 7 days of the flag going live.

Usage:
  python3 scripts/cold_warm.py [--sample 200] [--top-k 10] [--commit]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

DB = ROOT / "db" / "nexus.db"


def _never_triggered(conn: sqlite3.Connection, limit: int | None) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, cluster, description
           FROM resources
           WHERE (times_triggered = 0 OR times_triggered IS NULL)
             AND name IS NOT NULL AND length(name) >= 3
           ORDER BY id LIMIT ?""",
        (limit or -1,),
    ).fetchall()
    return [dict(r) for r in rows]


def _seed_query(row: dict) -> str:
    """Build a query seed from a resource's name + description (first ~8 words)."""
    name = (row.get("name") or "").replace("-", " ").replace("_", " ")
    desc = (row.get("description") or "").strip()
    # drop long slug suffixes (e.g. 'ruflo-skill-ADR-055-...')
    short = name.split()[:4]
    if desc:
        short += desc.split()[:6]
    return " ".join(short)[:120]


def main() -> int:
    ap = argparse.ArgumentParser(description="Cold-start warming preview.")
    ap.add_argument("--sample", type=int, default=200,
                    help="max never-triggered resources to seed from (default 200)")
    ap.add_argument("--top-k", type=int, default=10,
                    help="top_k per unified_route call (default 10)")
    ap.add_argument("--commit", action="store_true",
                    help="write routing_log 'routed' rows for surfaced cold resources")
    args = ap.parse_args()

    from router_v2 import unified_route

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row

    nt_total = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE times_triggered = 0 OR times_triggered IS NULL"
    ).fetchone()[0]
    seeds = _never_triggered(conn, args.sample)
    print(f"never-triggered total: {nt_total} ({100*nt_total/2815:.1f}%)")
    print(f"seeding from {len(seeds)} never-triggered resources (sample={args.sample})")

    surfaced: dict[int, dict] = {}   # resource_id -> {name, cluster, seed, rrf}
    t0 = time.time()
    for i, row in enumerate(seeds, start=1):
        q = _seed_query(row)
        if not q:
            continue
        try:
            out = unified_route(q, top_k=args.top_k, cold_fraction=0.3, vec=False)
        except Exception as e:
            print(f"  [{i}/{len(seeds)}] route failed: {e}")
            continue
        # cold tier entries are never-triggered resources surfaced this pass
        for x in out.get("cold", []):
            rid = x.get("resource_id")
            if rid is None:
                continue
            if rid not in surfaced:
                surfaced[rid] = {
                    "name": x.get("name"),
                    "cluster": x.get("cluster"),
                    "seed": q[:60],
                    "rrf_score": x.get("rrf_score", 0.0),
                }
        if i % 50 == 0:
            print(f"  [{i}/{len(seeds)}] surfaced-so-far: {len(surfaced)} ({time.time()-t0:.1f}s)")

    print(f"\nsurfaced {len(surfaced)} unique never-triggered resources "
          f"in the cold tier ({100*len(surfaced)/max(nt_total,1):.1f}% of {nt_total})")
    for rid, info in list(surfaced.items())[:10]:
        print(f"  rid={rid:5d} {str(info['name']):40s} cluster={info['cluster']} "
              f"rrf={info['rrf_score']:.4f} seed={info['seed']!r}")

    coverage_pct = 100 * len(surfaced) / max(nt_total, 1)
    print(f"\nCOVERAGE PREVIEW: {len(surfaced)}/{nt_total} ({coverage_pct:.1f}%)")

    if args.commit and surfaced:
        written = 0
        for rid, info in surfaced.items():
            conn.execute(
                """INSERT INTO routing_log
                   (task_summary, project, matched_tier, matched_model,
                    matched_skills, outcome, skill_name, ide)
                   VALUES (?, 'cold-warm', 2, 'sonnet', ?, 'routed', ?, 'mcp')""",
                (info["seed"], json.dumps([info["name"]]), info["name"]),
            )
            written += 1
        conn.commit()
        print(f"COMMITTED: {written} 'routed' rows (outcome=NULL — real feedback via record_outcome)")
    else:
        print("read-only (pass --commit to write 'routed' exposure rows)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())