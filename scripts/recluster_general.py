#!/usr/bin/env python3
"""
scripts/recluster_general.py
==============================
Reassigns resources currently in the 'general' cluster to the nearest
named cluster centroid using cosine similarity on their stored embeddings.
Uses cluster_membership as the authoritative assignment table.

Run periodically to drain the general cluster bloat.
"""
import sqlite3
import numpy as np
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"

# Only reassign if similarity to a named cluster exceeds this threshold
REASSIGN_THRESHOLD = 0.38


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    a_norm = a / (np.linalg.norm(a) + 1e-10)
    b_norm = b / (np.linalg.norm(b) + 1e-10)
    return float(np.dot(a_norm, b_norm))


def recluster_general():
    print(f"Connecting to: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # Load named cluster centroids (exclude 'general')
    centroids = conn.execute("""
        SELECT name, centroid_embedding FROM cluster_definitions
        WHERE centroid_embedding IS NOT NULL AND name != 'general'
    """).fetchall()

    if not centroids:
        print("No named cluster centroids found. Run compute_cluster_centroids.py first.")
        conn.close()
        return

    centroid_data = []
    for c in centroids:
        arr = np.frombuffer(c["centroid_embedding"], dtype=np.float32)
        centroid_data.append((c["name"], arr))

    print(f"Loaded {len(centroid_data)} named cluster centroids")

    # Load general cluster resources with embeddings
    general_resources = conn.execute("""
        SELECT r.id, r.name, r.embedding
        FROM resources r
        JOIN cluster_membership cm ON cm.resource_id = r.id
        WHERE cm.cluster_name = 'general'
          AND r.status = 'active'
          AND r.embedding IS NOT NULL AND length(r.embedding) > 0
    """).fetchall()

    print(f"Found {len(general_resources)} resources in 'general' cluster to evaluate")

    reassigned = 0
    stayed = 0

    for resource in general_resources:
        rid = resource["id"]
        r_arr = np.frombuffer(resource["embedding"], dtype=np.float32)

        best_cluster = None
        best_score = REASSIGN_THRESHOLD  # must exceed threshold to reassign

        for cluster_name, c_arr in centroid_data:
            if r_arr.shape != c_arr.shape:
                continue  # skip dimension mismatch (different model embeddings)
            score = cosine_sim(r_arr, c_arr)
            if score > best_score:
                best_score = score
                best_cluster = cluster_name

        if best_cluster:
            # Update cluster_membership (triggers will sync resources.cluster)
            conn.execute("""
                UPDATE cluster_membership
                SET cluster_name = ?
                WHERE resource_id = ? AND cluster_name = 'general'
            """, (best_cluster, rid))
            reassigned += 1
        else:
            stayed += 1

    conn.commit()

    # Update cluster_definitions.resource_count for all affected clusters
    for cluster_name, _ in centroid_data:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = ?", (cluster_name,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE cluster_definitions SET resource_count = ? WHERE name = ?",
            (cnt, cluster_name)
        )
    # Update general count
    gen_cnt = conn.execute(
        "SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = 'general'"
    ).fetchone()[0]
    conn.execute(
        "UPDATE cluster_definitions SET resource_count = ? WHERE name = 'general'", (gen_cnt,)
    )
    conn.commit()

    conn.close()
    print(f"\nReclustering complete:")
    print(f"  Reassigned to named clusters: {reassigned}")
    print(f"  Remained in general: {stayed}")
    print(f"  General cluster size now: {gen_cnt}")


if __name__ == "__main__":
    recluster_general()
