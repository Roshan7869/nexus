"""Thompson Sampling over per-resource Beta beliefs for the NEXUS RL loop.

For each candidate resource and the current (cluster_id, task_intent) context:
    α = 1 + Σ decay_i  for outcomes == 'correct'
    β = 1 + Σ decay_i * w_i  where w_i = 1.0 for 'wrong', 0.5 for 'partial'
    decay_i = exp(-Δdays_i / 30)    (half-life ≈ 21 days)

We sample Beta(α, β) once per candidate and return the top-k by sampled value.
On cold start (outcome_log has fewer than ``COLD_START_MIN`` total rows) the
input order is returned unchanged — FTS5 + RRF decide alone, so the system is
useful on day 1 with no feedback. (Plan §5.3.)

This module is the *policy* in the ROS-style triad; the *reward signal* is
``outcome_log`` (server.rl.outcome_log) and the *costmap* is the
historical wrong/partial counts.
"""
from __future__ import annotations

import math
import sqlite3
import time
from typing import Sequence

# numpy is imported lazily inside sample()/sampled_scores() so the cold-start
# FTS-only search path stays zero-ML (no numpy, no sentence_transformers).

COLD_START_MIN = 20          # below this many total outcomes, no re-ranking
DECAY_DAYS = 30.0            # decay time constant (days)
SECONDS_PER_DAY = 86400.0


class ThompsonSampler:
    """Thompson sampler over outcome_log Beta beliefs."""

    def __init__(self, db_path: str, cold_start_min: int = COLD_START_MIN) -> None:
        self.db_path = db_path
        self.cold_start_min = cold_start_min

    # ---- internal ------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _total_outcomes(self, con: sqlite3.Connection) -> int:
        try:
            return int(con.execute("SELECT COUNT(*) FROM outcome_log").fetchone()[0])
        except sqlite3.Error:
            return 0

    def _context_stats(
        self, con: sqlite3.Connection, cluster_id, task_intent: str
    ) -> dict[int, dict[str, float]]:
        """Return {resource_id: {alpha, beta}} for the (cluster, intent) context.

        Applies exponential time decay to each historical outcome.
        """
        now = time.time()
        rows = con.execute(
            "SELECT resource_id, outcome, created_at FROM outcome_log "
            "WHERE cluster_id = ? AND task_intent = ?",
            (cluster_id, task_intent),
        ).fetchall()
        stats: dict[int, dict[str, float]] = {}
        for r in rows:
            rid = int(r["resource_id"])
            outcome = r["outcome"]
            created = float(r["created_at"] or now)
            age_days = max(0.0, (now - created) / SECONDS_PER_DAY)
            decay = math.exp(-age_days / DECAY_DAYS)
            s = stats.setdefault(rid, {"correct": 0.0, "wrong": 0.0, "partial": 0.0})
            if outcome in s:
                s[outcome] += decay
        # convert to alpha/beta
        out: dict[int, dict[str, float]] = {}
        for rid, s in stats.items():
            alpha = 1.0 + s["correct"]
            beta = 1.0 + s["wrong"] + 0.5 * s["partial"]
            out[rid] = {"alpha": alpha, "beta": beta}
        return out

    # ---- public API ----------------------------------------------------

    def sample(
        self,
        resource_ids: Sequence[int],
        cluster_id,
        task_intent: str,
        k: int = 10,
        rng: np.random.Generator | None = None,
    ) -> list[int]:
        """Re-rank ``resource_ids`` by Thompson-sampled posterior, return top-k.

        Cold start: if outcome_log has < ``cold_start_min`` total rows, the
        input order is returned (truncated to k) unchanged.

        Args:
            resource_ids: candidate resource ids (any order; FTS/RRF output).
            cluster_id: cluster name/id for the context (matches outcome_log.cluster_id).
            task_intent: the task intent string for the context.
            k: number to return.
            rng: optional numpy Generator (for deterministic tests).

        Returns:
            list of resource_ids, Thompson-ordered, length min(k, len(input)).
        """
        ids = [int(x) for x in resource_ids]
        if not ids:
            return []
        k = max(1, k)

        con = self._conn()
        try:
            if self._total_outcomes(con) < self.cold_start_min:
                return ids[:k]
            stats = self._context_stats(con, cluster_id, task_intent)
        finally:
            con.close()

        import numpy as np
        g = rng or np.random.default_rng()
        scored: list[tuple[int, float]] = []
        for rid in ids:
            s = stats.get(rid)
            if s is None:
                alpha, beta = 1.0, 1.0      # uninformed prior Beta(1,1)
            else:
                alpha, beta = s["alpha"], s["beta"]
            sampled = float(g.beta(alpha, beta))
            scored.append((rid, sampled))
        scored.sort(key=lambda t: t[1], reverse=True)
        return [rid for rid, _ in scored[:k]]

    def sampled_scores(
        self,
        resource_ids: Sequence[int],
        cluster_id,
        task_intent: str,
        rng: np.random.Generator | None = None,
    ) -> dict[int, float]:
        """Return {resource_id: sampled_thompson_score} for blending in fusion.

        Returns an empty dict on cold start (signals the caller to use RRF
        alone). Used by ``router_v2.fusion.rerank_with_feedback``.
        """
        ids = [int(x) for x in resource_ids]
        if not ids:
            return {}
        con = self._conn()
        try:
            if self._total_outcomes(con) < self.cold_start_min:
                return {}
            stats = self._context_stats(con, cluster_id, task_intent)
        finally:
            con.close()
        import numpy as np
        g = rng or np.random.default_rng()
        out: dict[int, float] = {}
        for rid in ids:
            s = stats.get(rid)
            alpha = s["alpha"] if s else 1.0
            beta = s["beta"] if s else 1.0
            out[rid] = float(g.beta(alpha, beta))
        return out