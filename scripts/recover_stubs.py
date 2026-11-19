#!/usr/bin/env python3
"""W4: recover stub resources from local clones.

363 active resources have < 300 chars of body_text — they are essentially
invisible to retrieval (no content to match). Their `file_path` values are the
ORIGINAL author's Linux paths (/home/roshan/...), so they cannot be opened
directly; but for repos cloned in this workspace the path suffix maps onto the
clone and the real file can be re-read.

This script:
  1. resolves each stub's path suffix against its local clone,
  2. reads the file (frontmatter-stripped body + description),
  3. updates resources.body_text/description/token_count,
  4. refreshes resources_fts + chunks (ingestion_chunks/chunks_fts) + embeddings.

Excluded per user decision: aashiyaana, kaari-weaves (no real-world meaning).

    python scripts/recover_stubs.py --dry-run
    python scripts/recover_stubs.py
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.db import get_db_path  # noqa: E402

WORKSPACE = ROOT.parent
EXCLUDED_REPOS = {"aashiyaana", "kaari-weaves"}
MAX_BODY = 20000


def strip_frontmatter(text: str) -> tuple[str, str]:
    """Return (description, body) with YAML frontmatter removed."""
    desc = ""
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        fm, body = m.group(1), text[m.end():]
        dm = re.search(r"^description:\s*(.+)$", fm, re.M)
        if dm:
            desc = dm.group(1).strip().strip("\"'")
    if not desc:
        # first non-heading, non-empty line as a fallback description
        for line in body.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "---", "```", "|")):
                desc = s[:300]
                break
    return desc, body.strip()


def resolve(repo: str, file_path: str) -> Path | None:
    base = WORKSPACE / repo
    if not base.is_dir():
        # case-insensitive directory match
        for d in WORKSPACE.iterdir():
            if d.is_dir() and d.name.lower() == repo.lower():
                base = d
                break
        else:
            return None
    parts = [p for p in file_path.replace("\\", "/").split("/") if p and p not in ("home", "roshan")]
    for i in range(min(6, len(parts))):
        cand = base.joinpath(*parts[i:])
        if cand.is_file():
            return cand
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    import sqlite3

    from server.iso_embedder import IsoEmbedder
    from server.router_v2.chunk_embedder import text_hash
    from server.router_v2.chunk_index import ChunkIndex

    db_path = Path(args.db or get_db_path())
    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row

    stubs = con.execute("""
        SELECT id, name, resource_type, repo_name, file_path, description
          FROM resources
         WHERE length(coalesce(body_text,'')) < 300 AND status='active'
         ORDER BY id""").fetchall()

    plan = []
    skipped = Counter()
    for r in stubs:
        repo = (r["repo_name"] or "").strip()
        if repo.lower() in EXCLUDED_REPOS:
            skipped["excluded repo"] += 1
            continue
        if not repo or not r["file_path"]:
            skipped["no repo/path"] += 1
            continue
        p = resolve(repo, r["file_path"])
        if p is None:
            skipped["file not found"] += 1
            continue
        plan.append((r, p))

    if args.limit:
        plan = plan[:args.limit]

    print(f"db            : {db_path}")
    print(f"stubs         : {len(stubs)}")
    print(f"recoverable   : {len(plan)}")
    for why, n in skipped.most_common():
        print(f"  skipped {n:4}  ({why})")

    if args.dry_run:
        for r, p in plan[:8]:
            d, b = strip_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            print(f"\n  {r['name'][:34]:34} <- {p.name}  ({p.stat().st_size}B)")
            print(f"    desc: {d[:90]}")
            print(f"    body: {b[:110]!r}")
        con.close()
        return 0

    embedder = IsoEmbedder.get(str(db_path))
    embedder.load_model()
    idx = ChunkIndex(db_path)
    print(f"backend       : {type(embedder.model).__name__}")

    # ensure a source row for recovered chunks
    row = con.execute(
        "SELECT id FROM ingestion_sources WHERE source_type='stub_recovery' LIMIT 1").fetchone()
    if row:
        rec_src = int(row["id"])
    else:
        cur = con.execute(
            """INSERT INTO ingestion_sources(source_type, source_path, content_hash,
                   status, created_at, updated_at)
               VALUES('stub_recovery','(W4 stub recovery)',?,'active',
                      datetime('now'), datetime('now'))""",
            (text_hash("w4-stub-recovery"),))
        rec_src = int(cur.lastrowid)
        con.commit()

    updated = 0
    texts, pairs = [], []
    for r, p in plan:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        desc, body = strip_frontmatter(raw)
        if len(body) < 40:
            continue
        body = body[:MAX_BODY]
        con.execute(
            """UPDATE resources
                  SET body_text=?, description=CASE WHEN coalesce(description,'')=''
                                                    THEN ? ELSE description END,
                      token_count=?, updated_at=datetime('now')
                WHERE id=?""",
            (body, desc, len(body) // 4, r["id"]))
        try:
            con.execute(
                "INSERT INTO resources_fts(rowid, name, description, body_text, "
                "resource_type, cluster, status) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(rowid) DO UPDATE SET body_text=excluded.body_text",
                (r["id"], r["name"], desc, body, r["resource_type"], None, "active"))
        except sqlite3.Error:
            pass
        # refresh the chunk lane for this resource
        con.execute(
            """INSERT INTO ingestion_chunks(source_id, resource_id, chunk_index,
                   chunk_type, heading, content, content_hash, token_count)
               VALUES(?,?,0,'section',?,?,?,?)""",
            (rec_src, r["id"], r["name"], f"{r['name']} — {desc}\n{body}"[:6000],
             text_hash(body), len(body) // 4))
        cid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        try:
            con.execute(
                "INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
                "chunk_type, source_id) VALUES(?,?,?,?,?,?)",
                (cid, f"{r['name']} — {desc}\n{body}"[:6000], r["name"],
                 r["id"], r["resource_type"], rec_src))
        except sqlite3.Error:
            pass
        texts.append(f"{r['name']} — {desc}\n{body}"[:6000])
        pairs.append((cid, r["id"]))
        updated += 1
        if updated % 200 == 0:
            con.commit()
    con.commit()

    # embed the new chunks in batches
    if texts:
        vecs = embedder.embed_texts(texts)
        for (cid, _rid), v in zip(pairs, vecs):
            con.execute(
                "INSERT OR REPLACE INTO chunk_embeddings"
                "(chunk_id, text_hash, embedding, resource_id, created_at) "
                "VALUES (?,?,?,?,datetime('now'))",
                (cid, text_hash(""), v.astype("float32").tobytes(), _rid))
        con.commit()
        idx.add_many([(cid, v) for (cid, _), v in zip(pairs, vecs)])

    stubs_left = con.execute(
        "SELECT count(*) FROM resources WHERE length(coalesce(body_text,''))<300 "
        "AND status='active'").fetchone()[0]
    con.close()
    idx.con.close()
    print(f"\nrecovered     : {updated} resources")
    print(f"stubs left    : {stubs_left} (was {len(stubs)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
