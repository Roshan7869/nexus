#!/usr/bin/env python3
"""~/nexus/scripts/demo-orchestrator.py — Demo script for NEXUS Orchestrator"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / 'nexus/server'))

import importlib.util
spec = importlib.util.spec_from_file_location("nexus_mcp", Path.home() / "nexus/server/nexus-mcp.py")
nexus_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nexus_mcp)

design_plan = nexus_mcp.design_plan
execute_phase = nexus_mcp.execute_phase
verify_phase = nexus_mcp.verify_phase
nexus_health = nexus_mcp.nexus_health

print("=" * 60)
print("  NEXUS Orchestrator Demo")
print("=" * 60)
print()

# 1. Health check
print("1. Health Check:")
health = json.loads(nexus_health())
print(f"   Status: {health['status']}")
print(f"   Resources: {health['resources']}")
print(f"   Plans: {health['plans']}")
print()

# 2. Design a plan
print("2. Design Plan: 'Build a trading dashboard' (kaari)")
result = json.loads(design_plan("Build a trading dashboard", "kaari"))
plan_id = result.get("plan_id")
print(f"   Plan ID: {plan_id}")
print(f"   Goal: {result.get('plan', {}).get('goal')}")
if plan_id:
    # 3. Execute a phase
    print()
    print("3. Execute Phase: phase-3-implement")
    exec_result = json.loads(execute_phase(plan_id, "phase-3-implement"))
    print(f"   Status: {exec_result.get('status')}")
    print(f"   Model: {exec_result.get('routing', {}).get('model')}")
    print(f"   Message: {exec_result.get('message')}")
    
    # 4. Verify phase
    print()
    print("4. Verify Phase: phase-3-implement")
    verify_result = json.loads(verify_phase(plan_id, "phase-3-implement"))
    print(f"   Verdict: {verify_result.get('verdict')}")
    
    # 5. Check execution log
    print()
    print("5. Check Execution Log")
    import sqlite3
    conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')
    cursor = conn.cursor()
    cursor.execute("SELECT phase_id, matched_model, outcome FROM execution_log WHERE plan_id=?", (plan_id,))
    rows = cursor.fetchall()
    for row in rows:
        print(f"   Phase: {row[0]}, Model: {row[1]}, Outcome: {row[2]}")
    conn.close()

print()
print("=" * 60)
print("  Demo Complete")
print("=" * 60)
