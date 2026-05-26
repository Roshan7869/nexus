#!/usr/bin/env python3
"""
NEXUS Core Engine & Architecture Audit Diagnostic Runner
"""
import sys
import sqlite3
import numpy as np
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"

def run_audit():
    print("====================================================")
    print("      NEXUS CORE ENGINE DIAGNOSTIC AUDIT LOG        ")
    print("====================================================")
    
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)
        
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    issues = []
    checks = []
    
    # ── PILLAR 1: DATABASE INTEGRITY ────────────────────────────────
    print("\n[Pillar 1: Database Integrity]")
    
    # 1. Orphaned cluster memberships
    orphans = conn.execute("""
        SELECT COUNT(*) FROM cluster_membership 
        WHERE resource_id NOT IN (SELECT id FROM resources WHERE status = 'active')
    """).fetchone()[0]
    checks.append(("No orphan cluster memberships", orphans == 0, f"Found {orphans} orphans"))
    
    # 2. Duplicate body text
    duplicates = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT body_text, COUNT(*) as cnt FROM resources 
            WHERE status = 'active' AND body_text IS NOT NULL AND body_text != ''
            GROUP BY body_text HAVING cnt > 1
        )
    """).fetchone()[0]
    checks.append(("No active body text duplicates", duplicates == 0, f"Found {duplicates} duplicate blocks"))
    
    # 3. Active resources with missing embeddings
    missing_embeds = conn.execute("""
        SELECT COUNT(*) FROM resources 
        WHERE status = 'active' AND (embedding IS NULL OR length(embedding) == 0)
    """).fetchone()[0]
    checks.append(("Active resources have vector embeddings", missing_embeds == 0, f"Found {missing_embeds} missing embeddings"))
    
    for desc, passed, detail in checks[:3]:
        status = "PASSED" if passed else "FAILED"
        print(f"  - {desc}: {status} ({detail if not passed else '0 issues'})")
        if not passed:
            issues.append(f"DB Integrity: {detail}")
            
    # ── PILLAR 2: CENTROID ALIGNMENT ────────────────────────────────
    print("\n[Pillar 2: Centroid Alignment]")
    
    centroids = conn.execute("""
        SELECT name, centroid_embedding FROM cluster_definitions 
        WHERE centroid_embedding IS NOT NULL
    """).fetchall()
    
    centroids_passed = len(centroids) == 8
    print(f"  - Centroids populated: {'PASSED' if centroids_passed else 'FAILED'} (Found {len(centroids)}/8)")
    if not centroids_passed:
        issues.append(f"Centroids: Only {len(centroids)}/8 cluster centroids are defined in DB")
        
    norms_check = True
    for c in centroids:
        c_arr = np.frombuffer(c["centroid_embedding"], dtype=np.float32)
        if len(c_arr) != 384:
            norms_check = False
            print(f"    * Warning: Centroid for '{c['name']}' has length {len(c_arr)} (Expected 384)")
            issues.append(f"Centroid length mismatch on cluster '{c['name']}'")
            
    print(f"  - Centroid dimension verification (384 floats): {'PASSED' if norms_check else 'FAILED'}")
    
    # ── PILLAR 3: THOMPSON SAMPLING & BELIEFS ────────────────────────
    print("\n[Pillar 3: Thompson Sampling & Belief States]")
    
    beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
    print(f"  - Total model belief profiles: {beliefs}")
    
    # Check if there are extreme belief priors (e.g. invalid probability bounds)
    invalid_beliefs = conn.execute("""
        SELECT COUNT(*) FROM model_beliefs WHERE alpha <= 0 OR beta <= 0
    """).fetchone()[0]
    print(f"  - Valid Beta priors: {'PASSED' if invalid_beliefs == 0 else 'FAILED'} (Found {invalid_beliefs} invalid bounds)")
    if invalid_beliefs > 0:
        issues.append(f"Thompson Sampling: Found {invalid_beliefs} invalid model belief boundaries (alpha/beta <= 0)")
        
    # Check benchmark seeding
    benchmarks_count = 0
    try:
        benchmarks_count = conn.execute("SELECT COUNT(*) FROM benchmark_results").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    print(f"  - Seeded model benchmark results: {benchmarks_count}")
    if benchmarks_count == 0:
        issues.append("Thompson Sampling: No benchmark results found (run scripts/seed_benchmarks.py)")

    # ── PILLAR 4: SELF-REFLECTION LOGS & GRAPH TOPOLOGY ──────────────────
    print("\n[Pillar 4: Self-Reflection & Graph Topology]")
    
    edges_count = 0
    try:
        edges_count = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    print(f"  - Active resource graph edges (topology): {edges_count}")
    if edges_count == 0:
        print("    * Note: Graph topology table is empty or missing edges.")

    failures = conn.execute("SELECT COUNT(*) FROM reflection_failures").fetchone()[0]
    fixes = conn.execute("SELECT COUNT(*) FROM reflection_fixes").fetchone()[0]
    print(f"  - Total recorded reflection failures: {failures}")
    print(f"  - Total recorded reflection fixes: {fixes}")
    
    if failures > 0:
        success_rate = conn.execute("""
            SELECT COUNT(*) FROM reflection_failures WHERE fix_successful = 1
        """).fetchone()[0]
        rate = (success_rate / failures) * 100
        print(f"  - Self-reflection success resolution rate: {rate:.2f}% ({success_rate}/{failures})")
    else:
        print("  - Self-reflection success resolution rate: N/A (no recorded failures)")
        
    # ── PILLAR 5: RESOURCE ACTIVATION PROFILE ────────────────────────
    print("\n[Pillar 5: Resource Activation Profile]")
    
    total_active = conn.execute("SELECT COUNT(*) FROM resources WHERE status = 'active'").fetchone()[0]
    cold_start = conn.execute("SELECT COUNT(*) FROM resources WHERE status = 'active' AND times_triggered = 0").fetchone()[0]
    cold_rate = (cold_start / total_active) * 100 if total_active > 0 else 0
    print(f"  - Active resource count: {total_active}")
    print(f"  - Cold-start resources (never triggered): {cold_start} ({cold_rate:.2f}%)")
    
    if cold_rate > 35:
        print("    * Warning: High density of cold-start resources. Epsilon-greedy routing must be prioritized.")
        
    # ── FINAL AUDIT CARD ───────────────────────────────────────────
    print("\n====================================================")
    print("                 AUDIT SCORE CARD                   ")
    print("====================================================")
    
    max_score = 100
    deduct = len(issues) * 15
    if orphans > 0: deduct += 10
    if duplicates > 0: deduct += 10
    if missing_embeds > 0: deduct += 15
    if not centroids_passed: deduct += 20
    
    score = max(0, max_score - deduct)
    print(f"  Health Check Index Score: {score}/100")
    print(f"  Deduction Issues Logged: {len(issues)}")
    for issue in issues:
        print(f"    - [!] {issue}")
        
    if score == 100:
        print("\n  >>> STATUS: SYSTEM INTEGRITY EXCELLENT (WORLDCLASS)")
    elif score >= 80:
        print("\n  >>> STATUS: SYSTEM INTEGRITY STABLE")
    else:
        print("\n  >>> STATUS: DEGRADED ENGINE INTEGRITY")
    print("====================================================")
    
    conn.close()

if __name__ == "__main__":
    run_audit()
