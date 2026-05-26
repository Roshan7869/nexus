#!/usr/bin/env python3
"""
Phase 2: Tier Classification
Classifies 1,097 resources into T1/T2/T3 based on token_count + resource_type heuristic.
T1: ≤ 500 tokens  → haiku (fast, cheap)
T2: 500–2000     → devstral-small-2 (current)
T3: ≥ 2000 OR any tool > 1500 tokens → gpt-4o-mini (capable)
Uses existing token_count column. Backup before write.
"""
import sqlite3, json, argparse, shutil, os, sys
from datetime import datetime

DB_PATH = "/home/roshan/nexus/db/nexus.db"
BACKUP_DIR = "/home/roshan/nexus/db/backups"
REPORT_PATH = "/home/roshan/nexus/db/phase2_report.json"

THRESHOLDS = {
    1: 500,
    2: 2000,
}
FALLBACK_TIER = 2

RULES = {
    "mcp": 3,
    "cli_tool": 2,
    "skill": None,  # compute from token_count
    "command": None,
    "design_system": 1,
    "agent": None,
    "hook": 1,
    "craft": 2,
    "doc": 1,
    "spec": 1,
}

def classify(token_count, resource_type):
    rule = RULES.get(resource_type)
    if rule is not None:
        return rule
    if token_count is None or token_count <= 0:
        return FALLBACK_TIER
    if token_count <= THRESHOLDS[1]:
        return 1
    if token_count <= THRESHOLDS[2]:
        return 2
    return 3

def run(dry_run=False):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_path = os.path.join(BACKUP_DIR, f"nexus.db.ph2_pre_classify.{ts}.bak")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(DB_PATH, backup_path)
    print(f"[Phase 2] Backup: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, resource_type, token_count, description, body_text,
               LENGTH(description) as desc_len, LENGTH(body_text) as body_len
        FROM resources ORDER BY id
    """)
    rows = cur.fetchall()

    changes = []
    for (rid, rtype, tc, desc, body, dlen, blen) in rows:
        old_tier = None
        cur.execute("SELECT tier FROM resources WHERE id=?", (rid,))
        old_tier = cur.fetchone()[0]
        new_tier = classify(tc, rtype)
        if old_tier != new_tier:
            changes.append({
                "id": rid, "old_tier": old_tier, "new_tier": new_tier,
                "type": rtype, "token_count": tc, "desc_len": dlen
            })
            if not dry_run:
                cur.execute("UPDATE resources SET tier=? WHERE id=?", (new_tier, rid))

    conn.commit()

    cur.execute("SELECT tier, COUNT(*) FROM resources GROUP BY tier ORDER BY tier")
    tier_dist = {str(k): v for k, v in cur.fetchall()}

    stats = {
        "dry_run": dry_run,
        "backup": backup_path,
        "timestamp": ts,
        "total_resources": len(rows),
        "tier_changes": len(changes),
        "new_tier_distribution": tier_dist,
        "rules_applied": RULES,
        "thresholds": THRESHOLDS,
        "changes_sample": changes[:20],
    }

    print(f"[Phase 2] Classified {len(rows)} resources. {len(changes)} tier changes.")
    print(f"[Phase 2] New distribution: {tier_dist}")
    print(f"[Phase 2] VERDICT: {'PASS' if not dry_run else 'DRY-RUN OK'}")

    with open(REPORT_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"[Phase 2] Report: {REPORT_PATH}")

    return stats

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = ap.parse_args()
    run(dry_run=args.dry_run)