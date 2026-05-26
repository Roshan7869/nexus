#!/usr/bin/env python3
"""Compute weighted cluster centroids using cluster_confidence as weights.

Problem: Current centroids use simple arithmetic mean. Low-confidence members
pull the centroid away from the cluster's semantic core, which degrades
short-query matching precision.

Fix: Weight each resource's embedding by its cluster_confidence score.
High-confidence resources (core members) dominate the centroid; low-confidence
resources (edge members) contribute less.

Math:
  centroid_k = Σ(w_i * e_i) / Σ(w_i)   for all i in cluster k
  w_i = confidence_i + ε                (ε = 0.01 smoothing, avoid zero weight)
  Then L2-normalize the centroid.
"""

import sqlite3, json, time, sys
from pathlib import Path

import numpy as np

DB_PATH = Path.home() / "nexus/db/nexus.db"
CLUSTER_NAMES = [
    "analyzer_planner", "architect", "frontend_ui", "backend_api",
    "devops_infra", "quality_security", "knowledge_wiki", "general",
]
DIM = 384
EPSILON = 0.01  # smoothing to avoid zero-weight resources


def compute_weighted_centroids(dry_run: bool = True, compare: bool = True) -> dict:
    print("=" * 60)
    print("Weighted Cluster Centroids (confidence-weighted mean)")
    print("=" * 60)
    t0 = time.time()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    weighted_stats = {}
    unweighted_stats = {}

    for cluster_name in CLUSTER_NAMES:
        rows = conn.execute(
            "SELECT re.embedding, r.name, r.cluster_confidence "
            "FROM resource_embeddings re "
            "JOIN resources r ON re.resource_id = r.id "
            "WHERE r.cluster = ? AND r.status = 'active' AND re.embedding IS NOT NULL",
            (cluster_name,),
        ).fetchall()

        vectors = []
        weights = []
        confs = []
        for row in rows:
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if len(vec) == DIM:
                vectors.append(vec)
                conf = row["cluster_confidence"] or EPSILON
                confs.append(conf)
                weights.append(conf + EPSILON)

        n = len(vectors)
        if n == 0:
            print(f"  {cluster_name:25s}: 0 resources — skipping")
            weighted_stats[cluster_name] = None
            unweighted_stats[cluster_name] = None
            continue

        arr = np.array(vectors, dtype=np.float32)
        w = np.array(weights, dtype=np.float32)

        # Weighted centroid
        weighted = np.average(arr, axis=0, weights=w).astype(np.float32)
        wnorm = np.linalg.norm(weighted)
        if wnorm > 0:
            weighted = weighted / wnorm

        # Unweighted centroid (for comparison)
        unweighted = np.mean(arr, axis=0).astype(np.float32)
        unorm = np.linalg.norm(unweighted)
        if unorm > 0:
            unweighted = unweighted / unorm

        # Angular difference
        cos_sim = float(np.dot(weighted, unweighted))
        angle_deg = np.degrees(np.arccos(np.clip(cos_sim, -1.0, 1.0)))

        wavg_conf = float(np.average(confs, weights=w))
        uavg_conf = float(np.mean(confs))

        weighted_stats[cluster_name] = {
            "centroid": weighted,
            "resources": n,
            "avg_conf": round(wavg_conf, 3),
            "min_conf": round(min(confs), 3),
            "max_conf": round(max(confs), 3),
        }
        unweighted_stats[cluster_name] = {
            "centroid": unweighted,
            "avg_conf": round(uavg_conf, 3),
        }

        print(f"  {cluster_name:25s}: {n:4d} resources  |  "
              f"weighted_avg_conf={wavg_conf:.3f}  unweighted_avg_conf={uavg_conf:.3f}  |  "
              f"Δ={cos_sim:.4f} cosim ({angle_deg:.1f}°)")

        # Store weighted centroid in DB
        if not dry_run:
            conn.execute(
                "UPDATE cluster_definitions SET centroid_embedding = ?, resource_count = ?, updated_at = datetime('now') WHERE name = ?",
                (weighted.tobytes(), n, cluster_name),
            )

    if not dry_run:
        conn.commit()
        print(f"\n  Written to cluster_definitions.centroid_embedding")

    # ── Comparison: test queries ──
    if compare:
        print("\n  ── Comparison: weighted vs unweighted centroid affinities ──")
        test_queries = [
            ("fix bug in login handler", "analyzer_planner"),
            ("deploy docker to production", "devops_infra"),
            ("write unit tests for API", "quality_security"),
            ("design architecture for microservice", "architect"),
            ("audit security vulnerabilities", "quality_security"),
            ("create react dashboard component", "frontend_ui"),
            ("set up CI/CD pipeline", "devops_infra"),
            ("implement REST endpoint", "backend_api"),
            ("document API reference", "knowledge_wiki"),
            ("what is the system status", "general"),
        ]

        for q, expected in test_queries:
            try:
                sys.path.insert(0, str(Path.home() / "nexus"))
                from server.iso_embedder import IsoEmbedder
                embedder = IsoEmbedder()
                qvec = embedder.encode(q)
                qnorm = qvec / (np.linalg.norm(qvec) + 1e-10)

                best_weighted = ""
                best_unweighted = ""
                best_wscore = -1.0
                best_uscore = -1.0

                for cn in CLUSTER_NAMES:
                    ws = weighted_stats.get(cn)
                    us = unweighted_stats.get(cn)
                    if ws is None:
                        continue

                    # Weighted affinity
                    wscore = float(np.dot(qnorm, ws["centroid"]))
                    if wscore > best_wscore:
                        best_wscore = wscore
                        best_weighted = cn

                    # Unweighted affinity
                    uscore = float(np.dot(qnorm, us["centroid"]))
                    if uscore > best_uscore:
                        best_uscore = uscore
                        best_unweighted = cn

                w_ok = "✓" if best_weighted == expected else "✗"
                u_ok = "✓" if best_unweighted == expected else "✗"
                print(f"    '{q[:40]:40s}'  weighted→{best_weighted:20s}({best_wscore:.3f}) {w_ok}  |  "
                      f"unweighted→{best_unweighted:20s}({best_uscore:.3f}) {u_ok}")
            except Exception as e:
                print(f"    Error for '{q[:30]}': {e}")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.2f}s")
    return weighted_stats


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    if dry_run:
        print("DRY RUN — use --apply to write to database")
    compute_weighted_centroids(dry_run=dry_run)
