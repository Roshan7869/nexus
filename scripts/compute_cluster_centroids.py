#!/usr/bin/env python3
"""Compute cluster centroids and manage cluster assignments.

Usage:
    python compute_cluster_centroids.py --classify       # Classify all resources into clusters
    python compute_cluster_centroids.py --centroids       # Recompute cluster centroids
    python compute_cluster_centroids.py --report          # Print classification report
    python compute_cluster_centroids.py --export-csv       # Export uncertain for human review
    python compute_cluster_centroids.py --seed             # Seed cluster_definitions table
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"
INDEX_META = Path(__file__).parent.parent / "db" / "faiss" / "resources.index.meta.json"

# Add server to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from cluster_config import CLUSTER_DEFINITIONS, CLUSTER_ORDER


def seed_cluster_definitions(conn: sqlite3.Connection):
    """Insert cluster definitions into the database."""
    cursor = conn.cursor()

    # Clear existing definitions
    cursor.execute("DELETE FROM cluster_definitions")

    for name in CLUSTER_ORDER:
        config = CLUSTER_DEFINITIONS[name]
        cursor.execute(
            """INSERT OR REPLACE INTO cluster_definitions
            (name, display_name, description, trigger_keywords, resource_types,
             name_patterns, default_skill, default_agents, auto_activate_threshold, max_resources)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                config["display_name"],
                config["description"],
                json.dumps(config["trigger_keywords"]),
                json.dumps(config["resource_types"]),
                json.dumps(config.get("name_patterns", [])),
                config["default_skill"],
                json.dumps(config.get("default_agents", [])),
                config.get("auto_activate_threshold", 0.35),
                config.get("expected_size", 200),
            ),
        )

    conn.commit()
    print(f"Seeded {len(CLUSTER_ORDER)} cluster definitions")


def compute_centroids(conn: sqlite3.Connection):
    """Compute cluster centroid embeddings from member resources."""
    # Load all embeddings
    cursor = conn.execute(
        "SELECT re.resource_id, re.embedding, r.cluster "
        "FROM resource_embeddings re "
        "JOIN resources r ON re.resource_id = r.id "
        "WHERE r.cluster IS NOT NULL AND re.embedding IS NOT NULL"
    )

    cluster_embeddings = {}  # cluster_name -> list of embeddings
    for row in cursor.fetchall():
        emb = np.frombuffer(row["embedding"], dtype=np.float32)
        if len(emb) == 384:
            cluster = row["cluster"]
            if cluster not in cluster_embeddings:
                cluster_embeddings[cluster] = []
            cluster_embeddings[cluster].append(emb)

    # Compute centroids and store
    for cluster_name, embs in cluster_embeddings.items():
        centroid = np.mean(embs, axis=0).astype(np.float32)
        centroid_blob = centroid.tobytes()

        conn.execute(
            "UPDATE cluster_definitions SET centroid_embedding = ? WHERE name = ?",
            (centroid_blob, cluster_name),
        )

    # Report
    print(f"Computed centroids for {len(cluster_embeddings)} clusters:")
    for cluster_name, embs in sorted(cluster_embeddings.items()):
        print(f"  {cluster_name}: {len(embs)} resources")

    conn.commit()


def classify_resources(conn: sqlite3.Connection, dry_run: bool = False):
    """Classify all resources into clusters using the ClusterClassifier."""
    from cluster_classifier import ClusterClassifier

    classifier = ClusterClassifier(str(DB_PATH), "")
    report = classifier.classify_all(dry_run=dry_run)

    print(f"\nClassification Results:")
    print(f"  Total: {report.total}")
    print(f"  Classified: {report.classified}")
    print(f"  Rule-based: {report.by_method.get('rule', 0)}")
    print(f"  Semantic: {report.by_method.get('semantic', 0)}")
    print(f"  Fallback: {report.by_method.get('fallback', 0)}")
    print(f"  Avg confidence: {report.avg_confidence:.3f}")
    print(f"\nBy cluster:")
    for cluster in CLUSTER_ORDER:
        count = report.by_cluster.get(cluster, 0)
        print(f"  {cluster}: {count}")

    return report


def generate_report(conn: sqlite3.Connection):
    """Print a classification report from the database."""
    from cluster_classifier import ClusterClassifier

    classifier = ClusterClassifier(str(DB_PATH), "")
    report = classifier.generate_report()
    print(json.dumps(report, indent=2))


def export_csv(conn: sqlite3.Connection):
    """Export uncertain classifications for human review."""
    from cluster_classifier import ClusterClassifier

    classifier = ClusterClassifier(str(DB_PATH), "")
    output_path = str(DB_PATH.parent / "cluster_review.csv")
    count = classifier.export_review_csv(output_path)
    print(f"Exported {count} uncertain classifications to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="NEXUS Cluster Centroid Manager")
    parser.add_argument("--classify", action="store_true", help="Classify all resources")
    parser.add_argument("--centroids", action="store_true", help="Compute cluster centroids")
    parser.add_argument("--seed", action="store_true", help="Seed cluster definitions table")
    parser.add_argument("--report", action="store_true", help="Print classification report")
    parser.add_argument("--export-csv", action="store_true", help="Export uncertain for review")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        if args.seed:
            seed_cluster_definitions(conn)

        if args.classify:
            classify_resources(conn, dry_run=args.dry_run)

        if args.centroids:
            compute_centroids(conn)

        if args.report:
            generate_report(conn)

        if args.export_csv:
            export_csv(conn)

        # Default: seed + classify + compute centroids
        if not any([args.classify, args.centroids, args.seed, args.report, args.export_csv]):
            print("Running full setup: seed → classify → compute centroids\n")
            seed_cluster_definitions(conn)
            classify_resources(conn, dry_run=args.dry_run)
            compute_centroids(conn)
            print("\nFull setup complete!")

    finally:
        conn.close()


if __name__ == "__main__":
    main()