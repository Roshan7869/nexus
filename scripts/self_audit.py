#!/usr/bin/env python3
"""
NEXUS Self-Audit Cron Script
=============================
Runs periodic health checks and auto-healing on the NEXUS learning pipeline.
Scheduled via Hermes cronjob (no_agent=True mode).

Checks:
  1. Degraded skills → restore after 7+ days of inactivity
  2. Pending outcomes → flag stale routed entries (>72h without outcome)
  3. Cluster centroids → verify all clusters have computed centroids
  4. Resource coverage → report untriggered resource count
  5. Thompson collapse → warn if any belief has beta<5 with >100 samples

Output is delivered to the user via cron delivery.
Silent (empty stdout) when everything is healthy.
"""
import sys, os, json
from pathlib import Path

# Add nexus root and server to path
# If running from ~/.hermes/scripts/, use NEXUS_HOME env or default to ~/nexus
NEXUS_ROOT = Path(os.environ.get("NEXUS_HOME", str(Path.home() / "nexus")))
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / 'server'))
os.environ.setdefault('NEXUS_DB_PATH', str(NEXUS_ROOT / 'db/nexus.db'))

try:
    from learning import check_cooldown_restorations, get_learning_stats, _get_conn
    HAS_LEARNING = True
except ImportError as e:
    HAS_LEARNING = False
    IMPORT_ERR = str(e)

ISSUES = []

def audit_cooldown():
    """Check degraded skills restoration."""
    conn = _get_conn()
    counts = conn.execute("""
        SELECT status, COUNT(*) as c FROM resources 
        WHERE status IN ('active', 'degraded')
        GROUP BY status
    """).fetchall()
    counts = {r['status']: r['c'] for r in counts}
    
    degraded_count = counts.get('degraded', 0)
    if degraded_count > 10:
        ISSUES.append(f"WARN: {degraded_count} resources still degraded after cooldown cycle")
    elif degraded_count > 0:
        pass  # expected — some may be recently degraded
    else:
        pass  # perfect
    
    # Run restoration
    check_cooldown_restorations(conn)
    
    # Check how many were restored
    still_degraded = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='degraded'"
    ).fetchone()[0]
    
    if still_degraded < degraded_count:
        restored = degraded_count - still_degraded
        print(f"Restored {restored} degraded skills from cooldown.")
    conn.close()

def audit_outcomes():
    """Check for stale pending outcomes."""
    conn = _get_conn()
    stale = conn.execute("""
        SELECT COUNT(*) FROM routing_log 
        WHERE (outcome IS NULL OR outcome = 'routed')
          AND created_at < datetime('now', '-72 hours')
    """).fetchone()[0]
    
    if stale > 50:
        ISSUES.append(f"WARN: {stale} routes pending >72h without outcome feedback")
    elif stale > 0:
        print(f"{stale} routes pending feedback (within 72h window — OK)")
    
    conn.close()

def audit_centroids():
    """Verify all clusters have centroids."""
    conn = _get_conn()
    try:
        no_centroid = conn.execute("""
            SELECT COUNT(*) FROM cluster_definitions 
            WHERE centroid_embedding IS NULL
        """).fetchone()[0]
        
        total = conn.execute("SELECT COUNT(*) FROM cluster_definitions").fetchone()[0]
        
        if no_centroid > 0:
            ISSUES.append(f"WARN: {no_centroid}/{total} clusters missing centroids")
        elif total > 0:
            print(f"All {total} clusters have centroids. OK.")
        else:
            ISSUES.append("WARN: No cluster definitions found in DB")
    except Exception as e:
        ISSUES.append(f"ERROR: centroid check failed: {e}")
    conn.close()

def audit_thompson():
    """Check for Thompson belief collapse."""
    conn = _get_conn()
    try:
        collapsed = conn.execute("""
            SELECT provider, model, task_type, alpha, beta, total_requests
            FROM model_beliefs
            WHERE total_requests > 100 AND beta < 5
            ORDER BY total_requests DESC
            LIMIT 5
        """).fetchall()
        
        if collapsed:
            for row in collapsed:
                alpha = row['alpha']
                beta = row['beta']
                mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0
                if mean > 0.98:
                    ISSUES.append(
                        f"Thompson collapse: {row['provider']}/{row['model']} "
                        f"({row['task_type']}): α={alpha:.1f}, β={beta:.1f}, "
                        f"mean={mean:.3f} — pure exploit, no exploration"
                    )
    except Exception as e:
        ISSUES.append(f"ERROR: thompson check failed: {e}")
    conn.close()

def audit_coverage():
    """Report resource coverage."""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        triggered = conn.execute("SELECT COUNT(*) FROM resources WHERE times_triggered > 0").fetchone()[0]
        untriggered = total - triggered
        pct = round(untriggered / max(total, 1) * 100, 1)
        
        if pct > 50:
            ISSUES.append(f"WARN: {untriggered}/{total} resources ({pct}%) never triggered")
    except Exception as e:
        ISSUES.append(f"ERROR: coverage check failed: {e}")
    conn.close()

def main():
    if not HAS_LEARNING:
        print(f"ERROR: learning module not available: {IMPORT_ERR}")
        sys.exit(1)
    
    audit_cooldown()
    audit_outcomes()
    audit_centroids()
    audit_thompson()
    audit_coverage()
    
    # Also capture learning_stats summary
    try:
        stats = get_learning_stats()
        outcomes = stats.get('outcomes', {})
        print(
            f"Routes: {outcomes.get('total_routes', '?')} total, "
            f"{outcomes.get('accuracy', '?')}% accuracy, "
            f"{outcomes.get('pending_or_routed', '?')} pending"
        )
        refl = stats.get('reflection', {})
        print(
            f"Reflection: {refl.get('total_failures', '?')} failures, "
            f"{refl.get('total_fixes', '?')} fixes, "
            f"{refl.get('fix_rate', '?')}% fix rate"
        )
    except Exception as e:
        ISSUES.append(f"ERROR: learning_stats failed: {e}")
    
    # Output
    if ISSUES:
        print("\nIssues found:")
        for issue in ISSUES:
            print(f"  ⚠  {issue}")
    else:
        # Silent when healthy — no output = all good
        pass

if __name__ == '__main__':
    main()