"""NEXUS v3 Hallucination Gate — after_model verification layer.
Blocks predicted tools that don't exist, are inactive, or have unmet preconditions.
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path
import sqlite3, time
from typing import Optional
from collections.abc import Callable
from precondition import GLOBAL_GATES, StateVariable

DB_PATH = str(_resolve_db_path())
HALLUCINATE_SCORE_THRESHOLD = 0.45  # minimum score to pass

class HallucinationGate:
    """Verify a predicted tool call before execution.

    The gate sits at the end of the routing pipeline (Phase 7).
    It takes (tool_name, score) and returns:
      (allowed: bool, block_reason: str | None, alternatives: list[dict])
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def verify(self, tool_name: str, score: float, *,
               task: str = "", project: str = "default") -> tuple[bool, str | None, list[dict]]:
        """Return (allowed, block_reason, alternatives)."""
        # 1. Existence check
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, resource_type, status, tier, token_count, "
                "       times_triggered, times_correct, times_wrong, description, file_path "
                "FROM resources WHERE name=?" , 
                (tool_name,),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            # Fallthrough — return nearest matches
            alternatives = self._nearest_matches(tool_name, limit=5)
            return False, f"Tool '{tool_name}' does not exist in the registry." , alternatives

        status = row["status"]
        times_triggered = row["times_triggered"] or 0
        times_correct = row["times_correct"] or 0
        if times_triggered > 0:
            success_rate = times_correct / times_triggered
        else:
            success_rate = 1.0

        # 2. Active check
        if status != "active":
            alternatives = self._nearest_matches(tool_name, limit=5)
            return False, f"Tool '{tool_name}' is not active (status={status})." , alternatives

        # 3. Score threshold check
        if score < HALLUCINATE_SCORE_THRESHOLD:
            alternatives = self._nearest_matches(tool_name, limit=5)
            return False, f"Tool '{tool_name}' score {score:.3f} below threshold {HALLUCINATE_SCORE_THRESHOLD}." , alternatives

        # 4. State precondition check (look up tool-specific gate if any)
        state_gate = GLOBAL_GATES.check(tool_name)
        if not state_gate[0]:
            return False, state_gate[1], []

        return True, None, []

    def _nearest_matches(self, tool_name: str, limit: int = 5) -> list[dict]:
        """Find nearest-name matches if a tool doesn't exist."""
        alternatives = []
        conn = self._conn()
        try:
            cur = conn.cursor()
            # Substring match
            cur.execute(
                "SELECT name, resource_type, description, status, tier "
                "FROM resources WHERE name LIKE ? OR name LIKE ? OR name LIKE ? "
                "ORDER BY times_triggered DESC LIMIT ?",
                (f"%{tool_name}%", f"%{tool_name.split()[0]}%", f"%{tool_name.split()[-1]}%", limit),
            )
            for row in cur.fetchall():
                alternatives.append({
                    "name": row[0],
                    "resource_type": row[1],
                    "description": row[2],
                    "status": row[3],
                    "tier": row[4],
                })
        finally:
            conn.close()
        return alternatives

    def batch_verify(self, candidates: list[tuple[str, float]], *,
                     task: str = "", project: str = "default") -> list[tuple[str, bool, str | None, list[dict]]]:
        """Verify multiple candidates. Returns list of (name, allowed, reason, alternatives)."""
        results = []
        for name, score in candidates:
            allowed, reason, alternatives = self.verify(name, score, task=task, project=project)
            results.append((name, allowed, reason, alternatives))
        return results


def get_hallucination_gate(db_path: str = DB_PATH) -> HallucinationGate:
    if not hasattr(get_hallucination_gate, "_instance"):
        get_hallucination_gate._instance = HallucinationGate(db_path)
    return get_hallucination_gate._instance