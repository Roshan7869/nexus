#!/usr/bin/env python3
"""
scripts/scan_duplicates.py
============================
Scans active resources for near-duplicate content pairs (cosine similarity > 0.90)
and populates the dedup_candidates table for manual review.

Runs a batch comparison using numpy matrix operations.
"""
import sqlite3
import numpy as np
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"

SIMILARITY_THRESHOLD = 0.90
BATCH_SIZE = 200  # compare in batches to avoid OOM


def scan_duplicates():
    print(f"Connecting to: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    resources = conn.execute("""
        SELECT id, name, embedding FROM resources
        WHERE status = 'active' AND embedding IS NOT NULL AND length(embedding) > 0
    """).fetchall()

    print(f"Loaded {len(resources)} active resources with embeddings")

    # Build matrix
    ids = []
    embeds = []
    for r in resources:
        arr = np.frombuffer(r["embedding"], dtype=np.float32)
        norm = arr / (np.linalg.norm(arr) + 1e-10)
        ids.append(r["id"])
        embeds.append(norm)

    # Group by embedding dimension (models may have different sizes)
    from collections import defaultdict
    dim_groups = defaultdict(list)
    for i, emb in enumerate(embeds):
        dim_groups[emb.shape[0]].append((i, ids[i], emb))

    # Clear existing pending candidates before rescan
    conn.execute("DELETE FROM dedup_candidates WHERE status = 'pending'")
    conn.commit()

    inserted = 0
    for dim, group in dim_groups.items():
        if len(group) < 2:
            continue
        print(f"  Comparing {len(group)} resources of dim={dim}...")
        group_ids = [g[1] for g in group]
        matrix = np.array([g[2] for g in group], dtype=np.float32)
        n = len(matrix)

        for start in range(0, n, BATCH_SIZE):
            batch = matrix[start:start + BATCH_SIZE]
            sims = batch @ matrix.T

            for bi, sim_row in enumerate(sims):
                global_i = start + bi
                for j in range(global_i + 1, n):
                    if sim_row[j] >= SIMILARITY_THRESHOLD:
                        try:
                            conn.execute("""
                                INSERT OR IGNORE INTO dedup_candidates
                                    (resource_id_a, resource_id_b, similarity_score, dedup_method, status)
                                VALUES (?, ?, ?, 'cosine', 'pending')
                            """, (group_ids[global_i], group_ids[j], float(sim_row[j])))
                            inserted += 1
                        except Exception:
                            pass

            conn.commit()

    conn.commit()
    conn.close()

    print(f"\nDuplicate scan complete:")
    print(f"  Near-duplicate pairs found: {inserted}")
    print(f"  All candidates marked 'pending' for review in dedup_candidates table")


if __name__ == "__main__":
    scan_duplicates()
