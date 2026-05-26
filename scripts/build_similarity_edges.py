#!/usr/bin/env python3
"""
scripts/build_similarity_edges.py
====================================
Uses batch cosine similarity (numpy matrix multiplication) to build
'similar_to' edges in resource_edges for resource pairs exceeding 0.82
similarity. Deduplicates against existing edges.

Recommended to run after FAISS index rebuild.
"""
import sqlite3
import numpy as np
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"

SIMILARITY_THRESHOLD = 0.82
MAX_EDGES_PER_RESOURCE = 5  # limit fan-out per resource
BATCH_SIZE = 200


def build_similarity_edges():
    print(f"Connecting to: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    resources = conn.execute("""
        SELECT id, name, embedding, cluster FROM resources
        WHERE status = 'active' AND embedding IS NOT NULL AND length(embedding) > 0
    """).fetchall()

    print(f"Loaded {len(resources)} active resources with embeddings")

    ids = []
    embeds = []
    for r in resources:
        arr = np.frombuffer(r["embedding"], dtype=np.float32)
        norm = arr / (np.linalg.norm(arr) + 1e-10)
        ids.append(r["id"])
        embeds.append(norm)

    # Group by embedding dimension
    from collections import defaultdict
    dim_groups = defaultdict(list)
    for i, emb in enumerate(embeds):
        dim_groups[emb.shape[0]].append((i, ids[i], emb))

    # Load existing similar_to edges to avoid duplicates
    existing = set()
    for row in conn.execute(
        "SELECT source_id, target_id FROM resource_edges WHERE edge_type='similar_to'"
    ).fetchall():
        existing.add((row["source_id"], row["target_id"]))
        existing.add((row["target_id"], row["source_id"]))

    inserted = 0
    skipped_existing = 0

    for dim, group in dim_groups.items():
        if len(group) < 2:
            continue
        print(f"  Processing dim={dim} group ({len(group)} resources)...")
        group_ids = [g[1] for g in group]
        matrix = np.array([g[2] for g in group], dtype=np.float32)
        n = len(matrix)

        for start in range(0, n, BATCH_SIZE):
            batch = matrix[start:start + BATCH_SIZE]
            sims = batch @ matrix.T

            for bi, sim_row in enumerate(sims):
                global_i = start + bi
                sim_row[global_i] = -1.0
                top_idxs = np.argsort(sim_row)[::-1]

                count = 0
                for j in top_idxs:
                    if count >= MAX_EDGES_PER_RESOURCE:
                        break
                    if j == global_i:
                        continue
                    sim = float(sim_row[j])
                    if sim < SIMILARITY_THRESHOLD:
                        break
                    src, tgt = group_ids[global_i], group_ids[j]
                    if (src, tgt) in existing:
                        skipped_existing += 1
                        count += 1
                        continue
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO resource_edges
                                (source_id, target_id, edge_type, weight, co_trigger_count)
                            VALUES (?, ?, 'similar_to', ?, 0)
                        """, (src, tgt, round(sim, 4)))
                        existing.add((src, tgt))
                        existing.add((tgt, src))
                        inserted += 1
                        count += 1
                    except Exception:
                        pass

            conn.commit()

    conn.commit()
    total_edges = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
    conn.close()

    print(f"\nSimilarity edge build complete:")
    print(f"  New similar_to edges inserted: {inserted}")
    print(f"  Skipped (already existing): {skipped_existing}")
    print(f"  Total resource_edges now: {total_edges}")


if __name__ == "__main__":
    build_similarity_edges()
