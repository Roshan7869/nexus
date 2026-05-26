#!/usr/bin/env python3
"""
nexus-maintenance.py — Full NEXUS health check + fix pipeline

Usage:
    python scripts/nexus-maintenance.py [--fix] [--embed] [--clusters]

What it does:
1. Check empty body_text — fill from file_path if readable
2. Check body_text="None" or NULL — fix
3. Check missing embeddings — generate via Ollama
4. Auto-degrade high-error resources (>40% error rate, >=5 uses)
5. Deduplicate Ruflo .d variant duplicates (set to archived)
6. Deduplicate exact duplicate body_text content (set to archived)
7. Clean up orphaned cluster memberships (referencing archived/deleted resources)
8. Verify cluster_membership is populated for all active resources
9. Output health report
"""
import os, sys, sqlite3, sqlite_vec, ollama
from pathlib import Path

NEXUS_DB = str(Path.home() / "nexus/db/nexus.db")
EMBED_MODEL = 'nomic-embed-text:latest'
MAX_BODY_LEN = 12000

def connect():
    conn = sqlite3.connect(NEXUS_DB, timeout=60)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn

def fix_empty_bodies(conn):
    """Fill empty body_text from file_path."""
    cur = conn.cursor()
    cur.execute("SELECT id, name, file_path, description FROM resources WHERE body_text IS NULL OR body_text IN ('None', 'NULL', '')")
    rows = cur.fetchall()
    fixed = 0
    for rid, name, fpath, desc in rows:
        content = ""
        if fpath:
            for base in [Path.home(), Path.home()/"nexus", Path.home()/"Downloads", Path("/usr")]:
                try_path = (base / fpath).resolve()
                if try_path.exists() and try_path.is_file():
                    try:
                        content = try_path.read_text(encoding='utf-8', errors='ignore')
                        break
                    except Exception:
                        continue
        if not content:
            content = desc or f"Resource {name} — no content available"
        cur.execute("UPDATE resources SET body_text=? WHERE id=?", (content[:MAX_BODY_LEN], rid))
        fixed += 1
    conn.commit()
    return fixed

def fix_embeddings(conn):
    """Generate embeddings for resources missing them."""
    cur = conn.cursor()
    cur.execute("SELECT id, name, resource_type, description, body_text FROM resources WHERE embedding IS NULL AND status='active'")
    rows = cur.fetchall()
    fixed = 0
    for rid, name, rtype, desc, body in rows:
        text = f"{name} {rtype} {desc or ''} {body or ''}"
        try:
            resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:12000])
            emb = sqlite_vec.serialize_float32(resp['embedding'])
            cur.execute("UPDATE resources SET embedding=? WHERE id=?", (emb, rid))
            fixed += 1
        except Exception as e:
            print(f"  [EMBED ERR] {name}: {e}")
    conn.commit()
    return fixed

def apply_auto_degradation(conn):
    """Mark resources with >40% error rate and >=5 uses as degraded, and ensure active otherwise."""
    cur = conn.cursor()
    # Degrade resources meeting failure threshold
    cur.execute("""
        UPDATE resources 
        SET status='degraded', updated_at=datetime('now')
        WHERE times_triggered >= 5 
          AND (times_wrong * 1.0 / times_triggered) > 0.4 
          AND status = 'active'
    """)
    degraded_count = cur.rowcount
    
    # Restore resources that no longer meet the failure threshold
    cur.execute("""
        UPDATE resources 
        SET status='active', updated_at=datetime('now')
        WHERE status='degraded'
          AND (times_triggered < 5 OR (times_wrong * 1.0 / times_triggered) <= 0.4)
    """)
    restored_count = cur.rowcount
    
    conn.commit()
    return degraded_count, restored_count

def archive_ruflo_d_variants(conn):
    """Archive Ruflo .d variant duplicates."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE resources 
        SET status='archived', updated_at=datetime('now') 
        WHERE status='active' AND name LIKE 'ruflo-%.d'
    """)
    conn.commit()
    return cur.rowcount

def archive_exact_duplicates(conn):
    """Archive exact duplicate body_text content, keeping the one with higher triggers."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, body_text, times_triggered 
        FROM resources 
        WHERE status='active' 
          AND body_text IS NOT NULL 
          AND body_text != ''
    """)
    rows = cur.fetchall()
    
    by_content = {}
    for rid, name, body, triggered in rows:
        if body.startswith("Resource ") and "no content available" in body:
            continue
        normalized = " ".join(body.strip().split())
        if not normalized or len(normalized) < 20:
            continue
        by_content.setdefault(normalized, []).append((rid, name, triggered))
        
    archived_count = 0
    for content, items in by_content.items():
        if len(items) > 1:
            items.sort(key=lambda x: (-x[2], x[0]))
            for rid, name, triggered in items[1:]:
                cur.execute("""
                    UPDATE resources 
                    SET status='archived', updated_at=datetime('now') 
                    WHERE id=?
                """, (rid,))
                archived_count += 1
    conn.commit()
    return archived_count

def clean_orphan_memberships(conn):
    """Remove orphaned cluster memberships (referencing archived/deleted resources)."""
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM cluster_membership 
        WHERE resource_id IN (SELECT id FROM resources WHERE status='archived')
           OR resource_id NOT IN (SELECT id FROM resources)
    """)
    conn.commit()
    return cur.rowcount

def fix_tier(conn):
    """Ensure tier is integer or TEXT '1','2',or '3'."""
    cur = conn.cursor()
    cur.execute("UPDATE resources SET tier=2 WHERE tier NOT IN (1, 2, 3) OR tier IS NULL")
    conn.commit()
    return cur.rowcount

def verify_membership(conn):
    """Ensure all active resources have at least one cluster."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM resources WHERE status='active' AND id NOT IN (SELECT resource_id FROM cluster_membership)")
    orphaned = cur.fetchall()
    fixed = 0
    for rid, in orphaned:
        cur.execute("INSERT OR IGNORE INTO cluster_membership (cluster_name, resource_id, affinity) VALUES ('general', ?, 0.5)", (rid,))
        fixed += 1
    conn.commit()
    return fixed

def report(conn):
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    active = cur.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
    degraded = cur.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
    archived = cur.execute("SELECT COUNT(*) FROM resources WHERE status='archived'").fetchone()[0]
    empty_body = cur.execute("SELECT COUNT(*) FROM resources WHERE body_text IS NULL OR body_text IN ('None','')").fetchone()[0]
    missing_emb = cur.execute("SELECT COUNT(*) FROM resources WHERE embedding IS NULL AND status='active'").fetchone()[0]
    no_cluster = cur.execute("SELECT COUNT(*) FROM resources WHERE status='active' AND id NOT IN (SELECT resource_id FROM cluster_membership)").fetchone()[0]
    orphaned_memberships = cur.execute("SELECT COUNT(*) FROM cluster_membership WHERE resource_id NOT IN (SELECT id FROM resources WHERE status='active')").fetchone()[0]
    by_type = dict(cur.execute("SELECT resource_type, COUNT(*) FROM resources WHERE status='active' GROUP BY resource_type").fetchall())
    
    print("="*60)
    print("NEXUS HEALTH REPORT")
    print("="*60)
    print(f"  Total resources in DB: {total}")
    print(f"  Active resources:      {active}")
    print(f"  Degraded resources:    {degraded}")
    print(f"  Archived resources:    {archived}")
    print(f"  Empty body_text:       {empty_body}")
    print(f"  Missing embeddings:    {missing_emb}")
    print(f"  Orphan memberships:    {orphaned_memberships}")
    print(f"  Active without cluster: {no_cluster}")
    print(f"  Active by type:")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {t:20s} {c}")
    print("="*60)
    return {
        "total": total,
        "active": active,
        "degraded": degraded,
        "archived": archived,
        "empty_body": empty_body,
        "missing_emb": missing_emb,
        "orphaned_memberships": orphaned_memberships,
        "no_cluster": no_cluster
    }

def main():
    conn = connect()
    print("Running NEXUS maintenance...\n")
    
    print("BEFORE MAINTENANCE:")
    r = report(conn)
    
    print("\n[FIX] Empty body_text...")
    n = fix_empty_bodies(conn)
    print(f"  Fixed: {n}")
    
    print("\n[FIX] Auto-degrade high-error resources...")
    deg, rest = apply_auto_degradation(conn)
    print(f"  Auto-degraded: {deg}, Restored to active: {rest}")
    
    print("\n[FIX] Archive Ruflo .d variant duplicates...")
    ruflo_d = archive_ruflo_d_variants(conn)
    print(f"  Archived: {ruflo_d}")
    
    print("\n[FIX] Archive exact body_text duplicates...")
    exact_dups = archive_exact_duplicates(conn)
    print(f"  Archived: {exact_dups}")
    
    print("\n[FIX] Clean up orphaned memberships...")
    orphans = clean_orphan_memberships(conn)
    print(f"  Cleaned: {orphans}")
    
    print("\n[FIX] Missing embeddings for active resources...")
    n = fix_embeddings(conn)
    print(f"  Fixed: {n}")
    
    print("\n[FIX] Tier normalization...")
    n = fix_tier(conn)
    print(f"  Fixed: {n}")
    
    print("\n[FIX] Assign active resources without cluster to 'general'...")
    n = verify_membership(conn)
    print(f"  Fixed: {n}")

    print("\n[FIX] Edge weight temporal decay (0.995/day)...")
    try:
        from edge_decay import run_decay
        run_decay()
    except Exception as e:
        print(f"  Edge decay skipped: {e}")

    print("\nAFTER MAINTENANCE:")
    r2 = report(conn)
    conn.close()
    
    if r2['empty_body'] == 0 and r2['missing_emb'] == 0 and r2['orphaned_memberships'] == 0 and r2['no_cluster'] == 0:
        print("NEXUS IS HEALTHY")
        sys.exit(0)
    else:
        print("NEXUS HAS ISSUES — review above")
        sys.exit(1)

if __name__ == "__main__":
    main()
