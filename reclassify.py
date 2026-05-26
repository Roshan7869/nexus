#!/usr/bin/env python3
"""Re-classify all resources with NULL cluster or confidence=0.0 using ClusterClassifier."""
import sys
import os
sys.path.insert(0, os.path.expanduser('~/nexus/server'))
sys.path.insert(0, os.path.expanduser('~/nexus'))

from cluster_classifier import ClusterClassifier

def main():
    print("=" * 60)
    print("NEXUS Resource Re-classification")
    print("=" * 60)
    
    classifier = ClusterClassifier()
    
    # Phase 1: Show current state
    conn = classifier.conn
    null_count = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE (cluster IS NULL OR cluster = '') AND status='active'"
    ).fetchone()[0]
    zero_conf = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE cluster_confidence = 0.0 AND status='active'"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
    
    print(f"Before classification:")
    print(f"  Total active resources: {total}")
    print(f"  NULL/empty cluster: {null_count}")
    print(f"  Zero confidence: {zero_conf}")
    print()
    
    # Phase 2: Run classification (not dry run)
    print("Running ClusterClassifier.classify_all()...")
    report = classifier.classify_all(dry_run=False)
    
    print(f"\nClassification Report:")
    print(f"  Total: {report.total}")
    print(f"  Classified: {report.classified}")
    print(f"  By method: {report.by_method}")
    print(f"  By cluster: {report.by_cluster}")
    print(f"  Avg confidence: {report.avg_confidence:.3f}")
    print(f"  Uncertain (<0.5): {len(report.uncertain)}")
    
    # Phase 3: Verify improvements
    null_after = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE (cluster IS NULL OR cluster = '') AND status='active'"
    ).fetchone()[0]
    zero_after = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE cluster_confidence = 0.0 AND status='active'"
    ).fetchone()[0]
    
    print(f"\nAfter classification:")
    print(f"  NULL/empty cluster: {null_after} (was {null_count}, delta={null_count-null_after})")
    print(f"  Zero confidence: {zero_after} (was {zero_conf}, delta={zero_conf-zero_after})")
    
    # Phase 4: Show cluster distribution
    dist = conn.execute(
        "SELECT cluster, COUNT(*), ROUND(AVG(cluster_confidence), 3) FROM resources WHERE status='active' GROUP BY cluster ORDER BY COUNT(*) DESC"
    ).fetchall()
    print("\nCluster distribution after classification:")
    for row in dist:
        print(f"  {row[0] or 'NULL':20s}: {row[1]:4d} resources, avg_conf={row[2]}")
    
    conn.close()
    print("\nDone.")

if __name__ == '__main__':
    main()