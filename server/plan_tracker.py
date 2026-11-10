#!/usr/bin/env python3
"""NEXUS Plan Tracker — plan execution lifecycle management.

Reads/writes plans from the `plans` DB table. Provides phase-by-phase
tracking so the orchestrator can advance plans through their DAG.

Usage:
    from plan_tracker import (
        get_plan, list_plans, create_plan,
        start_phase, complete_phase, abandon_plan,
        get_next_phase, add_artifact,
    )

    plan = get_plan(1)
    next_phase = get_next_plan(plan)
    complete_phase(plan["id"])
"""
from server.db import get_db_path as _resolve_db_path

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

DB_PATH = _resolve_db_path()


def _connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row) if row else {}


def _parse_phases(phases_json: str) -> List[Dict]:
    try:
        return json.loads(phases_json) if phases_json else []
    except (json.JSONDecodeError, TypeError):
        return []


def _serialize(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def get_plan(plan_id: int) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            return {}
        plan = _row_to_dict(row)
        plan["phases"] = _parse_phases(plan.get("phases_json", "[]"))
        plan["completed"] = _parse_phases(plan.get("phases_completed", "[]"))
        plan["remaining"] = _parse_phases(plan.get("phases_remaining", "[]"))
        plan["artifact_list"] = _parse_phases(plan.get("artifacts", "[]"))
        return plan
    finally:
        conn.close()


def list_plans(
    status: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if project:
            conditions.append("project = ?")
            params.append(project)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT id, goal, project, status, plan_type, current_phase, "
            f"phases_completed, phases_remaining, created_at, updated_at "
            f"FROM plans {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def create_plan(
    goal: str,
    project: str = "default",
    phases: Optional[List[Dict]] = None,
    plan_type: str = "adaptive",
) -> int:
    conn = _connect()
    try:
        phases = phases or []
        remaining = [p.get("id", str(i)) for i, p in enumerate(phases)]
        cur = conn.execute(
            "INSERT INTO plans (goal, project, phases_json, phases_remaining, plan_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (goal, project, _serialize(phases), _serialize(remaining), plan_type),
        )
        conn.commit()
        plan_id = cur.lastrowid
        logger.info("Created plan %d: %s", plan_id, goal[:60])
        return plan_id
    finally:
        conn.close()


def start_phase(plan_id: int) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return {"error": "plan not found"}
        plan = _row_to_dict(row)
        if plan["status"] == "completed":
            return {"error": "plan already completed"}
        remaining = _parse_phases(plan.get("phases_remaining", "[]"))
        if not remaining:
            return {"error": "no remaining phases"}
        next_phase_id = remaining[0]
        conn.execute(
            "UPDATE plans SET status='active', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), plan_id),
        )
        conn.commit()
        return {"plan_id": plan_id, "phase_id": next_phase_id, "status": "active"}
    finally:
        conn.close()


def complete_phase(plan_id: int) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return {"error": "plan not found"}
        plan = _row_to_dict(row)
        remaining = _parse_phases(plan.get("phases_remaining", "[]"))
        completed = _parse_phases(plan.get("phases_completed", "[]"))
        if not remaining:
            return {"error": "no remaining phases to complete"}
        phase_id = remaining.pop(0)
        completed.append(phase_id)
        current_phase = plan["current_phase"] + 1
        new_status = "completed" if not remaining else "active"
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE plans SET status=?, current_phase=?, phases_completed=?, "
            "phases_remaining=?, updated_at=? WHERE id=?",
            (new_status, current_phase, _serialize(completed),
             _serialize(remaining), now, plan_id),
        )
        conn.commit()
        logger.info("Plan %d phase %s complete (%s)", plan_id, phase_id, new_status)
        return {
            "plan_id": plan_id,
            "phase_id": phase_id,
            "new_status": new_status,
            "remaining": len(remaining),
        }
    finally:
        conn.close()


def get_next_phase(plan_id: int) -> Optional[str]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT phases_remaining FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return None
        remaining = _parse_phases(row["phases_remaining"])
        return remaining[0] if remaining else None
    finally:
        conn.close()


def add_artifact(plan_id: int, artifact: str) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT artifacts FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return {"error": "plan not found"}
        artifacts = _parse_phases(row["artifacts"])
        artifacts.append(artifact)
        conn.execute(
            "UPDATE plans SET artifacts=?, updated_at=? WHERE id=?",
            (_serialize(artifacts), datetime.now().isoformat(), plan_id),
        )
        conn.commit()
        return {"plan_id": plan_id, "artifacts": len(artifacts)}
    finally:
        conn.close()


def abandon_plan(plan_id: int, reason: str = "") -> Dict[str, Any]:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE plans SET status='failed', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), plan_id),
        )
        conn.commit()
        logger.info("Plan %d abandoned: %s", plan_id, reason[:80])
        return {"plan_id": plan_id, "status": "failed"}
    finally:
        conn.close()


def repair_plan(plan_id: Optional[int] = None) -> Dict[str, Any]:
    """Back-fill phases_remaining from phases_json for stuck plans."""
    conn = _connect()
    try:
        if plan_id:
            rows = conn.execute(
                "SELECT id, phases_json, phases_remaining FROM plans WHERE id = ?",
                (plan_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, phases_json, phases_remaining FROM plans "
                "WHERE status='planning' AND (phases_remaining IS NULL OR phases_remaining = '[]')"
            ).fetchall()
        fixed = 0
        for row in rows:
            plan = _row_to_dict(row)
            remaining = _parse_phases(plan.get("phases_remaining", "[]"))
            if remaining:
                continue
            phases = _parse_phases(plan.get("phases_json", "[]"))
            if not phases:
                continue
            phase_ids = [p.get("id", str(i)) for i, p in enumerate(phases)]
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE plans SET phases_remaining=?, updated_at=? WHERE id=?",
                (_serialize(phase_ids), now, plan["id"]),
            )
            fixed += 1
        conn.commit()
        msg = f"Repaired {fixed} plans" if plan_id is None else f"Repaired plan {plan_id}"
        logger.info(msg)
        return {"fixed": fixed}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python plan_tracker.py <command> [args]")
        print("Commands: list, get <id>, create <goal>, next <id>")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        for p in list_plans(status=status):
            print(f"  [{p['id']}] {p['status']:10s} {p['goal'][:70]}")
    elif cmd == "get" and len(sys.argv) > 2:
        plan = get_plan(int(sys.argv[2]))
        if plan:
            print(f"ID: {plan['id']}  Status: {plan['status']}  Type: {plan['plan_type']}")
            print(f"Goal: {plan['goal']}")
            print(f"Phase: {plan['current_phase']}")
            print(f"Completed: {plan.get('completed', [])}")
            print(f"Remaining: {plan.get('remaining', [])}")
            print(f"Artifacts: {plan.get('artifact_list', [])}")
        else:
            print("Not found")
    elif cmd == "next" and len(sys.argv) > 2:
        phase = get_next_phase(int(sys.argv[2]))
        print(f"Next phase: {phase}" if phase else "No remaining phases")
    else:
        print(f"Unknown command: {cmd}")
