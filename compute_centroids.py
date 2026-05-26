#!/usr/bin/env python3
"""Compute cluster centroids for all NEXUS clusters from resource embeddings."""
import sys
import os
import sqlite3
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.expanduser('~/nexus/server'))
sys.path.insert(0, os.path.expanduser('~/nexus'))

DB_PATH = Path.home() / 'nexus/db/nexus.db'

def compute_centroids():
    print("=" * 60)
    print("NEXUS Cluster Centroid Computation")
    print("=" * 60)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Get all clusters with their resources and embeddings
    clusters = conn.execute("""
        SELECT DISTINCT cluster FROM resources 
        WHERE cluster IS NOT NULL AND cluster != '' AND status = 'active'
    """).fetchall()
    
    cluster_names = [c[0] for c in clusters]
    print(f"Clusters found: {cluster_names}")
    
    total_centroided = 0
    
    for cluster_name in cluster_names:
        # Get embeddings for resources in this cluster
        rows = conn.execute("""
            SELECT r.id, r.name, re.embedding, r.cluster_confidence
            FROM resources r
            LEFT JOIN resource_embeddings re ON re.resource_id = r.id
            WHERE r.cluster = ? AND r.status = 'active'
        """, (cluster_name,)).fetchall()
        
        valid_vectors = []
        for row in rows:
            rid, name, emb_bytes, conf = row
            if emb_bytes is None:
                continue
            vec = np.frombuffer(emb_bytes, dtype=np.float32)
            if len(vec) == 384:  # all-MiniLM-L6-v2 dimension
                # Weight by confidence
                weight = conf if conf and conf > 0 else 0.15
                valid_vectors.append((vec, weight))
        
        if not valid_vectors:
            print(f"  {cluster_name}: No valid embeddings ({len(rows)} resources), skipping")
            continue
        
        # Compute weighted centroid
        vectors_np = np.array([v for v, w in valid_vectors])
        weights = np.array([w for v, w in valid_vectors]).reshape(-1, 1)
        
        # Normalize vectors
        norms = np.linalg.norm(vectors_np, axis=1, keepdims=True)
        norms[norms == 0] = 1
        vectors_norm = vectors_np / norms
        
        # Weighted average
        centroid = np.average(vectors_norm, axis=0, weights=weights.flatten())
        # Normalize centroid
        centroid = centroid / np.linalg.norm(centroid)
        
        centroid_bytes = centroid.astype(np.float32).tobytes()
        resource_count = len(valid_vectors)
        
        # Update existing row or insert if missing
        existing = conn.execute(
            "SELECT id FROM cluster_definitions WHERE name = ?", (cluster_name,)
        ).fetchone()
        
        if existing:
            conn.execute("""
                UPDATE cluster_definitions 
                SET centroid_embedding = ?, resource_count = ?, updated_at = datetime('now')
                WHERE name = ?
            """, (centroid_bytes, resource_count, cluster_name))
        else:
            conn.execute("""
                INSERT INTO cluster_definitions 
                (name, display_name, description, centroid_embedding, resource_count, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (cluster_name, cluster_name.replace('_', ' ').title(), 
                  f'Auto-computed cluster for {cluster_name}', centroid_bytes, resource_count))
        
        total_centroided += 1
        print(f"  {cluster_name}: {resource_count} resources, centroid computed (||c|| = {np.linalg.norm(centroid):.4f})")
    
    conn.commit()
    conn.close()
    
    print(f"\nCentroids computed for {total_centroided}/{len(cluster_names)} clusters")
    print("Done.")

if __name__ == '__main__':
    compute_centroids()