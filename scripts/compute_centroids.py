#!/usr/bin/env python3
"""Phase A3: Compute cluster centroids from cluster assignments.

First-principles: Without centroids, cluster_router.py cannot compute
semantic affinities. It falls back to brittle substring keyword matching.
Centroids enable: query → encode → cosine_with_centroid → affinity score.

Algorithm:
  Step 1: For each cluster, collect all member resource embeddings
  Step 2: Compute weighted mean per cluster (weighted by cluster_confidence)
          centroid_k = Σ(w_i·e_i) / Σ(w_i),  w_i = confidence_i + 0.01
  Step 3: L2-normalize the centroid (for cosine similarity)
  Step 4: Store in cluster_definitions.centroid_embedding
  Step 5: Update cluster_definitions.resource_count
  Step 6: Verify: load centroids, compute affinity for a test query

Note: Confidence-weighted centroids give more influence to high-confidence
resources, reducing pull from edge members. Typically 2-8° angular shift
from unweighted in 384-d space. Use --unweighted to restore old behavior.

Bottleneck: Only 8 clusters, ~200 vectors/cluster. Negligible compute.
"""
import sqlite3, json, time, sys, re
from pathlib import Path

import numpy as np
import faiss

DB_PATH = Path.home() / "nexus/db/nexus.db"
CLUSTER_NAMES = [
    "analyzer_planner", "architect", "frontend_ui", "backend_api",
    "devops_infra", "quality_security", "knowledge_wiki", "general",
]
DIM = 384


def compute_centroids(dry_run: bool = False) -> dict:
    print("=" * 60)
    print("Phase A3: Compute Cluster Centroids")
    print("=" * 60)
    t0 = time.time()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    stats = {}

    for cluster_name in CLUSTER_NAMES:
        # Step 1: Get all embeddings in this cluster
        rows = conn.execute(
            "SELECT re.embedding, r.name, r.cluster_confidence "
            "FROM resource_embeddings re "
            "JOIN resources r ON re.resource_id = r.id "
            "WHERE r.cluster = ? AND r.status = 'active' AND re.embedding IS NOT NULL",
            (cluster_name,),
        ).fetchall()

        vectors = []
        conf_sum = 0.0
        for row in rows:
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if len(vec) == DIM:
                vectors.append(vec)
                conf_sum += row["cluster_confidence"] or 0.0

        n_resources = len(vectors)
        avg_conf = conf_sum / max(n_resources, 1)

        if n_resources == 0:
            print(f"  {cluster_name}: 0 resources with embeddings — skipping")
            stats[cluster_name] = {"resources": 0, "avg_conf": 0.0, "centroid": None}
            continue

        # Step 2: Compute weighted mean (confidence-weighted)
        # Weighted centroids reduce pull from low-confidence edge members.
        use_weighted = not any(a == "--unweighted" for a in sys.argv)
        nparr = np.array(vectors, dtype=np.float32)
        if use_weighted:
            weights = np.array([(row["cluster_confidence"] or 0.01) + 0.01 for row in rows], dtype=np.float32)
            centroid = np.average(nparr, axis=0, weights=weights).astype(np.float32)
        else:
            centroid = np.mean(nparr, axis=0).astype(np.float32)

        # Step 3: Normalize
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        # Step 4: Store in DB
        centroid_blob = centroid.tobytes()

        if not dry_run:
            conn.execute(
                "UPDATE cluster_definitions SET centroid_embedding = ?, resource_count = ?, updated_at = datetime('now') WHERE name = ?",
                (centroid_blob, n_resources, cluster_name),
            )

        stats[cluster_name] = {
            "resources": n_resources,
            "avg_conf": round(avg_conf, 3),
            "centroid_norm": round(float(np.linalg.norm(centroid)), 4),
        }

        print(f"  {cluster_name}: {n_resources} resources, avg_conf={avg_conf:.3f}")

    if not dry_run:
        conn.commit()

    # Step 6: Verify with test query
    print("\n  Verification — signaling test queries:")
    test_queries = [
        "fix this bug in the login page",
        "deploy the docker container to production",
        "write test cases for the API endpoint",
        "design the architecture for a new service",
    ]
    for q in test_queries:
        try:
            from iso_embedder import IsoEmbedder
            embedder = IsoEmbedder()
            qvec = embedder.encode(q)
            qnorm = qvec / (np.linalg.norm(qvec) + 1e-10)

            best_cluster = "general"
            best_score = -1.0
            for cn in CLUSTER_NAMES:
                s = stats[cn]
                if s["centroid"] is not None:
                    # need centroid bytes, reload. quick compute from db.
                    row = conn.execute("SELECT centroid_embedding FROM cluster_definitions WHERE name=?", (cn,)).fetchone()
                    if row and row["centroid_embedding"]:
                        cv = np.frombuffer(row["centroid_embedding"], dtype=np.float32)
                        score = float(np.dot(qnorm, cv))
                        if score > best_score:
                            best_score = score
                            best_cluster = cn

            print(f"    '{q[:40]}...' → {best_cluster} (affinity={best_score:.3f})")
        except Exception as e:
            print(f"    Verify error for '{q[:30]}': {e}")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.2f}s")
    return stats


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    compute_centroids(dry_run=dry)
