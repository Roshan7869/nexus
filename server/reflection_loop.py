#!/usr/bin/env python3
"""
NEXUS Reflection Loop — Self-Refine + Reflexion pattern

Based on:
- Reflexion (2303.11366): verbal reflection + memory retry
- Self-Refine (2303.17651): Generate → Evaluate → Refine loop
- SWE-agent (2405.15793): accept signal determines success

When a tool/route/execution fails:
1. self_reflector.reflect() analyzes failure
2. Selects alternative from candidates
3. Retries with learned context
4. Max 3 attempts
5. Logs fixes to reflection_fixes table for future learning
"""

import json, sqlite3, os, logging, time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / 'nexus/db/nexus.db'
REFLECTION_ENABLED = os.environ.get("NEXUS_REFLECTION", "false").lower() == "true"
MAX_RETRIES = 3


@dataclass
class RetryResult:
    success: bool
    attempts: int
    diagnosis: str
    alternative_used: str
    original_error: str
    final_result: Any = None


class ReflectionLoop:
    """Wraps SelfReflector into a retry loop. Reflexion pattern."""

    def __init__(self):
        self._reflector = None
        self._conn = None

    @property
    def reflector(self):
        if self._reflector is None:
            import sys
            sys.path.insert(0, str(Path.home() / 'nexus/server'))
            from self_reflector import SelfReflector
            self._reflector = SelfReflector()
        return self._reflector

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(DB_PATH), timeout=60)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def retry_on_failure(self, failed_tool: str, error: str, query: str,
                          candidates: List[Dict], execute_fn=None) -> RetryResult:
        """
        Reflexion retry loop. On failure:
        1. reflect() → diagnosis + alternatives
        2. Try each alternative
        3. Log reflection to DB
        4. Return RetryResult

        If execute_fn provided, calls it with each alternative.
        If not, just returns the diagnosis + alternatives for caller to use.
        """
        if not REFLECTION_ENABLED:
            return RetryResult(
                success=False, attempts=0,
                diagnosis="Reflection disabled", alternative_used="",
                original_error=error
            )

        attempts = 0
        last_error = error

        for attempt in range(MAX_RETRIES):
            attempts += 1

            # Step 1: Reflect on failure
            try:
                reflection = self.reflector.reflect(
                    failed_tool=failed_tool,
                    error=last_error,
                    query=query,
                    candidates=candidates
                )
            except Exception as e:
                logger.warning(f"Self-reflector failed: {e}")
                break

            if not reflection.should_retry or not reflection.alternative_tools:
                logger.info(f"Reflection: no alternatives after {attempts} attempts")
                break

            # Step 2: Try alternative
            alternative = reflection.alternative_tools[0] if reflection.alternative_tools else None
            if not alternative:
                break

            # Step 3: Execute with alternative (if executor provided)
            if execute_fn:
                try:
                    result = execute_fn(alternative, query)
                    # Success! Log the fix
                    self._log_fix(failed_tool, alternative, reflection.diagnosis,
                                  reflection.learned_approach, success=True)
                    return RetryResult(
                        success=True, attempts=attempts,
                        diagnosis=reflection.diagnosis,
                        alternative_used=alternative,
                        original_error=error,
                        final_result=result
                    )
                except Exception as alt_error:
                    last_error = str(alt_error)
                    failed_tool = alternative
                    logger.info(f"Reflection attempt {attempts} failed: {alt_error}")
                    continue
            else:
                # No executor - just return the alternatives
                self._log_fix(failed_tool, alternative, reflection.diagnosis,
                              reflection.learned_approach, success=None)
                return RetryResult(
                    success=False, attempts=attempts,
                    diagnosis=reflection.diagnosis,
                    alternative_used=alternative,
                    original_error=error
                )

        # All retries exhausted
        self._log_fix(failed_tool, "", "Max retries exhausted", "", success=False)
        return RetryResult(
            success=False, attempts=attempts,
            diagnosis="Max retries exhausted",
            alternative_used="",
            original_error=error
        )

    def _log_fix(self, failed_tool: str, alternative: str, diagnosis: str,
                 approach: str, success: Optional[bool]):
        """Log reflection result for future learning."""
        try:
            self.conn.execute(
                """INSERT INTO reflection_failures
                   (task, resource_name, expected, actual, root_cause, fix_attempted, fix_successful)
                   VALUES (?,?,?,?,?,?,?)""",
                (diagnosis[:200], failed_tool, alternative, "",
                 diagnosis[:500], approach[:200],
                 1 if success else 0 if success is False else None)
            )
            if success:
                self.conn.execute(
                    """INSERT INTO reflection_fixes
                       (resource_id, failure_summary, fix_description, times_applied, times_successful)
                       VALUES (?,?,?,?,?)""",
                    (0, f"{failed_tool} failed", f"Use {alternative} instead: {approach}",
                     1, 1)
                )
            elif success is False and alternative:
                self.conn.execute(
                    """INSERT INTO reflection_fixes
                       (resource_id, failure_summary, fix_description, times_applied, times_successful)
                       VALUES (?,?,?,?,?)""",
                    (0, f"{failed_tool} failed", f"Attempted {alternative} (also failed)", 1, 0)
                )
            self.conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log reflection: {e}")

    def get_fix_history(self, resource_name: str = None) -> List[Dict]:
        """Retrieve reflection fix history for a resource."""
        try:
            if resource_name:
                rows = self.conn.execute(
                    "SELECT * FROM reflection_fixes WHERE failure_summary LIKE ? ORDER BY id DESC LIMIT 20",
                    (f"%{resource_name}%",)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM reflection_fixes ORDER BY id DESC LIMIT 20"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_failure_stats(self) -> Dict:
        """Get reflection statistics."""
        try:
            total = self.conn.execute("SELECT COUNT(*) FROM reflection_failures").fetchone()[0]
            fixed = self.conn.execute(
                "SELECT COUNT(*) FROM reflection_failures WHERE fix_successful=1"
            ).fetchone()[0]
            fixes = self.conn.execute("SELECT COUNT(*) FROM reflection_fixes").fetchone()[0]
            return {
                "total_failures": total,
                "successful_fixes": fixed,
                "fix_rate": round(fixed / total * 100, 1) if total > 0 else 0,
                "total_fix_records": fixes,
                "enabled": REFLECTION_ENABLED,
            }
        except Exception:
            return {"enabled": REFLECTION_ENABLED, "error": "db unavailable"}


# ── Convenience wrapper for router integration ────────────────────────────

def reflect_and_retry(failed_tool: str, error: str, query: str,
                       candidates: List[Dict], execute_fn=None) -> RetryResult:
    """One-call convenience for the reflection loop."""
    loop = ReflectionLoop()
    result = loop.retry_on_failure(failed_tool, error, query, candidates, execute_fn)
    loop.close()
    return result