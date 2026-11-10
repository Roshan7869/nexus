"""NEXUS Plan Executor — OOC/OOM-safe persistent phase/task/subtask engine.

Solves the graphify OOC/OOM problem by externalizing ALL plan state into
nexus.db. The IDE never holds the full plan — just the current unit of work.

Flow:
  1. nexus_design_plan() stores plan in DB  →  returns plan_id
  2. Plan executor reads one phase/task/subtask at a time  →  returns to IDE
  3. IDE executes that unit, calls complete_unit()  →  executor persists result
  4. On OOC/OOM: next session calls resume_plan(plan_id)  →  picks up exactly where left off
  5. Executor handles retries, dependency resolution, parallel dispatch
"""
from server.db import get_db_path as _resolve_db_path

import json
import logging
import time
import uuid
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

DB_PATH = _resolve_db_path()


class PlanExecutor:
    """Persistent plan execution engine with checkpoint-resume."""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=60, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ─── Plan CRUD ────────────────────────────────────────────────────────

    def create_plan(self, goal: str, project: str = "default",
                    phases: List[Dict] = None, plan_type: str = "adaptive") -> int:
        """Store a new plan in the DB and return plan_id."""
        phases_json = json.dumps(phases or [])
        phase_ids = [p.get("id", f"phase-{i}") for i, p in enumerate(phases or [])]
        cursor = self.conn.execute(
            """INSERT INTO plans (goal, project, phases_json, current_phase, 
               phases_completed, phases_remaining, status, plan_type)
               VALUES (?, ?, '[]', 0, '[]', '[]', 'planning', ?)""",
            (goal, project, plan_type),
        )
        plan_id = cursor.lastrowid
        # Store full phases in a separate structured way via execution_log
        self.conn.execute(
            "UPDATE plans SET phases_json=? WHERE id=?",
            (phases_json, plan_id),
        )
        # Enumerate all phase IDs as remaining
        self.conn.execute(
            "UPDATE plans SET phases_remaining=? WHERE id=?",
            (json.dumps(phase_ids), plan_id),
        )
        self.conn.commit()
        logger.info(f"Created plan {plan_id}: {goal[:60]}")
        return plan_id

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        """Get full plan state."""
        row = self.conn.execute(
            "SELECT * FROM plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def update_plan_status(self, plan_id: int, status: str):
        self.conn.execute(
            "UPDATE plans SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, plan_id),
        )
        self.conn.commit()

    def add_artifact(self, plan_id: int, artifact: str):
        row = self.conn.execute(
            "SELECT artifacts FROM plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not row:
            return
        artifacts = json.loads(row["artifacts"] or "[]")
        artifacts.append(artifact)
        self.conn.execute(
            "UPDATE plans SET artifacts=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(artifacts), plan_id),
        )
        self.conn.commit()

    # ─── Phase Execution ──────────────────────────────────────────────────

    def get_next_phase(self, plan_id: int) -> Optional[Dict]:
        """Get the next uncompleted phase. Returns None if all done."""
        plan = self.get_plan(plan_id)
        if not plan:
            return None
        remaining = json.loads(plan["phases_remaining"] or "[]")
        if not remaining:
            return None
        phases = json.loads(plan["phases_json"] or "[]")
        phase_map = {p.get("id"): p for p in phases}
        next_id = remaining[0]
        phase = phase_map.get(next_id, {"id": next_id})
        return {
            "plan_id": plan_id,
            "phase_id": next_id,
            "phase": phase,
            "plan_goal": plan["goal"],
            "project": plan["project"],
            "progress": {
                "completed": json.loads(plan["phases_completed"] or "[]"),
                "remaining": remaining,
                "total": len(phases),
            },
        }

    def complete_phase(self, plan_id: int, phase_id: str,
                       outcome: str = "correct", tokens_used: int = 0,
                       skills_used: List[str] = None, notes: str = ""):
        """Mark a phase as complete and advance to next."""
        plan = self.get_plan(plan_id)
        if not plan:
            return

        completed = json.loads(plan["phases_completed"] or "[]")
        remaining = json.loads(plan["phases_remaining"] or "[]")

        if phase_id in remaining:
            remaining.remove(phase_id)
        if phase_id not in completed:
            completed.append(phase_id)

        is_done = len(remaining) == 0
        status = "completed" if is_done else "active"

        self.conn.execute(
            """UPDATE plans SET current_phase=current_phase+1,
               phases_completed=?, phases_remaining=?, status=?,
               updated_at=datetime('now') WHERE id=?""",
            (json.dumps(completed), json.dumps(remaining), status, plan_id),
        )

        # Log execution
        self.conn.execute(
            """INSERT INTO execution_log 
               (plan_id, phase_id, task_summary, outcome, tokens_used, skills_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (plan_id, phase_id, notes[:200], outcome, tokens_used,
             json.dumps(skills_used or [])),
        )
        self.conn.commit()
        logger.info(f"Plan {plan_id} phase {phase_id} done ({outcome})")

    def fail_phase(self, plan_id: int, phase_id: str,
                   error: str, tokens_used: int = 0):
        """Mark a phase as failed but keep plan active for retry."""
        self.conn.execute(
            """INSERT INTO execution_log
               (plan_id, phase_id, task_summary, outcome, tokens_used)
               VALUES (?, ?, ?, 'wrong', ?)""",
            (plan_id, phase_id, error[:200], tokens_used),
        )
        self.conn.execute(
            "UPDATE plans SET updated_at=datetime('now') WHERE id=?",
            (plan_id,),
        )
        self.conn.commit()
        logger.warning(f"Plan {plan_id} phase {phase_id} FAILED: {error[:80]}")

    def retry_phase(self, plan_id: int, phase_id: str):
        """Re-queue a phase for retry (move back to remaining)."""
        plan = self.get_plan(plan_id)
        if not plan:
            return
        completed = json.loads(plan["phases_completed"] or "[]")
        remaining = json.loads(plan["phases_remaining"] or "[]")

        if phase_id in completed:
            completed.remove(phase_id)
        if phase_id not in remaining:
            remaining.insert(0, phase_id)

        self.conn.execute(
            """UPDATE plans SET phases_completed=?, phases_remaining=?,
               current_phase=MAX(0, current_phase-1),
               updated_at=datetime('now') WHERE id=?""",
            (json.dumps(completed), json.dumps(remaining), plan_id),
        )
        self.conn.commit()
        logger.info(f"Plan {plan_id} phase {phase_id} queued for retry")

    # ─── Subtask Tracking (granular) ──────────────────────────────────────

    def begin_subtask(self, plan_id: int, phase_id: str, subtask_name: str,
                      resource_name: str = "", resource_type: str = "skill") -> str:
        """Record a subtask start. Returns trace_id."""
        trace_id = str(uuid.uuid4())[:12]
        order = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM tool_traces WHERE task_run_id=? AND phase_id=?",
            (f"plan-{plan_id}", phase_id),
        ).fetchone()["cnt"]
        self.conn.execute(
            """INSERT INTO tool_traces 
               (task_run_id, phase_id, subtask_name, resource_name, resource_type,
                trace_order, status, execution_mode, side_effect_level, tool_role)
               VALUES (?, ?, ?, ?, ?, ?, 'in_progress', 'automatic', 'write', 'primary')""",
            (f"plan-{plan_id}", phase_id, subtask_name,
             resource_name, resource_type, order + 1),
        )
        self.conn.commit()
        return trace_id

    def complete_subtask(self, plan_id: int, phase_id: str, subtask_name: str,
                         outcome: str = "correct", notes: str = ""):
        """Mark a subtask complete with outcome."""
        self.conn.execute(
            """UPDATE tool_traces SET status='completed', outcome=?, notes=?
               WHERE task_run_id=? AND phase_id=? AND subtask_name=?""",
            (outcome, notes[:200], f"plan-{plan_id}", phase_id, subtask_name),
        )
        self.conn.commit()

    def get_subtasks(self, plan_id: int, phase_id: str = None) -> List[Dict]:
        """Get all subtasks for a plan, optionally filtered by phase."""
        if phase_id:
            rows = self.conn.execute(
                """SELECT * FROM tool_traces WHERE task_run_id=? AND phase_id=?
                   ORDER BY trace_order""",
                (f"plan-{plan_id}", phase_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM tool_traces WHERE task_run_id=?
                   ORDER BY trace_order""",
                (f"plan-{plan_id}",),
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── Task Runs (broker integration) ───────────────────────────────────

    def create_task_run(self, task_text: str, project: str = "default",
                        plan_id: int = None) -> str:
        """Create a broker task run linked to a plan."""
        task_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            """INSERT INTO task_runs 
               (id, task_text, project, status, plan_id, created_at, updated_at)
               VALUES (?, ?, ?, 'previewed', ?, datetime('now'), datetime('now'))""",
            (task_id, task_text, project, plan_id),
        )
        self.conn.commit()
        return task_id

    def update_task_run(self, task_id: str, **kwargs):
        sets = []
        params = []
        for k, v in kwargs.items():
            if k in ("status", "mode", "cluster", "review_status"):
                sets.append(f"{k}=?")
                params.append(v)
            elif k in ("plan_json", "execution_preview_json", "final_report_json",
                       "bundle_graph_json", "repo_context_json"):
                sets.append(f"{k}=?")
                params.append(json.dumps(v) if not isinstance(v, str) else v)
        if sets:
            sets.append("updated_at=datetime('now')")
            params.append(task_id)
            self.conn.execute(
                f"UPDATE task_runs SET {', '.join(sets)} WHERE id=?",
                params,
            )
            self.conn.commit()

    # ─── Checkpoint & Resume ──────────────────────────────────────────────

    def checkpoint(self, plan_id: int) -> Dict:
        """Create an OOC/OOM-safe checkpoint of current plan state."""
        plan = self.get_plan(plan_id)
        if not plan:
            return {"status": "error", "message": "Plan not found"}
        checkpoint = {
            "plan_id": plan_id,
            "goal": plan["goal"],
            "project": plan["project"],
            "status": plan["status"],
            "current_phase_idx": plan["current_phase"],
            "phases_completed": json.loads(plan["phases_completed"] or "[]"),
            "phases_remaining": json.loads(plan["phases_remaining"] or "[]"),
            "artifact_count": len(json.loads(plan["artifacts"] or "[]")),
            "timestamp": time.time(),
            "resume_token": str(uuid.uuid4())[:8],
        }
        # Write to temp file for IDE-side recovery
        cp_path = Path(f"/tmp/nexus-plan-checkpoint-{plan_id}.json")
        cp_path.write_text(json.dumps(checkpoint, indent=2))
        logger.info(f"Checkpoint saved: {cp_path}")
        return checkpoint

    def resume_plan(self, plan_id: int = None, checkpoint_path: str = None) -> Optional[Dict]:
        """Resume a plan from checkpoint or DB state."""
        if checkpoint_path:
            cp = json.loads(Path(checkpoint_path).read_text())
            plan_id = cp["plan_id"]

        plan = self.get_plan(plan_id)
        if not plan:
            return None

        remaining = json.loads(plan["phases_remaining"] or "[]")
        if not remaining and plan["status"] in ("completed",):
            return {"plan_id": plan_id, "status": "completed",
                    "message": "Plan already completed"}

        # Re-activate if paused/planning
        if plan["status"] in ("planning", "paused"):
            self.update_plan_status(plan_id, "active")

        next_phase = self.get_next_phase(plan_id)
        return {
            "plan_id": plan_id,
            "status": "resumed",
            "next_phase": next_phase,
            "progress": {
                "completed": json.loads(plan["phases_completed"] or "[]"),
                "remaining": remaining,
                "total_phases": len(json.loads(plan["phases_json"] or "[]")),
            },
            "checkpoint": self.checkpoint(plan_id),
        }

    # ─── DAG Dependency Resolution ────────────────────────────────────────

    def get_ready_phases(self, plan_id: int) -> List[str]:
        """Get phases whose dependencies are all met (for parallel dispatch)."""
        plan = self.get_plan(plan_id)
        if not plan:
            return []
        phases = json.loads(plan["phases_json"] or "[]")
        completed = json.loads(plan["phases_completed"] or "[]")
        remaining = json.loads(plan["phases_remaining"] or "[]")

        ready = []
        phase_map = {p.get("id"): p for p in phases}
        for phase_id in remaining:
            phase = phase_map.get(phase_id, {})
            deps = phase.get("inputs", []) or phase.get("depends_on", [])
            if all(d in completed for d in deps):
                ready.append(phase_id)
        return ready

    # ─── Stats ────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get plan execution statistics."""
        active = self.conn.execute(
            "SELECT COUNT(*) FROM plans WHERE status='active'"
        ).fetchone()[0]
        completed = self.conn.execute(
            "SELECT COUNT(*) FROM plans WHERE status='completed'"
        ).fetchone()[0]
        failed = self.conn.execute(
            "SELECT COUNT(*) FROM plans WHERE status='failed'"
        ).fetchone()[0]
        total_phases = self.conn.execute(
            "SELECT COUNT(*) FROM execution_log"
        ).fetchone()[0]
        total_subtasks = self.conn.execute(
            "SELECT COUNT(*) FROM tool_traces"
        ).fetchone()[0]

        latest = self.conn.execute(
            """SELECT p.id, p.goal, p.status, p.current_phase, p.updated_at
               FROM plans p ORDER BY p.updated_at DESC LIMIT 5"""
        ).fetchall()

        return {
            "active_plans": active,
            "completed_plans": completed,
            "failed_plans": failed,
            "total_phases_executed": total_phases,
            "total_subtasks_tracked": total_subtasks,
            "latest_plans": [dict(r) for r in latest],
        }

    def list_plans(self, status: str = None, limit: int = 10) -> List[Dict]:
        """List plans, optionally filtered by status."""
        if status:
            rows = self.conn.execute(
                """SELECT id, goal, project, status, current_phase,
                   phases_completed, phases_remaining, created_at, updated_at
                   FROM plans WHERE status=? ORDER BY updated_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT id, goal, project, status, current_phase,
                   phases_completed, phases_remaining, created_at, updated_at
                   FROM plans ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
