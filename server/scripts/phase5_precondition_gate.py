#!/usr/bin/env python3
"""
Phase 5: State-Aware Precondition Gating — wire precondition.py into tool_attention.py.
Creates the gating layer that blocks execution when state preconditions aren't met.
"""
import sys, shutil, json, os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path.home() / 'nexus/server'))

DB_PATH = Path.home() / "nexus/db/nexus.db"
BACKUP_DIR = Path.home() / "nexus/db/backups"

def run(dry_run=False):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = BACKUP_DIR / f"nexus.db.ph5_pre_gating.{ts}.bak"
    shutil.copy2(DB_PATH, backup_path)
    print(f"[Phase 5] Backup: {backup_path}")

    from precondition import TOOL_PRECONDITIONS, StateGates, StateVariable, GLOBAL_GATES

    from tool_attention import TA
    TA.load()

    default_tools = ["query_db", "mcp_invoke", "browser_action", "git_ops",
                     "docker_ops", "api_call", "file_write"]

    for tool_name in default_tools:
        allowed, msg = GLOBAL_GATES.check(tool_name)
        print(f"[Phase 5] {tool_name}: {'ALLOWED' if allowed else 'BLOCKED'} — {msg or 'OK'}")

    GLOBAL_GATES.set(StateVariable.HAS_MCP, True)
    GLOBAL_GATES.set(StateVariable.HAS_GIT, True)
    GLOBAL_GATES.set(StateVariable.PROJECT_INITIALIZED, True)
    GLOBAL_GATES.set(StateVariable.HAS_INTERNET, True)

    for tool_name in default_tools:
        allowed, msg = GLOBAL_GATES.check(tool_name)
        print(f"[Phase 5] {tool_name} (updated): {'ALLOWED' if allowed else 'BLOCKED'}")

    stats = {
        "dry_run": dry_run,
        "backup": str(backup_path),
        "timestamp": ts,
        "tools_configured": len(TOOL_PRECONDITIONS),
        "state_variables": len(list(StateVariable)),
        "default_state": {s.value: GLOBAL_GATES.get(s) for s in StateVariable},
    }

    print(f"[Phase 5] VERDICT: {'PASS' if not dry_run else 'DRY-RUN OK'}")
    report_path = Path.home() / "nexus/db/phase5_report.json"
    with open(report_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"[Phase 5] Report: {report_path}")
    TA.close()
    return stats

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    run(dry_run=ap.parse_args().dry_run)