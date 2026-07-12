"""NEXUS resource quality validator.

Implements PERF-2: detect and purge zero-token / empty-body resources that
pollute NEXUS centroids and classification. Provides a 3-gate validation
pipeline plus Z-score anomaly detection.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _iso_embedder() -> Callable[[str], Any] | None:
    """Return IsoEmbedder.encode if available, otherwise None."""
    try:
        from server.iso_embedder import IsoEmbedder
        return IsoEmbedder.get().encode
    except Exception:
        return None


@dataclass(frozen=True)
class ResourceQualityContract:
    """Rules defining a minimum-quality NEXUS resource.

    Attributes:
        min_tokens: Minimum acceptable token_count (inclusive). Default 1.
        require_non_empty_body: If True, body_text must be a non-empty string.
        z_score_threshold: Lower bound for flagging token_count anomalies.
            Resources with Z-score below this value are flagged.
        embed_timeout_seconds: Optional timeout for embed calls (not enforced
            by default implementation; callers may wrap embedders).
    """

    min_tokens: int = 1
    require_non_empty_body: bool = True
    z_score_threshold: float = -2.0
    embed_timeout_seconds: float | None = None

    def token_count_is_valid(self, token_count: Any) -> bool:
        try:
            return int(token_count) >= self.min_tokens
        except (TypeError, ValueError):
            return False

    def body_is_non_empty(self, body: Any) -> bool:
        return isinstance(body, str) and len(body.strip()) > 0


def _try_embed(text: str, embedder: Callable[[str], Any] | None) -> tuple[bool, str | None]:
    """Attempt to embed ``text``. Returns (success, error_message)."""
    if embedder is None:
        return True, None
    try:
        embedder(text)
        return True, None
    except Exception as exc:  # pragma: no cover - embed failures are external
        return False, str(exc)


def validate_resources(
    resources: list[dict],
    contract: ResourceQualityContract | None = None,
    embedder: Callable[[str], Any] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Apply 3 quality gates to every resource.

    Gates:
      1. ``token_count`` >= ``contract.min_tokens`` (default > 0).
      2. ``body_text`` is a non-empty string (if required).
      3. The body can be embedded successfully (if ``embedder`` is supplied).

    Args:
        resources: List of resource dicts. Expected keys: ``id``, ``name``,
            ``body_text``, ``token_count``.
        contract: Optional quality rules. Defaults to ``ResourceQualityContract()``.
        embedder: Optional callable ``text -> embedding``. If None, the embed
            gate is skipped. Production callers should pass a real embedder;
            tests can pass a no-op.

    Returns:
        ``(valid, invalid)`` where each resource in ``invalid`` carries an
        extra ``__violations`` key listing the failed gate messages.
    """
    contract = contract or ResourceQualityContract()
    valid: list[dict] = []
    invalid: list[dict] = []

    for resource in resources:
        violations: list[str] = []
        body = resource.get("body_text") or ""
        tokens = resource.get("token_count", 0)

        if not contract.token_count_is_valid(tokens):
            violations.append(
                f"token_count={tokens!r} below min_tokens={contract.min_tokens}"
            )

        if contract.require_non_empty_body and not contract.body_is_non_empty(body):
            violations.append("body_text empty or missing")

        if contract.require_non_empty_body and contract.body_is_non_empty(body):
            success, err = _try_embed(body, embedder)
            if not success:
                violations.append(f"embed failed: {err}")

        annotated = dict(resource)
        annotated["__violations"] = violations
        if violations:
            invalid.append(annotated)
        else:
            valid.append(annotated)

    return valid, invalid


def flag_anomalies(
    resources: list[dict],
    contract: ResourceQualityContract | None = None,
) -> list[dict]:
    """Flag resources whose ``token_count`` Z-score is below threshold.

    Uses population standard deviation so that a single extreme outlier does
    not hide a cluster of low-token resources. Requires at least two resources
    with non-zero standard deviation.

    Args:
        resources: List of resource dicts.
        contract: Optional quality rules. ``z_score_threshold`` defaults to -2.0
            (mean - 2*sd).

    Returns:
        Copies of flagged resources with ``__z_score`` and ``__anomaly`` keys.
    """
    contract = contract or ResourceQualityContract()
    if len(resources) < 2:
        return []

    try:
        token_counts = [int(r.get("token_count", 0) or 0) for r in resources]
    except (TypeError, ValueError):
        return []

    mean = statistics.mean(token_counts)
    std = statistics.pstdev(token_counts)
    if std == 0:
        return []

    flagged: list[dict] = []
    for resource, tc in zip(resources, token_counts):
        z = (tc - mean) / std
        if z < contract.z_score_threshold:
            annotated = dict(resource)
            annotated["__z_score"] = round(z, 4)
            annotated["__anomaly"] = True
            flagged.append(annotated)
    return flagged


def purge_zero_token_and_empty(
    conn: sqlite3.Connection,
    contract: ResourceQualityContract | None = None,
    embedder: Callable[[str], Any] | None = None,
    delete: bool = False,
) -> dict[str, Any]:
    """Detect and purge resources that fail the quality contract.

    By default, invalid resources are marked ``status='purged'`` rather than
    physically deleted. This preserves referential integrity with tables such
    as ``outcome_log`` and leaves an audit trail. Pass ``delete=True`` to
    physically remove rows.

    Args:
        conn: SQLite connection to the NEXUS database.
        contract: Optional quality rules.
        embedder: Optional embedder for the third gate.
        delete: If True, ``DELETE`` invalid rows; otherwise ``UPDATE`` their
            status to ``'purged'``.

    Returns:
        Summary dict with keys:
          - ``purged`` or ``deleted``: count of affected rows
          - ``invalid_ids``: list of affected resource ids
          - ``valid_count``: count of resources passing all gates
          - ``invalid_details``: annotated invalid resources with violations
    """
    contract = contract or ResourceQualityContract()

    rows = conn.execute(
        """
        SELECT id, name, body_text, token_count, status
        FROM resources
        """
    ).fetchall()
    resources = [dict(r) for r in rows]

    valid, invalid = validate_resources(resources, contract=contract, embedder=embedder)

    ids = [r["id"] for r in invalid]
    if not ids:
        return {"purged": 0, "invalid_ids": [], "valid_count": len(valid)}

    placeholders = ",".join("?" * len(ids))
    if delete:
        conn.execute(f"DELETE FROM resources WHERE id IN ({placeholders})", ids)
        action_key = "deleted"
    else:
        conn.execute(
            f"""
            UPDATE resources
            SET status = 'purged', updated_at = datetime('now')
            WHERE id IN ({placeholders})
            """,
            ids,
        )
        action_key = "purged"

    logger.info(
        "Resource purge %s: %d resources marked invalid (valid=%d, invalid=%d)",
        action_key,
        len(ids),
        len(valid),
        len(invalid),
    )

    return {
        action_key: len(ids),
        "invalid_ids": ids,
        "valid_count": len(valid),
        "invalid_details": invalid,
    }


def get_default_embedder() -> Callable[[str], Any] | None:
    """Return the default production embedder if available.

    Lazily loads ``server.iso_embedder.IsoEmbedder``. Returns None when the
    embedder is unavailable so callers can decide whether to fail open or
    fail closed.
    """
    return _iso_embedder()
