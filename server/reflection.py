"""NEXUS v3 Reflection System - structured failure logging + historical fix matching."""
from __future__ import annotations
import sqlite3, json, time, os, shutil
from typing import Optional
from dataclasses import dataclass

# ── DB — consolidated from server/db.py (single source of truth)
from server.db import get_db_path

FAILURE_TABLE = "reflection_failures"
FIX_TABLE = "reflection_fixes"

class ReflectionEngine:
    """Stores structured failure context and suggests historical fixes."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(get_db_path())
        self.init_db()

    def _resolve_db_path(self, db_path: str) -> str:
        env_path = os.environ.get("NEXUS_DB_PATH")
        if env_path:
            return env_path
        if db_path and os.access(db_path, os.W_OK):
            return db_path
        tmp_db = "/tmp/nexus.db"
        if not os.path.exists(tmp_db) and os.path.exists(db_path):
            try:
                shutil.copy2(db_path, tmp_db)
            except Exception:
                return db_path
        return tmp_db if os.path.exists(tmp_db) else db_path

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self._conn()
        try:
            c = conn.cursor()
            # Failures table
            c.execute(f"""
                CREATE TABLE IF NOT EXISTS {FAILURE_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    resource_name TEXT NOT NULL,
                    resource_id INTEGER,
                    expected TEXT,
                    actual TEXT,
                    root_cause TEXT,
                    fix_attempted TEXT,
                    fix_successful INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Fixes table (indexed for quick lookup)
            c.execute(f"""
                CREATE TABLE IF NOT EXISTS {FIX_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id INTEGER,
                    failure_summary TEXT,
                    fix_description TEXT,
                    times_applied INTEGER DEFAULT 0,
                    times_successful INTEGER DEFAULT 0,
                    last_applied_at TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Index for quick lookup
            c.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_reflection_resource ON {FAILURE_TABLE}(resource_id)
            """)
            conn.commit()
        finally:
            conn.close()

    def log_failure(self, task: str, resource_name: str, resource_id: int,
                    expected: str, actual: str, root_cause: str) -> int:
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute(f"""
                INSERT INTO {FAILURE_TABLE}
                (task, resource_name, resource_id, expected, actual, root_cause)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task, resource_name, resource_id, expected, actual, root_cause))
            conn.commit()
            return c.lastrowid
        finally:
            conn.close()

    def log_fix(self, resource_id: int, failure_summary: str,
                fix_description: str) -> int:
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute(f"""
                INSERT INTO {FIX_TABLE} (resource_id, failure_summary, fix_description)
                VALUES (?, ?, ?)
            """, (resource_id, failure_summary, fix_description))
            conn.commit()
            return c.lastrowid
        finally:
            conn.close()

    def mark_outcome(self, resource_id: int, success: bool):
        """After routing, update times_correct / times_wrong."""
        conn = self._conn()
        try:
            c = conn.cursor()
            c.execute("SELECT times_triggered, times_correct, times_wrong FROM resources WHERE id= ?", (resource_id,))
            row = c.fetchone()
            if not row:
                return
            triggered = (row[0] or 0) + 1
            correct = (row[1] or 0) + (1 if success else 0)
            wrong = (row[2] or 0) + (0 if success else 1)
            c.execute("""
                UPDATE resources
                SET times_triggered = ?, times_correct = ?, times_wrong = ?
                WHERE id= ?
            """, (triggered, correct, wrong, resource_id))
            conn.commit()
        finally:
            conn.close()

    def get_historical_fix(self, task: str, resource_id: int,
                          similarity_threshold: float = 0.8) -> Optional[dict]:
        """Find a fix from history if the failure pattern matches."""
        conn = self._conn()
        try:
            c = conn.cursor()
            # Get last 5 fixes for this resource
            c.execute(f"""
                SELECT fix_description, times_successful, times_applied
                FROM {FIX_TABLE}
                WHERE resource_id = ?
                ORDER BY last_applied_at DESC
                LIMIT 5
            """, (resource_id,))
            fixes = c.fetchall()
            if not fixes:
                return None
            # Simple heuristic: if any fix has > 0 success, return the best one
            best = max(fixes, key=lambda f: f[1])
            if best[1] > 0:
                return {
                    "fix": best[0],
                    "confidence": min(best[1] / max(best[2], 1), 1.0),
                    "times_successful": best[1],
                }
            return None
        finally:
            conn.close()

    def get_failure_stats(self, resource_id: Optional[int] = None) -> dict:
        conn = self._conn()
        try:
            c = conn.cursor()
            if resource_id:
                c.execute(f"""
                    SELECT COUNT(*), AVG(fix_successful)
                    FROM {FAILURE_TABLE}
                    WHERE resource_id = ?
                """, (resource_id,))
            else:
                c.execute(f"""
                    SELECT COUNT(*), AVG(fix_successful)
                    FROM {FAILURE_TABLE}
                """)
            total, success_rate = c.fetchone()
            return {
                "total_failures": total or 0,
                "avg_success_rate": round(success_rate or 0, 2),
            }
        finally:
            conn.close()

# Module-level
REFLECTION = ReflectionEngine()

def log_failure(task: str, resource_name: str, resource_id: int, expected: str, actual: str, root_cause: str) -> int:
    return REFLECTION.log_failure(task, resource_name, resource_id, expected, actual, root_cause)

def log_fix(resource_id: int, failure_summary: str, fix_description: str) -> int:
    return REFLECTION.log_fix(resource_id, failure_summary, fix_description)

def mark_outcome(resource_id: int, success: bool):
    REFLECTION.mark_outcome(resource_id, success)

def get_historical_fix(task: str, resource_id: int) -> Optional[dict]:
    return REFLECTION.get_historical_fix(task, resource_id)
