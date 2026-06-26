#!/usr/bin/env python3
"""Generate embeddings for all resources, using cleaned text (no YAML/HTML noise).

Fixes RC-6: embedding text was description + raw body_text[0:500], which included
YAML frontmatter and HTML comments. Now strips those before embedding.

Can run in two modes:
  --missing : only embed resources lacking embeddings (fast, incremental)
  --rebuild : re-embed ALL active resources with cleaned text (slow, thorough)
"""
import sys
import os
import re
import sqlite3
import time
import argparse
from pathlib import Path

sys.path.insert(0, os.path.expanduser('~/nexus/server'))
sys.path.insert(0, os.path.expanduser('~/nexus'))

DB_PATH = Path.home() / 'nexus/db/nexus.db'

# Regex patterns for cleaning embedding text
_YAML_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PREFIX_RE = re.compile(r"^[a-z0-9-]+\s+", re.IGNORECASE)


def clean_text(name: str, description: str, body_text: str) -> str:
    """Strip YAML frontmatter, HTML comments, and name prefixes from embedding text."""
    parts = []
    if description:
        desc = _HTML_COMMENT_RE.sub("", description).strip()
        if desc:
            parts.append(desc)

    if body_text:
        body = body_text[:500]
        body = _YAML_FRONTMATTER_RE.sub("", body)
        body = _HTML_COMMENT_RE.sub("", body)
        body = _PREFIX_RE.sub("", body)
        body = body.strip()
        if body:
            parts.append(body)

    if not parts:
        parts.append(name)

    return " ".join(parts)[:512]


def generate_embeddings(mode: str = "missing"):
    print("=" * 60)
    print(f"NEXUS Embedding Generation (mode={mode})")
    print("=" * 60)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    if mode == "rebuild":
        # Re-embed ALL active routable resources with cleaned text
        rows = conn.execute("""
            SELECT r.id, r.name, r.description, r.body_text
            FROM resources r
            WHERE r.status = 'active'
            AND r.resource_type IN ('skill', 'command', 'agent', 'mcp')
            AND r.name NOT LIKE 'chromadb-%'
        """).fetchall()
        print(f"Re-embedding ALL {len(rows)} routable resources with cleaned text")
    else:
        # Only embed resources missing embeddings
        rows = conn.execute("""
            SELECT r.id, r.name, r.description, r.body_text
            FROM resources r
            WHERE r.status = 'active'
            AND r.id NOT IN (SELECT DISTINCT resource_id FROM resource_embeddings)
        """).fetchall()
        print(f"Resources missing embeddings: {len(rows)}")

    if not rows:
        print("Nothing to do — all resources already embedded!")
        conn.close()
        return

    # Load sentence-transformers model
    print("Loading all-MiniLM-L6-v2 model...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')
    print("Model loaded.")

    # Generate embeddings in batches
    batch_size = 64
    total_embedded = 0

    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start:batch_start + batch_size]
        ids = [row[0] for row in batch]
        # Clean text before embedding — this is the RC-6 fix
        texts = [clean_text(row[1], row[2] or "", row[3] or "") for row in batch]

        embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)

        for i, (rid, emb_vec) in enumerate(zip(ids, embeddings)):
            import numpy as np
            emb_bytes = emb_vec.astype(np.float32).tobytes()
            conn.execute("""
                INSERT OR REPLACE INTO resource_embeddings (resource_id, embedding, model_name, created_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (rid, emb_bytes, 'all-MiniLM-L6-v2'))
            conn.execute("UPDATE resources SET embedding = ? WHERE id = ?", (emb_bytes, rid))

        total_embedded += len(batch)
        print(f"  Embedded {total_embedded}/{len(rows)} resources...")

    conn.commit()
    print(f"Total embeddings generated: {total_embedded}")

    # Verify
    final_missing = conn.execute("""
        SELECT COUNT(*) FROM resources r
        WHERE r.status = 'active'
        AND r.resource_type IN ('skill', 'command', 'agent', 'mcp')
        AND r.name NOT LIKE 'chromadb-%'
        AND r.id NOT IN (SELECT DISTINCT resource_id FROM resource_embeddings)
    """).fetchone()[0]
    print(f"Remaining missing routable embeddings: {final_missing}")

    conn.close()
    print("\nDone. NumpyRouter will auto-load from DB — no FAISS index needed.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="NEXUS embedding generation")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Re-embed ALL routable resources with cleaned text (fixes RC-6)"
    )
    parser.add_argument(
        "--missing", action="store_true",
        help="Only embed resources lacking embeddings (default)"
    )
    args = parser.parse_args()
    mode = "rebuild" if args.rebuild else "missing"
    generate_embeddings(mode=mode)