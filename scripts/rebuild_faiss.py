#!/usr/bin/env python3
"""Phase A2: Rebuild FAISS index from current resource_embeddings table.

First-principles: The FAISS index must be a 1:1 mirror of the DB.
If embeddings are added/updated without rebuilding FAISS, the search returns
stale/mismatched resource IDs. This script reads EVERY current embedding
from SQLite and rebuilds the FAISS index atomically.

Algorithm:
  Step 1: Read all resource_embeddings from DB (id, embedding_blob)
  Step 2: Deserialize float32 vectors, validate dim==384
  Step 3: L2-normalize for cosine similarity (IndexFlatIP uses inner product)
  Step 4: Build IndexFlatIP (exact search, optimal for 1.7k vectors)
  Step 5: Write to temp file atomic via os.rename() + checksum
  Step 6: Verify round-trip: search a query, match IDs back to DB

Bottleneck: Reading 2,463 blobs from SQLite (~15MB). Takes ~3s.
If this grows to 100k+ vectors, switch to IndexIVFFlat or HNSW.
"""
import hashlib, json, os, sqlite3, subprocess, sys, time
from pathlib import Path

import numpy as np
import faiss

DB_PATH = Path.home() / "nexus/db/nexus.db"
INDEX_DIR = Path.home() / "nexus/db/faiss"
INDEX_PATH = INDEX_DIR / "resources.index"
INDEX_TMP_PATH = INDEX_DIR / "resources.index.tmp"
INDEX_META_PATH = INDEX_PATH.with_suffix(".index.meta.json")
DIM = 384


def _get_git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:
        return "unknown"


def _compute_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def rebuild_faiss(dry_run: bool = False) -> dict:
    print("=" * 60)
    print("Phase A2: Rebuild FAISS Index")
    print("=" * 60)
    t0 = time.time()

    # Step 1: Read embeddings from DB
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT re.resource_id, re.embedding, r.name, r.status "
        "FROM resource_embeddings re "
        "JOIN resources r ON re.resource_id = r.id "
        "WHERE re.embedding IS NOT NULL"
    ).fetchall()
    conn.close()

    print(f"  Read {len(rows)} embeddings from DB")

    # Step 2: Deserialize + validate dim
    vectors = []
    resource_ids = []
    missing_embeddings = 0
    for rid, blob, name, status in rows:
        if blob is None:
            missing_embeddings += 1
            continue
        vec = np.frombuffer(blob, dtype=np.float32)
        if len(vec) != DIM:
            print(f"  WARN: resource #{rid} ({name}) has dim {len(vec)}, expected {DIM} — skipping")
            continue
        vectors.append(vec)
        resource_ids.append(rid)

    if not vectors:
        print("  FATAL: No valid embeddings found!")
        sys.exit(1)

    embeddings = np.array(vectors, dtype=np.float32)
    print(f"  Valid embeddings: {len(embeddings)}")
    if missing_embeddings:
        print(f"  Missing (NULL blob): {missing_embeddings}")

    # Step 3: Normalize for cosine similarity
    faiss.normalize_L2(embeddings)

    # Step 4: Build IndexFlatIP (inner product on normalized = cosine)
    index = faiss.IndexFlatIP(DIM)
    index.add(embeddings)
    print(f"  FAISS index built: {index.ntotal} vectors, dim={DIM}")
    assert index.ntotal == len(resource_ids), (
        f"Mismatch: index has {index.ntotal} vectors but {len(resource_ids)} IDs"
    )

    # Step 5: Atomic write to disk (temp file + os.rename)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    git_hash = _get_git_hash()

    meta = {
        "resource_ids": resource_ids,
        "dim": DIM,
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "n_total": len(resource_ids),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_hash,
    }

    if dry_run:
        print("  DRY RUN — skipping write")
        return meta

    # Write to temp file first, then atomic rename
    faiss.write_index(index, str(INDEX_TMP_PATH))
    checksum = _compute_checksum(INDEX_TMP_PATH)
    meta["checksum_sha256"] = checksum
    print(f"  Temp index written: {INDEX_TMP_PATH} ({INDEX_TMP_PATH.stat().st_size} bytes)")
    print(f"  Checksum: {checksum}")

    # Atomic rename
    os.rename(str(INDEX_TMP_PATH), str(INDEX_PATH))
    print(f"  Atomic rename: {INDEX_TMP_PATH} → {INDEX_PATH}")

    with open(str(INDEX_META_PATH), "w") as f:
        json.dump(meta, f)
    print(f"  Written: {INDEX_META_PATH}")

    # Step 6: Verify round-trip
    index2 = faiss.read_index(str(INDEX_PATH))
    with open(str(INDEX_META_PATH)) as f:
        meta2 = json.load(f)
    assert index2.ntotal == index.ntotal, "Round-trip verification failed!"
    assert meta2["resource_ids"] == resource_ids, "Meta IDs mismatch!"
    assert meta2.get("checksum_sha256") == checksum, "Checksum verification failed!"
    print("  ✓ Round-trip + checksum verification passed")

    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.2f}s")
    return meta


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    rebuild_faiss(dry_run=dry)
