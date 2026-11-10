"""NEXUS v4 Cluster Classifier.

Classifies all 1,097 resources into domain clusters using a two-phase approach:
  Phase A: Rule-based classification (regex + keyword matching) — ~80% coverage
  Phase B: Semantic classification (cosine similarity to cluster centroids) — remaining ~20%
  Phase C: Export uncertain classifications for human review

Usage:
    python -m server.cluster_classifier --classify       # Classify all resources
    python -m server.cluster_classifier --report          # Print classification report
    python -m server.cluster_classifier --export-csv      # Export uncertain for review
    python -m server.cluster_classifier --apply           # Write assignments to DB
"""
from server.db import get_db_path as _resolve_db_path

import csv
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
try:
    from server.service_layer import canonical_cluster_for_resource
except Exception:
    from service_layer import canonical_cluster_for_resource

# Path setup for standalone execution
DB_PATH = _resolve_db_path()
INDEX_PATH = Path(__file__).parent.parent / "db" / "faiss" / "resources.index"

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of classifying a single resource."""
    resource_id: int
    name: str
    resource_type: str
    cluster: str
    confidence: float
    method: str  # "rule" or "semantic" or "fallback"


@dataclass
class ClassificationReport:
    """Summary of classification across all resources."""
    total: int = 0
    classified: int = 0
    by_cluster: dict = field(default_factory=dict)
    by_method: dict = field(default_factory=dict)
    uncertain: list = field(default_factory=list)
    avg_confidence: float = 0.0


class ClusterClassifier:
    """Classify resources into domain clusters using rules + FAISS similarity."""

    def __init__(self, db_path: str = None, index_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.index_path = Path(index_path) if index_path else INDEX_PATH
        self._conn = None
        self._embedder = None
        self._centroids = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _load_config(self):
        """Import cluster config, avoiding circular imports."""
        from cluster_config import CLUSTER_DEFINITIONS
        return CLUSTER_DEFINITIONS

    def classify_resource(
        self, name: str, description: str, resource_type: str
    ) -> tuple[str, float, str]:
        """Classify a single resource.

        Returns:
            (cluster_name, confidence, method)
        """
        clusters = self._load_config()
        text = f"{name} {description}".lower()

        override_cluster = canonical_cluster_for_resource(name)
        if override_cluster:
            return override_cluster, 1.0, "rule"

        # Phase A: Rule-based classification
        best_cluster = None
        best_score = 0.0

        for cluster_name, config in clusters.items():
            if cluster_name == "general":
                continue  # General is fallback only

            # Check name patterns (regex)
            for pattern in config.get("name_patterns", []):
                try:
                    if re.search(pattern, name, re.IGNORECASE):
                        score = 0.9
                        if score > best_score:
                            best_score = score
                            best_cluster = cluster_name
                except re.error:
                    continue

            # Check trigger keywords
            keyword_hits = sum(
                1 for kw in config.get("trigger_keywords", [])
                if kw.lower() in text
            )
            if keyword_hits > 0:
                # More keyword hits = higher confidence, max 0.85
                score = min(0.5 + keyword_hits * 0.1, 0.85)
                if score > best_score:
                    best_score = score
                    best_cluster = cluster_name

            # Check resource type inclusion
            if resource_type in config.get("resource_types", []):
                # Small bonus for type match, not decisive
                pass  # type match alone doesn't classify

        if best_cluster and best_score >= 0.35:
            return best_cluster, best_score, "rule"

        # If no rule match, will be classified by semantic similarity later
        return None, 0.0, "none"

    def classify_all(self, dry_run: bool = False) -> ClassificationReport:
        """Classify all resources in the database.

        Args:
            dry_run: If True, don't write to database.

        Returns:
            ClassificationReport with summary statistics.
        """
        clusters = self._load_config()
        report = ClassificationReport()
        results = []

        # Load all resources
        cursor = self.conn.execute(
            "SELECT id, name, description, resource_type FROM resources WHERE status = 'active'"
        )
        resources = cursor.fetchall()
        report.total = len(resources)

        # Phase A: Rule-based classification
        rule_classified = 0
        for row in resources:
            cluster, confidence, method = self.classify_resource(
                row["name"], row["description"] or "", row["resource_type"]
            )
            if cluster:
                results.append(ClassificationResult(
                    resource_id=row["id"],
                    name=row["name"],
                    resource_type=row["resource_type"],
                    cluster=cluster,
                    confidence=confidence,
                    method=method,
                ))
                rule_classified += 1

        # Phase B: Semantic classification for unclassified resources
        unclassified_ids = {
            row["id"] for row in resources
        } - {r.resource_id for r in results}

        semantic_classified = 0
        if unclassified_ids and self.index_path.exists():
            semantic_results = self._semantic_classify(
                list(unclassified_ids), clusters
            )
            for r in semantic_results:
                results.append(r)
                semantic_classified += 1

        # Phase C: Assign remaining to "general"
        classified_ids = {r.resource_id for r in results}
        general_count = 0
        for row in resources:
            if row["id"] not in classified_ids:
                results.append(ClassificationResult(
                    resource_id=row["id"],
                    name=row["name"],
                    resource_type=row["resource_type"],
                    cluster="general",
                    confidence=0.15,
                    method="fallback",
                ))
                general_count += 1

        report.classified = len(results)
        report.by_method = {
            "rule": rule_classified,
            "semantic": semantic_classified,
            "fallback": general_count,
        }

        # Compute per-cluster counts
        report.by_cluster = {}
        for r in results:
            report.by_cluster[r.cluster] = report.by_cluster.get(r.cluster, 0) + 1

        # Track uncertain classifications (confidence < 0.5)
        report.uncertain = [
            r for r in results if r.confidence < 0.5
        ]

        # Compute average confidence
        if results:
            report.avg_confidence = sum(r.confidence for r in results) / len(results)

        # Apply to database if not dry run
        if not dry_run:
            self._apply_classifications(results)

        logger.info(
            f"Classification complete: {report.classified}/{report.total} "
            f"(rule={rule_classified}, semantic={semantic_classified}, "
            f"general={general_count}), "
            f"avg_confidence={report.avg_confidence:.3f}"
        )

        return report

    def _semantic_classify(
        self, resource_ids: list[int], clusters: dict
    ) -> list[ClassificationResult]:
        """Classify resources using FAISS similarity to cluster centroids."""
        results = []

        try:
            from iso_embedder import IsoEmbedder
            embedder = IsoEmbedder.get(str(self.db_path))
        except ImportError:
            logger.warning("IsoEmbedder not available, skipping semantic classification")
            return results

        # Get embeddings for unclassified resources
        placeholders = ",".join("?" * len(resource_ids))
        cursor = self.conn.execute(
            f"SELECT resource_id, embedding FROM resource_embeddings "
            f"WHERE resource_id IN ({placeholders})",
            resource_ids,
        )
        embeddings = {}
        for row in cursor.fetchall():
            if row["embedding"]:
                emb = np.frombuffer(row["embedding"], dtype=np.float32)
                if len(emb) == 384:
                    embeddings[row["resource_id"]] = emb

        if not embeddings:
            return results

        # Compute cluster centroids from rule-classified resources
        centroids = self._compute_centroids(embedder, clusters)
        if not centroids:
            return results

        # Classify each unclassified resource by cosine similarity to centroids
        for rid, emb in embeddings.items():
            best_cluster = "general"
            best_sim = 0.0

            # Normalize embedding
            emb_norm = emb / (np.linalg.norm(emb) + 1e-10)

            for cluster_name, centroid in centroids.items():
                if cluster_name == "general":
                    continue
                centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-10)
                sim = float(np.dot(emb_norm, centroid_norm))
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = cluster_name

            # Get resource info
            cursor = self.conn.execute(
                "SELECT name, resource_type FROM resources WHERE id = ?",
                (rid,),
            )
            row = cursor.fetchone()
            if row:
                results.append(ClassificationResult(
                    resource_id=rid,
                    name=row["name"],
                    resource_type=row["resource_type"],
                    cluster=best_cluster,
                    confidence=min(best_sim, 0.95),
                    method="semantic",
                ))

        return results

    def _compute_centroids(
        self, embedder, clusters: dict
    ) -> dict[str, np.ndarray]:
        """Compute cluster centroids from already-classified resources.

        Uses rule-based results as seed, then computes mean embeddings.
        """
        centroids = {}

        # Get all embeddings
        cursor = self.conn.execute(
            "SELECT resource_id, embedding FROM resource_embeddings WHERE embedding IS NOT NULL"
        )
        all_embeddings = {}
        for row in cursor.fetchall():
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            if len(emb) == 384:
                all_embeddings[row["resource_id"]] = emb

        if not all_embeddings:
            return centroids

        # For each cluster, compute centroid from resources that match rule patterns
        for cluster_name, config in clusters.items():
            if cluster_name == "general":
                continue

            # Get resources matching this cluster's rules
            matching_ids = set()
            cursor2 = self.conn.execute(
                "SELECT id, name, description FROM resources WHERE status = 'active'"
            )
            for row in cursor2.fetchall():
                cluster, _, _ = self.classify_resource(
                    row["name"], row["description"] or "", "skill"
                )
                if cluster == cluster_name:
                    matching_ids.add(row["id"])

            # Compute centroid from matching embeddings
            cluster_embeddings = [
                all_embeddings[rid]
                for rid in matching_ids
                if rid in all_embeddings
            ]

            if cluster_embeddings:
                centroid = np.mean(cluster_embeddings, axis=0)
                centroids[cluster_name] = centroid

        return centroids

    def _apply_classifications(self, results: list[ClassificationResult]):
        """Write cluster assignments to the database."""
        cursor = self.conn.cursor()
        for r in results:
            cursor.execute(
                "UPDATE resources SET cluster = ?, cluster_confidence = ? WHERE id = ?",
                (r.cluster, r.confidence, r.resource_id),
            )
        self.conn.commit()
        logger.info(f"Applied {len(results)} cluster assignments to database")

    def export_review_csv(self, output_path: str = None) -> int:
        """Export uncertain classifications (< 0.5 confidence) for human review.

        Returns:
            Number of uncertain resources exported.
        """
        if output_path is None:
            output_path = str(self.db_path.parent / "cluster_review.csv")

        cursor = self.conn.execute(
            "SELECT id, name, resource_type, cluster, cluster_confidence "
            "FROM resources WHERE cluster_confidence < 0.5 OR cluster IS NULL"
        )
        uncertain = cursor.fetchall()

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "resource_id", "name", "resource_type",
                "suggested_cluster", "confidence", "manual_cluster"
            ])
            for row in uncertain:
                writer.writerow([
                    row["id"], row["name"], row["resource_type"],
                    row["cluster"] or "uncertain",
                    row["cluster_confidence"],
                    "",  # manual_cluster to be filled by reviewer
                ])

        logger.info(f"Exported {len(uncertain)} uncertain classifications to {output_path}")
        return len(uncertain)

    def generate_report(self) -> dict:
        """Generate a classification report from the database."""
        cursor = self.conn.execute(
            "SELECT cluster, COUNT(*) as cnt, AVG(cluster_confidence) as avg_conf "
            "FROM resources WHERE cluster IS NOT NULL GROUP BY cluster ORDER BY cnt DESC"
        )
        by_cluster = {}
        for row in cursor.fetchall():
            by_cluster[row["cluster"]] = {
                "count": row["cnt"],
                "avg_confidence": round(row["avg_conf"], 3),
            }

        total = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM resources WHERE status = 'active'"
        ).fetchone()["cnt"]

        classified = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM resources WHERE cluster IS NOT NULL"
        ).fetchone()["cnt"]

        unclassified = total - classified

        return {
            "total_resources": total,
            "classified": classified,
            "unclassified": unclassified,
            "classification_rate": round(classified / max(total, 1), 3),
            "by_cluster": by_cluster,
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS Cluster Classifier")
    parser.add_argument("--classify", action="store_true", help="Classify all resources")
    parser.add_argument("--report", action="store_true", help="Print classification report")
    parser.add_argument("--export-csv", action="store_true", help="Export uncertain for review")
    parser.add_argument("--apply", action="store_true", help="Apply classifications to DB")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    args = parser.parse_args()

    classifier = ClusterClassifier()

    if args.classify or args.apply:
        report = classifier.classify_all(dry_run=args.dry_run or not args.apply)
        print(f"Classified {report.classified}/{report.total} resources")
        print(f"  Rule-based: {report.by_method.get('rule', 0)}")
        print(f"  Semantic: {report.by_method.get('semantic', 0)}")
        print(f"  Fallback (general): {report.by_method.get('fallback', 0)}")
        print(f"  Avg confidence: {report.avg_confidence:.3f}")
        print(f"  Uncertain (<0.5): {len(report.uncertain)}")
        print(f"\nBy cluster:")
        for cluster, count in sorted(report.by_cluster.items(), key=lambda x: -x[1]):
            print(f"  {cluster}: {count}")

    if args.report:
        report = classifier.generate_report()
        print(json.dumps(report, indent=2))

    if args.export_csv:
        count = classifier.export_review_csv()
        print(f"Exported {count} uncertain classifications for review")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
