"""Phase 5 — Practice Score → Thompson Beliefs.

Maps Coach practice scores into the NEXUS ``model_beliefs`` table so that
routing can prefer resources aligned with the user's improving practice areas.

Score injection uses a capped ``score_delta ∈ [0, 0.3]`` to avoid distorting
the Beta distribution.
"""

from __future__ import annotations

import logging
from typing import Any

from server.coach.bridge import get_bridge
from server.coach.schema import get_connection

logger = logging.getLogger(__name__)

# ── Mapping: practice score dimension → model_beliefs task_type ──────────
DIMENSION_TO_TASK_TYPE: dict[str, str] = {
    "prompt_quality": "coding",
    "session_hygiene": "general",
    "code_review": "reasoning",
    "tool_mastery": "devops",
    "context_management": "frontend",
}

# Cap the score-derived reward so one score reading can never dominate
# the Beta(alpha, beta) posterior.
MAX_SCORE_DELTA = 0.3


def sync_scores_to_beliefs(
    project: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Read the latest Coach practice scores and inject them as reward
    signals into the ``model_beliefs`` table.

    For each practice dimension:

    1. The dimension is mapped to a ``task_type`` (e.g. ``prompt_quality``
       → ``"coding"``).
    2. A provider entry of ``"coach"`` with model ``"coach:<dimension>"``
       receives ``alpha += score_delta``.
    3. ``score_delta = min(score / 100 * 0.3, MAX_SCORE_DELTA)`` — a
       score of 100 yields the maximum boost of 0.3; a score of 50
       yields 0.15.

    Args:
        project: Project scope for practice scores.
        dry_run: If True, return the planned delta without writing.

    Returns:
        Dict with ``updates``, ``total_delta``, and optional ``error``.
    """
    bridge = get_bridge()
    scores = bridge.get_practice_scores(project)

    if not scores:
        return {
            "updates": 0,
            "total_delta": 0.0,
            "message": "No practice scores available.",
        }

    latest = scores[0]
    updates: list[dict[str, Any]] = []
    total_delta = 0.0

    try:
        conn = get_connection()

        for dimension, task_type in DIMENSION_TO_TASK_TYPE.items():
            raw_score = getattr(latest, dimension, 0.0)
            score_delta = min(raw_score / 100.0 * MAX_SCORE_DELTA, MAX_SCORE_DELTA)

            if score_delta <= 0:
                continue

            total_delta += score_delta
            provider = "coach"
            model = f"coach:{dimension}"

            updates.append(
                {
                    "provider": provider,
                    "model": model,
                    "task_type": task_type,
                    "dimension": dimension,
                    "raw_score": raw_score,
                    "score_delta": round(score_delta, 4),
                }
            )

            if not dry_run:
                _ensure_belief(conn, provider, model, task_type)

                conn.execute(
                    """UPDATE model_beliefs
                       SET alpha = alpha + ?,
                           total_requests = total_requests + 1,
                           total_reward = total_reward + ?,
                           avg_reward = total_reward / total_requests,
                           last_updated = datetime('now')
                       WHERE provider = ? AND model = ? AND task_type = ?""",
                    (score_delta, score_delta, provider, model, task_type),
                )

        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("sync_scores_to_beliefs failed: %s", exc)
        return {
            "updates": len(updates),
            "total_delta": round(total_delta, 4),
            "error": str(exc),
        }

    return {
        "updates": len(updates),
        "total_delta": round(total_delta, 4),
        "details": updates if dry_run else None,
        "message": (
            f"Injected {len(updates)} belief update(s) "
            f"(total delta={total_delta:.3f}) from Coach practice scores."
        ),
    }


def _ensure_belief(conn, provider: str, model: str, task_type: str) -> None:
    """Make sure a belief row exists (mirrors learning.py)."""
    existing = conn.execute(
        "SELECT id FROM model_beliefs WHERE provider=? AND model=? AND task_type=?",
        (provider, model, task_type),
    ).fetchone()
    if not existing:
        conn.execute(
            """INSERT OR IGNORE INTO model_beliefs
               (provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward)
               VALUES (?, ?, ?, 1.0, 1.0, 0, 0.0, 0.0)""",
            (provider, model, task_type),
        )
        conn.commit()
