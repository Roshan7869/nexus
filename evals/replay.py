#!/usr/bin/env python3
"""Replay recorded routing_log traffic through the CURRENT router (offline A/B).

Reads stored (task_summary → routed skills) rows and re-routes each with
route_for_task(open_session=False); reports the agreement matrix. Enables
router experiments without new traffic. No provider calls.

Usage: python evals/replay.py [--limit 25] [--project NAME] [--out TAG]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def load_rows(limit: int, project: str | None):
    from server.db import get_connection
    conn = get_connection()
    try:
        q = "SELECT id, task_summary, project, matched_skills FROM routing_log"
        params: list = []
        if project:
            q += " WHERE project=?"
            params.append(project)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            try:
                skills = json.loads(r["matched_skills"] or "[]")
            except json.JSONDecodeError:
                skills = []
            if not isinstance(skills, list):
                skills = []
            out.append({"id": r["id"], "prompt": r["task_summary"],
                        "project": r["project"], "stored_skills": skills})
        return out
    finally:
        conn.close()


def replay(limit: int, project: str | None, tag: str) -> dict:
    from server.router_cutover import route_for_task
    rows = load_rows(limit, project)
    results = []
    skipped_no_gt = 0
    for row in rows:
        if not row["prompt"]:
            continue
        d = route_for_task(row["prompt"], row["project"], open_session=False)
        new_top = [s.get("name") for s in (d.get("skills") or [])][:5]
        stored_top = [s for s in row["stored_skills"] if isinstance(s, str)][:5]
        if not stored_top:
            # empty matched_skills in the log = no ground truth to compare
            skipped_no_gt += 1
            continue
        overlap = len(set(new_top) & set(stored_top))
        denom = max(len(set(stored_top)), 1)
        results.append({
            "routing_log_id": row["id"], "prompt": row["prompt"][:80],
            "stored_top": stored_top, "new_top": new_top,
            "overlap": overlap, "of": denom,
            "top1_match": bool(new_top and stored_top and new_top[0] == stored_top[0]),
        })

    n = len(results) or 1
    summary = {
        "replayed": len(results),
        "skipped_no_ground_truth": skipped_no_gt,
        "avg_overlap": round(sum(r["overlap"] / r["of"] for r in results) / n, 4),
        "top1_match_rate": round(sum(r["top1_match"] for r in results) / n, 4),
        "tag": tag,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    out = REPO_ROOT / "evals" / "results" / f'replay_{summary["ts"].replace(":", "").replace("-", "")}_{tag}.jsonl'
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        f.write(json.dumps(summary) + "\n")
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"replayed={summary['replayed']} avg_overlap={summary['avg_overlap']} "
          f"top1_match={summary['top1_match_rate']}")
    print(f"archived: {out.relative_to(REPO_ROOT)}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--project", default=None)
    ap.add_argument("--tag", default="dev")
    a = ap.parse_args()
    replay(a.limit, a.project, a.tag)
