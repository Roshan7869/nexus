#!/usr/bin/env python3
"""Sync NEXUS resources into Wenlan so they are *recallable*.

Why this exists
---------------
The previous mirror used `write_page` only. Measured behaviour: a `write_page`
page is invisible to `recall` (recall searches memories, not authored pages), while
a `capture` memory is found immediately (probe: score 0.358). That is exactly why
Wenlan "had 1,070 pages and 0 recall hits" â€” the mirror wrote the wrong primitive.

This script writes **memories** via `capture`, which is what Wenlan's recall,
distillation and cross-tool sharing actually read. Pages are still written for the
human-readable wiki view, but they are no longer the only thing that exists.

Incremental
-----------
State lives in ``NEXUS_WENLAN_STATE`` (default ``D:\\nexus-data\\wenlan_sync.json``)
mapping resource name -> content hash. Re-runs push only new or changed resources,
so a full sync is a one-off and a refresh is cheap.

    python scripts/sync_wenlan.py --limit 20 --dry-run
    python scripts/sync_wenlan.py --limit 100
    python scripts/sync_wenlan.py                 # everything outstanding
    python scripts/sync_wenlan.py --types skill --reset-state
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

STATE_FILE = Path(os.environ.get("NEXUS_WENLAN_STATE", r"D:\nexus-data\wenlan_sync.json"))
MAX_CONTENT = 1200


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=0), encoding="utf-8")
    tmp.replace(STATE_FILE)


def content_hash(r: dict) -> str:
    h = hashlib.sha256()
    h.update((r["description"] or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((r["body_text"] or "")[:4000].encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def memory_text(r: dict) -> str:
    """One durable statement per resource â€” Wenlan rejects near-duplicates (HTTP 422).

    The description alone is not enough: when a resource's *body* is enriched but its
    one-line description stays the same, the generated memory is byte-identical to the
    earlier capture and Wenlan refuses it as a duplicate. Including a real content
    excerpt both fixes that and makes the memory worth recalling.
    """
    desc = (r["description"] or "").strip().replace("\n", " ")
    body = (r.get("body_text") or "").strip()
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{2,}", "\n", body)

    head = (f"NEXUS {r['resource_type']} resource '{r['name']}' "
            f"(repo {r['repo_name'] or 'unknown'}, cluster {r['cluster'] or 'unassigned'}")
    if desc:
        head += f": {desc[:600]}"
    head += "."

    if body and len(body) >= 120:
        excerpt = body[: MAX_CONTENT - len(head) - 40]
        head += f" Content: {excerpt}"
    return head[:MAX_CONTENT + 200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--types", default=None, help="comma list, e.g. skill,agent")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset-state", action="store_true")
    ap.add_argument("--pages", action="store_true", help="also write wiki pages")
    ap.add_argument("--sleep", type=float, default=0.08,
                    help="pause between captures (the daemon crashed under a sustained flood)")
    ap.add_argument("--retries", type=int, default=2,
                    help="retry a capture this many times when the daemon is unreachable")
    args = ap.parse_args()

    db = Path(args.db or get_db_path())
    con = sqlite3.connect(str(db), timeout=60)
    con.row_factory = sqlite3.Row
    sql = ("SELECT name, resource_type, description, body_text, repo_name, cluster "
           "FROM resources WHERE status='active' ORDER BY resource_type, name")
    params: list = []
    if args.types:
        types = [t.strip() for t in args.types.split(",") if t.strip()]
        sql = sql.replace("ORDER BY", "AND resource_type IN (" + ",".join("?" * len(types)) +
                          ") ORDER BY")
        params = types
    rows = [dict(r) for r in con.execute(sql, params)]
    con.close()

    state = {} if args.reset_state else load_state()
    pending = []
    for r in rows:
        h = content_hash(r)
        if state.get(r["name"]) == h:
            continue
        pending.append((r, h))
    if args.limit:
        pending = pending[:args.limit]

    print(f"db        : {db}")
    print(f"resources : {len(rows)}   already synced: {len(state)}   pending: {len(pending)}")
    if not pending:
        print("nothing to sync")
        return 0

    if args.dry_run:
        for r, _ in pending[:5]:
            print(f"  would capture: {memory_text(r)[:110]}")
        print(f"  ... {len(pending)} total")
        return 0

    from server.integrations.wenlan_mcp import WenlanMCP

    captured = failed = rejected = 0
    t0 = time.perf_counter()
    with WenlanMCP(timeout=90) as w:
        for i, (r, h) in enumerate(pending, 1):
            ok = False
            for attempt in range(max(1, args.retries + 1)):
                try:
                    res = w.capture(
                        content=memory_text(r),
                        memory_type="fact",
                        entity="NEXUS",
                        entity_type="project",
                    )
                    text = str(res)
                    if "Stored" in text or "mem_" in text:
                        state[r["name"]] = h
                        captured += 1
                        ok = True
                    else:
                        rejected += 1
                        print(f"  [rejected] {r['name']}: {text[:110]}")
                        ok = True
                    break
                except Exception as exc:
                    msg = str(exc)
                    # A dead MCP child surfaces as a broken pipe, not as "unreachable":
                    # Windows raises OSError 22 (Invalid argument) and the client
                    # reports "closed the stream". Both mean "restart the client",
                    # and previously neither was retried — one child death cascaded
                    # into 433 consecutive failures.
                    retryable = any(k in msg for k in (
                        "not reachable", "timed out", "closed the stream",
                        "Invalid argument", "Broken pipe", "OSError"))
                    if retryable and attempt < args.retries:
                        time.sleep(3 + attempt * 3)
                        try:
                            w.close()
                        except Exception:
                            pass
                        try:
                            w.start()
                        except Exception:
                            pass
                        continue
                    failed += 1
                    print(f"  [failed] {r['name']}: {type(exc).__name__}: {msg[:110]}")
                    break
            if args.pages and ok:
                try:
                    w.write_page(title=f"NEXUS {r['resource_type']}: {r['name']}",
                                 content=f"{r['description'] or ''}\n\n{(r['body_text'] or '')[:4000]}")
                except Exception:
                    pass
            if args.sleep:
                time.sleep(args.sleep)
            if i % 25 == 0:
                elapsed = time.perf_counter() - t0
                save_state(state)
                print(f"  {i}/{len(pending)} captured={captured} failed={failed} "
                      f"rejected={rejected} ({elapsed / i * 1000:.0f} ms/each)")

    save_state(state)
    elapsed = time.perf_counter() - t0
    print(f"\ncaptured {captured} | rejected {rejected} | failed {failed} "
          f"| {elapsed:.1f}s ({elapsed / max(1, len(pending)) * 1000:.0f} ms each)")
    print(f"state: {STATE_FILE} ({len(state)} synced)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
