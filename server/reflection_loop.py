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


def _resolve_db_path() -> Path:
    """Resolve NEXUS DB path, respecting env overrides (matches reflection.py).

    Priority: NEXUS_BROKER_DB_PATH > NEXUS_DB_PATH > ~/nexus/db/nexus.db.
    """
    for var in ("NEXUS_BROKER_DB_PATH", "NEXUS_DB_PATH"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    return Path.home() / "nexus" / "db" / "nexus.db"


DB_PATH = _resolve_db_path()
REFLECTION_ENABLED = os.environ.get("NEXUS_REFLECTION", "false").lower() == "true"
MAX_RETRIES = 3  # Reflexion (Shinn et al. NeurIPS 2023) caps trials at 2-3.


def _max_retries() -> int:
    """Env-tunable retry cap (NEXUS_REFLECTION_MAX_RETRIES), HARD-CAPPED at 3
    per Reflexion: more trials yield diminishing returns and risk regression to
    the failing tool. Default 3."""
    try:
        n = int(os.environ.get("NEXUS_REFLECTION_MAX_RETRIES", "3"))
    except ValueError:
        n = 3
    return max(1, min(n, MAX_RETRIES))


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
            self._conn = sqlite3.connect(str(DB_PATH), timeout=60, check_same_thread=False)
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

        for attempt in range(_max_retries()):
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
                self._log_reflection(failed_tool, query, reflection.diagnosis,
                                     last_error, self._cluster_of(failed_tool),
                                     reflection.learned_approach,
                                     len(reflection.alternative_tools or []), False)
                break

            # Step 2: Try alternative — cluster-affinity-weighted selection (P4-C)
            alternative = self._select_alternative(reflection.alternative_tools, failed_tool)
            if not alternative:
                break

            # Step 3: Execute with alternative (if executor provided)
            if execute_fn:
                try:
                    result = execute_fn(alternative, query)
                    # Success! Log the fix
                    self._log_fix(failed_tool, alternative, reflection.diagnosis,
                                  reflection.learned_approach, success=True)
                    self._log_reflection(failed_tool, query, reflection.diagnosis,
                                         last_error, self._cluster_of(failed_tool),
                                         reflection.learned_approach,
                                         len(reflection.alternative_tools or []),
                                         True)
                    # Feed the successful alternative back to TS (P4-B).
                    self._feed_ts(alternative, "correct", query)
                    return RetryResult(
                        success=True, attempts=attempts,
                        diagnosis=reflection.diagnosis,
                        alternative_used=alternative,
                        original_error=error,
                        final_result=result
                    )
                except Exception as alt_error:
                    last_error = str(alt_error)
                    self._log_reflection(failed_tool, query, reflection.diagnosis,
                                         last_error, self._cluster_of(failed_tool),
                                         reflection.learned_approach,
                                         len(reflection.alternative_tools or []),
                                         False)
                    # Feed the failed alternative back to TS (P4-B).
                    self._feed_ts(alternative, "wrong", query)
                    failed_tool = alternative
                    logger.info(f"Reflection attempt {attempts} failed: {alt_error}")
                    continue
            else:
                # No executor - just return the alternatives
                self._log_fix(failed_tool, alternative, reflection.diagnosis,
                              reflection.learned_approach, success=None)
                self._log_reflection(failed_tool, query, reflection.diagnosis,
                                     last_error, self._cluster_of(failed_tool),
                                     reflection.learned_approach,
                                     len(reflection.alternative_tools or []), None)
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

    def _select_alternative(self, alternatives: List[str],
                            failed_tool: str) -> Optional[str]:
        """Pick the best alternative, weighted by learned cluster affinity
        (P4-C). Alternatives in higher-affinity clusters are preferred, with a
        small bonus for same-cluster substitutes (likelier functional stand-ins
        for the failed tool — same domain, same tool surface). Falls back to
        ``alternatives[0]`` when affinity data is unavailable or all clusters are
        unknown, preserving the prior first-listed behavior."""
        if not alternatives:
            return None
        try:
            from server.learning import get_cluster_affinity_scores
            aff = get_cluster_affinity_scores(self.conn)
            failed_cluster = self._cluster_of(failed_tool)
            best, best_score = None, -1.0
            for alt in alternatives:
                ac = self._cluster_of(alt)
                score = aff.get(ac, 0.5) if ac else 0.5
                if ac and failed_cluster and ac == failed_cluster:
                    score += 0.1  # same-cluster substitute bonus
                if score > best_score:
                    best, best_score = alt, score
            return best if best is not None else alternatives[0]
        except Exception:
            return alternatives[0]

    def _feed_ts(self, skill_name: str, outcome: str, task_summary: str):
        """Feed an alternative's retry outcome back to Thompson Sampling (P4-B).
        On a successful retry the alternative SUCCEEDED (outcome='correct'); on a
        failed retry it FAILED ('wrong'). Updates the TS Beta posterior directly
        via ``_update_thompson_belief`` (and the resource counters) so future
        routing up-weights alternatives that resolved a failure and down-weights
        ones that also failed — closing the RL loop the reflection loop discovers.

        Calls the DB layer directly (NOT ``record_outcome``) to avoid re-entering
        the reflection layer (``record_outcome`` → ``_trigger_reflection`` would
        double-log a reflection_failures row for the same failure). Only the
        ALTERNATIVES' outcomes are fed here; the original tool's failure is the
        caller's responsibility (recorded via ``record_outcome`` before the loop
        is invoked), so there is no double-count."""
        try:
            from server.learning import _update_thompson_belief
            _update_thompson_belief(self.conn, skill_name, outcome, task_summary, quality=1.0)
            if outcome == "correct":
                self.conn.execute(
                    "UPDATE resources SET times_correct = times_correct + 1.0, "
                    "times_triggered = times_triggered + 1, last_used_at = datetime('now') "
                    "WHERE name = ?", (skill_name,))
            elif outcome == "wrong":
                self.conn.execute(
                    "UPDATE resources SET times_wrong = times_wrong + 1.0, "
                    "times_triggered = times_triggered + 1, last_used_at = datetime('now') "
                    "WHERE name = ?", (skill_name,))
            self.conn.commit()
        except Exception as e:
            logger.warning(f"Failed to feed TS from reflection: {e}")

    def _cluster_of(self, tool_name: str) -> Optional[str]:
        """Look up the cluster for a resource/tool name (best-effort; the
        failed_tool may not be a resource row, in which case return None)."""
        try:
            row = self.conn.execute(
                "SELECT cluster FROM resources WHERE name = ?", (tool_name,)
            ).fetchone()
            return row["cluster"] if row else None
        except Exception:
            return None

    def _log_reflection(self, failed_tool: str, query: str, diagnosis: str,
                        error: str, cluster: Optional[str], suggestion: str,
                        alternative_count: int, retry_success: Optional[bool]):
        """MEMORIZE step (Reflexion, Shinn et al. NeurIPS 2023): persist the
        verbal reflection to ``reflection_log`` so future trials can retrieve
        prior lessons — the episodic buffer the Self-Reflector reuses. Prior to
        P4-A this was never written (0 rows), so the loop never closed: each
        failure was re-diagnosed from scratch and the 2.1% close rate stagnated.
        Only called from within the enabled retry loop, so no flag-off impact."""
        try:
            self.conn.execute(
                """INSERT INTO reflection_log
                   (failed_skill, task_summary, root_cause, error_type, cluster,
                    suggestion, alternative_count, auto_retry, retry_success,
                    created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (failed_tool, (query or "")[:200], (diagnosis or "")[:500],
                 (error or "")[:200], cluster, (suggestion or "")[:500],
                 int(alternative_count or 0), 1,
                 None if retry_success is None else (1 if retry_success else 0))
            )
            self.conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log reflection_log: {e}")

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