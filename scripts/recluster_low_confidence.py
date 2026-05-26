#!/usr/bin/env python3
"""Phase B2: Re-cluster resources with cluster_confidence < 0.5.

First-principles: 382 resources have low-confidence cluster assignments.
This means their semantic embedding doesn't match their cluster centroid well.
They're in the wrong bucket, causing noise when cluster routing narrows search.

Algorithm:
  Step 1: Load all 8 cluster centroids from cluster_definitions
  Step 2: Find resources with cluster_confidence < 0.5 (or unassigned)
  Step 3: For each, compute cosine similarity to ALL 8 centroids
  Step 4: Re-assign to centroid with highest similarity
  Step 5: Update cluster_confidence = max_cosine_similarity
  Step 6: If max similarity < 0.3, mark as 'general' (true orphan)
  Step 7: Report: how many were re-assigned, confidence improvement

Bottleneck: 382 resources × 8 centroids = 3,056 dot products. ~0.01s.
"""
import sqlite3, json, time, sys
from pathlib import Path

import numpy as np

DB_PATH = Path.home() / "nexus/db/nexus.db"
DIM = 384
MIN_CONFIDENCE = 0.5
ORPHAN_THRESHOLD = 0.3


def recluster(dry_run: bool = False) -> dict:
    print("=" * 60)
    print("Phase B2: Re-cluster Low-Confidence Resources")
    print("=" * 60)
    t0 = time.time()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Step 1: Load centroids
    centroid_rows = conn.execute(
        "SELECT name, centroid_embedding FROM cluster_definitions WHERE centroid_embedding IS NOT NULL"
    ).fetchall()

    centroids = {}
    for row in centroid_rows:
        vec = np.frombuffer(row["centroid_embedding"], dtype=np.float32)
        if len(vec) == DIM:
            centroids[row["name"]] = vec

    print(f"  Loaded {len(centroids)} cluster centroids")

    # Step 2: Find low-confidence resources
    low_conf = conn.execute(
        "SELECT r.id, r.name, r.cluster, r.cluster_confidence, re.embedding "
        "FROM resources r "
        "JOIN resource_embeddings re ON re.resource_id = r.id "
        "WHERE r.status = 'active' "
        "  AND (r.cluster_confidence < ? OR r.cluster_confidence IS NULL OR r.cluster_confidence = 0)",
        (MIN_CONFIDENCE,),
    ).fetchall()

    print(f"  Found {len(low_conf)} low-confidence resources")

    changes = []
    re_cluster_map = {}  # cluster_name -> count
    to_general = 0
    confidence_before = []
    confidence_after = []

    for row in low_conf:
        rid = row["id"]
        name = row["name"]
        old_cluster = row["cluster"] or "general"
        old_conf = row["cluster_confidence"] or 0.0
        emb_blob = row["embedding"]

        if emb_blob is None:
            continue

        vec = np.frombuffer(emb_blob, dtype=np.float32)
        if len(vec) != DIM:
            continue

        vec_norm = vec / (np.linalg.norm(vec) + 1e-10)

        # Step 3: Compute similarity to all centroids
        best_cluster = "general"
        best_score = -1.0
        scores = {}
        for cname, cvec in centroids.items():
            cnorm = cvec / (np.linalg.norm(cvec) + 1e-10)
            sim = float(np.dot(vec_norm, cnorm))
            scores[cname] = sim
            if sim > best_score:
                best_score = sim
                best_cluster = cname

        # Step 6: Orphan threshold
        if best_score < ORPHAN_THRESHOLD:
            best_cluster = "general"
            to_general += 1

        confidence_before.append(old_conf)
        confidence_after.append(best_score)

        if best_cluster != old_cluster or abs(best_score - old_conf) > 0.05:
            changes.append({
                "id": rid,
                "name": name,
                "old_cluster": old_cluster,
                "new_cluster": best_cluster,
                "old_conf": round(old_conf, 3),
                "new_conf": round(best_score, 3),
            })
            if not dry_run:
                conn.execute(
                    "UPDATE resources SET cluster = ?, cluster_confidence = ?, updated_at = datetime('now') WHERE id = ?",
                    (best_cluster, round(best_score, 3), rid),
                )

            re_cluster_map[best_cluster] = re_cluster_map.get(best_cluster, 0) + 1

    if not dry_run:
        conn.commit()

    # Report
    avg_before = np.mean(confidence_before) if confidence_before else 0
    avg_after = np.mean(confidence_after) if confidence_after else 0
    print(f"  Resources re-assigned: {len(changes)}")
    print(f"  Moved to general (orphans): {to_general}")
    print(f"  Avg confidence: {avg_before:.3f} → {avg_after:.3f}")

    print("\n  Re-distribution:")
    for cname, cnt in sorted(re_cluster_map.items(), key=lambda x: -x[1]):
        print(f"    {cname}: {cnt}")

    if changes:
        print("\n  Sample changes (up to 10):")
        for c in changes[:10]:
            print(f"    {c['name'][:40]}: {c['old_cluster']}({c['old_conf']}) → {c['new_cluster']}({c['new_conf']})")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.2f}s")
    return {
        "scanned": len(low_conf),
        "changed": len(changes),
        "to_general": to_general,
        "avg_conf_before": round(float(avg_before), 3),
        "avg_conf_after": round(float(avg_after), 3),
    }


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    recluster(dry_run=dry)
