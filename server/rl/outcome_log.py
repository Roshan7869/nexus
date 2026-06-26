"""OutcomeLog — RL feedback API for the NEXUS resource registry.

Records per-invocation outcomes (correct / wrong / partial / skip) for tools,
skills, clusters and agents, feeding the Thompson Sampling feedback loop that
ranks resources for future routing decisions.

Schema (table ``outcome_log``):
    id                       INTEGER PRIMARY KEY AUTOINCREMENT
    resource_id              INTEGER NOT NULL  (FK -> resources.id)
    cluster_id               INTEGER (see note below)
    task_intent              TEXT
    outcome                  TEXT CHECK in ('correct','wrong','partial','skip')
    duration_ms              INTEGER
    alternative_resource_ids TEXT  (JSON array serialized via json.dumps)
    notes                    TEXT
    session_id               TEXT
    created_at               REAL DEFAULT strftime('%s','now')

Note on ``cluster_id`` type mismatch
------------------------------------
The ``outcome_log.cluster_id`` column is declared ``INTEGER`` but the
``resources.cluster`` column it is derived from is ``TEXT`` (a cluster *name*
like ``"analyzer_planner"``). No integer cluster id mapping exists in the
schema, so rather than silently dropping the value or coercing a bogus
integer, we store the cluster NAME string directly into ``cluster_id``.
SQLite uses manifest typing (dynamic typing), which permits storing TEXT in an
INTEGER-declared column, so the value round-trips losslessly and downstream
queries (``recent_for_cluster_intent``) match on the string. This is a
deliberate design choice documented here and in :meth:`OutcomeLog.record`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# Valid outcome labels — mirrors the CHECK constraint on ``outcome_log``.
VALID_OUTCOMES: frozenset[str] = frozenset({"correct", "wrong", "partial", "skip"})


class OutcomeLog:
    """Reinforcement-learning feedback API over the ``outcome_log`` table.

    Each call to :meth:`record` appends a row capturing how a resource
    performed for a given task intent. The aggregated history drives
    Thompson Sampling reward updates elsewhere in the system.

    The instance owns a single ``sqlite3.Connection`` configured with
    ``row_factory = sqlite3.Row`` and ``busy_timeout = 30000`` ms to tolerate
    concurrent writers. Use it as a context manager when possible::

        with OutcomeLog(db_path) as log:
            log.record(1, "correct", task_intent="debug X")
    """

    def __init__(self, db_path: str) -> None:
        """Open the SQLite database at ``db_path`` for read/write access.

        Args:
            db_path: Filesystem path to the SQLite database file containing
                the ``resources`` and ``outcome_log`` tables.
        """
        self.db_path: str = db_path
        self.conn: sqlite3.Connection = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # Coerce busy_timeout to 30s to avoid SQLITE_BUSY under contention.
        self.conn.execute("PRAGMA busy_timeout=30000")

    # -- internal helpers -------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a ``sqlite3.Row`` to a plain ``dict``."""
        return dict(row)

    def _resolve_cluster_id(self, resource_id: int) -> str | None:
        """Look up the cluster NAME for ``resource_id`` from ``resources``.

        Returns the ``resources.cluster`` string (e.g. ``"analyzer_planner"``)
        or ``None`` when the row has no cluster assigned or does not exist.
        Stored into ``outcome_log.cluster_id`` as-is — see module docstring for
        the type-mismatch rationale.
        """
        cur = self.conn.execute(
            "SELECT cluster FROM resources WHERE id = ?",
            (resource_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return row["cluster"]

    # -- public API -------------------------------------------------------

    def record(
        self,
        resource_id: int,
        outcome: str,
        task_intent: str | None = None,
        cluster_id: int | None = None,
        duration_ms: int | None = None,
        alternative_resource_ids: list[int] | None = None,
        notes: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Insert one outcome row and return its ``lastrowid``.

        Args:
            resource_id: FK into ``resources.id`` (caller must ensure it exists).
            outcome: One of ``"correct"``, ``"wrong"``, ``"partial"``, ``"skip"``.
            task_intent: Free-text description of the routed task intent.
            cluster_id: Optional cluster identifier. When ``None`` (default),
                it is auto-populated from ``resources.cluster`` for the given
                ``resource_id``. NOTE: ``resources.cluster`` is a TEXT cluster
                NAME, while ``outcome_log.cluster_id`` is declared INTEGER.
                Because SQLite uses dynamic typing we store the cluster NAME
                string here rather than discarding it. Pass an explicit value
                only if you have a real integer cluster id.
            duration_ms: Wall-clock duration of the resource invocation in ms.
            alternative_resource_ids: List of resource ids that could have been
                chosen instead; serialized to JSON. ``None`` -> SQL NULL.
            notes: Free-text annotation for the outcome.
            session_id: Correlation id for the originating session.

        Returns:
            The ``lastrowid`` of the inserted row.

        Raises:
            ValueError: If ``outcome`` is not one of :data:`VALID_OUTCOMES`.
        """
        if outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {outcome!r}"
            )

        # Serialize alternatives as a JSON array; None stays NULL.
        alt_json: str | None = (
            json.dumps(alternative_resource_ids)
            if alternative_resource_ids is not None
            else None
        )

        # Auto-pull cluster name when caller omitted it.
        if cluster_id is None:
            cluster_id_resolved: str | None = self._resolve_cluster_id(resource_id)
        else:
            cluster_id_resolved = cluster_id  # type: ignore[assignment]

        cur = self.conn.execute(
            """
            INSERT INTO outcome_log (
                resource_id, cluster_id, task_intent, outcome,
                duration_ms, alternative_resource_ids, notes, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource_id,
                cluster_id_resolved,
                task_intent,
                outcome,
                duration_ms,
                alt_json,
                notes,
                session_id,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def recent_for_resource(
        self,
        resource_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most recent outcomes for a single resource.

        Args:
            resource_id: Resource id to filter on.
            limit: Maximum rows to return (newest first).

        Returns:
            List of row dicts ordered by ``created_at`` descending.
        """
        cur = self.conn.execute(
            """
            SELECT id, resource_id, cluster_id, task_intent, outcome,
                   duration_ms, alternative_resource_ids, notes, session_id,
                   created_at
            FROM outcome_log
            WHERE resource_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (resource_id, limit),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def recent_for_cluster_intent(
        self,
        cluster_id: int | str,
        task_intent: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent outcomes for a cluster + task-intent pair.

        Because ``cluster_id`` stores the cluster NAME (see module docstring),
        ``cluster_id`` here is typically a string like ``"analyzer_planner"``;
        an integer is accepted too for forward compatibility.

        Args:
            cluster_id: Cluster name (string) or integer cluster id.
            task_intent: Exact task-intent string to match.
            limit: Maximum rows to return (newest first).

        Returns:
            List of row dicts ordered by ``created_at`` descending.
        """
        cur = self.conn.execute(
            """
            SELECT id, resource_id, cluster_id, task_intent, outcome,
                   duration_ms, alternative_resource_ids, notes, session_id,
                   created_at
            FROM outcome_log
            WHERE cluster_id = ? AND task_intent = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (cluster_id, task_intent, limit),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def count(self) -> int:
        """Return the total number of rows in ``outcome_log``."""
        cur = self.conn.execute("SELECT COUNT(*) FROM outcome_log")
        row = cur.fetchone()
        return int(row[0]) if row is not None else 0

    # -- context manager --------------------------------------------------

    def __enter__(self) -> "OutcomeLog":
        """Enter context-manager scope, returning ``self``."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Close the underlying connection on context-manager exit."""
        self.conn.close()