#!/usr/bin/env python3
"""Report JEV teacher status: spend, label corpus, agreement, and bake-in readiness.

This is the meter for the "limited time" arrangement — JEV is meant to run only until the
local classifier has enough labels, and that decision needs numbers, not a feeling.

    python scripts/jev_status.py
    python scripts/jev_status.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402
from server.jev_gate import ensure_schema, mode  # noqa: E402

#: Bake-in gate from the execution plan: 3 labels per class, >=20 distinct tasks,
#: holdout agreement >=85% before JEV may be demoted to `verify`.
MIN_TASKS = 20
MIN_PER_CLASS = 3
MIN_AGREEMENT = 0.85


def collect(db_path: str) -> dict:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        ensure_schema(db_path)
        cap = int(os.environ.get("NEXUS_JEV_BUDGET", "40") or 40)
        day = time.strftime("%Y-%m-%d")
        row = con.execute("SELECT calls FROM jev_budget WHERE day=?", (day,)).fetchone()
        used = row["calls"] if row else 0

        by_question: dict[str, dict] = {}
        for r in con.execute(
            """SELECT question,
                      count(*)                                   AS labels,
                      count(DISTINCT task_hash)                  AS tasks,
                      sum(CASE WHEN agreed = 1 THEN 1 ELSE 0 END) AS agree,
                      sum(CASE WHEN agreed = 0 THEN 1 ELSE 0 END) AS disagree,
                      sum(CASE WHEN agreed IS NULL THEN 1 ELSE 0 END) AS unjudged
               FROM jev_labels GROUP BY question"""
        ):
            judged = (r["agree"] or 0) + (r["disagree"] or 0)
            by_question[r["question"]] = {
                "labels": r["labels"],
                "tasks": r["tasks"],
                "agree": r["agree"] or 0,
                "disagree": r["disagree"] or 0,
                "unjudged": r["unjudged"] or 0,
                "agreement": round((r["agree"] or 0) / judged, 3) if judged else None,
            }

        classes: dict[str, dict[str, int]] = {}
        for r in con.execute(
            "SELECT question, label, count(*) AS n FROM jev_labels "
            "WHERE label IS NOT NULL GROUP BY question, label"
        ):
            classes.setdefault(r["question"], {})[r["label"]] = r["n"]

        thin = {
            q: [c for c, n in counts.items() if n < MIN_PER_CLASS]
            for q, counts in classes.items()
        }
        return {
            "mode": mode(),
            "budget": {"cap": cap, "used_today": used, "remaining": max(0, cap - used)},
            "by_question": by_question,
            "class_counts": classes,
            "under_sampled_classes": thin,
            "total_labels": con.execute("SELECT count(*) FROM jev_labels").fetchone()[0],
        }
    finally:
        con.close()


def verdict(stats: dict) -> list[str]:
    out = []
    for question, d in stats["by_question"].items():
        if d["tasks"] < MIN_TASKS:
            out.append(f"{question}: {d['tasks']}/{MIN_TASKS} distinct tasks — keep labelling")
        elif stats["under_sampled_classes"].get(question):
            miss = stats["under_sampled_classes"][question]
            out.append(f"{question}: classes under {MIN_PER_CLASS} labels: {', '.join(miss[:6])}")
        elif d["agreement"] is None:
            out.append(f"{question}: {d['labels']} labels but none judged — run enforce/verify")
        elif d["agreement"] < MIN_AGREEMENT:
            out.append(f"{question}: agreement {d['agreement']*100:.0f}% < "
                       f"{MIN_AGREEMENT*100:.0f}% — local model not ready")
        else:
            out.append(f"{question}: READY (agreement {d['agreement']*100:.0f}%, "
                       f"{d['tasks']} tasks)")
    if not out:
        out.append("no labels yet — JEV has never been consulted")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    stats = collect(str(Path(args.db or get_db_path())))
    if args.json:
        print(json.dumps(stats, indent=2))
        return 0

    b = stats["budget"]
    print(f"JEV mode        : {stats['mode']}")
    print(f"Budget today    : {b['used_today']}/{b['cap']} used, {b['remaining']} left")
    print(f"Labels recorded : {stats['total_labels']}\n")
    if stats["by_question"]:
        print(f"{'question':14} {'labels':>7} {'tasks':>6} {'agree':>6} {'disagree':>9} {'agr%':>6}")
        for q, d in sorted(stats["by_question"].items()):
            agr = f"{d['agreement']*100:.0f}" if d["agreement"] is not None else "-"
            print(f"{q:14} {d['labels']:>7} {d['tasks']:>6} {d['agree']:>6} {d['disagree']:>9} {agr:>6}")
    print("\nBake-in readiness:")
    for line in verdict(stats):
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
