"""Phase 4 — Anti-Pattern → Reflection Bridge.

Feeds Coach anti-pattern detections into the NEXUS reflection pipeline
so that repeated anti-patterns trigger self-reflection and alternative
resource recommendations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from server.coach.bridge import get_bridge
from server.coach.schema import get_connection

logger = logging.getLogger(__name__)


def sync_antipatterns_to_reflections(
    project: str = "default",
    min_severity: str = "medium",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Query ``coach_antipatterns`` for high-severity items and insert
    matching rows into ``reflection_failures``.

    Args:
        project: Scope to a specific project.
        min_severity: Minimum severity to sync (``"high"``, ``"medium"``,
                      or ``"low"``).
        dry_run: If True, only count and return preview — no writes.

    Returns:
        Dict with ``synced``, ``skipped``, ``total_antipatterns``,
        and optional ``error``.
    """
    bridge = get_bridge()
    patterns = bridge.get_antipatterns(project=project, severity=min_severity)

    if not patterns:
        return {
            "synced": 0,
            "skipped": 0,
            "total_antipatterns": 0,
            "message": "No anti-patterns found at or above severity threshold.",
        }

    severity_order = {"high": 0, "medium": 1, "low": 2}
    synced = 0
    skipped = 0
    inserted: list[dict[str, Any]] = []

    try:
        conn = get_connection()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for ap in patterns:
            resource_name = f"coach:{ap.rule_id}"
            task = f"[Coaching] {ap.rule_name}: {ap.description[:200]}"

            # Check for existing entry to avoid duplicates
            existing = conn.execute(
                "SELECT id FROM reflection_failures WHERE task=? AND resource_name=?",
                (task, resource_name),
            ).fetchone()

            if existing:
                skipped += 1
                continue

            if dry_run:
                inserted.append(
                    {
                        "task": task,
                        "resource_name": resource_name,
                        "root_cause": f"anti-pattern:{ap.group_name}:{ap.severity}",
                    }
                )
                synced += 1
                continue

            # Classify the error type from the anti-pattern group
            error_type = _antipattern_to_error_type(ap.group_name)

            conn.execute(
                """INSERT INTO reflection_failures
                   (task, resource_name, resource_id, expected, actual, root_cause, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task,
                    resource_name,
                    0,
                    "best practice",
                    ap.description[:300],
                    f"anti-pattern:{ap.group_name}:{ap.severity}:{error_type}",
                    now_iso,
                ),
            )
            synced += 1
            inserted.append(
                {
                    "task": task,
                    "resource_name": resource_name,
                    "root_cause": f"anti-pattern:{ap.group_name}:{ap.severity}",
                }
            )

        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("sync_antipatterns_to_reflections failed: %s", exc)
        return {
            "synced": synced,
            "skipped": skipped,
            "total_antipatterns": len(patterns),
            "error": str(exc),
        }

    return {
        "synced": synced,
        "skipped": skipped,
        "total_antipatterns": len(patterns),
        "reflections": inserted if dry_run else None,
        "message": (
            f"Synced {synced} anti-pattern(s) to reflection_failures "
            f"({skipped} already existed)."
        ),
    }


def _antipattern_to_error_type(group_name: str) -> str:
    """Map Coach practice group to a NEXUS error classification."""
    mapping = {
        "prompt-quality": "general_failure",
        "session-hygiene": "timeout",
        "code-review": "type_mismatch",
        "tool-mastery": "missing_dependency",
        "context-management": "null_reference",
    }
    return mapping.get(group_name, "general_failure")
