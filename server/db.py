"""NEXUS Database — single source of truth for DB path resolution and connection management.

Consolidates all _resolve_db_path() copies (previously: orchestrator.py, nexus-mcp-enhanced.py,
broker_runtime.py, reflection.py — all had their own slightly different versions).

Key design decisions:
  - LAZY resolution: DB_PATH is a function call, not a module constant.
    Avoids import-time side effects and ensures the path is re-resolved
    if the filesystem state changes.
  - Directory auto-creation: parent dir is created with mkdir(parents=True)
    so sqlite3.connect() never gets a non-existent directory.
  - WAL mode + busy_timeout: every connection is configured for concurrent
    access. Set via ``set_pragma(conn)``.
  - Single import: ``from server.db import get_db_path, get_connection``
    instead of each file having its own path resolution.
"""

from __future__ import annotations

import os
import sqlite3
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


# ── Default locations ────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path.home() / "nexus" / "db" / "nexus.db"

# Optional user override: set environment to redirect the DB
_ENV_VARS = ["NEXUS_BROKER_DB_PATH", "NEXUS_DB_PATH"]


# ── Path resolution (lazy, not at import time) ───────────────────────────


def resolve_db_path() -> Path:
    """Find the writable NEXUS database path.

    Priority:
      1. ``NEXUS_BROKER_DB_PATH`` env var (legacy / broker-specific)
      2. ``NEXUS_DB_PATH`` env var
      3. ``~/nexus/db/nexus.db`` (default)
      4. ``/tmp/nexus.db`` (fallback if default exists but is unwritable)

    Unlike the previous duplicated versions, this function ALWAYS ensures
    the parent directory exists before returning.
    """
    db_path: Path | None = None

    for var in _ENV_VARS:
        val = os.environ.get(var)
        if val:
            db_path = Path(os.path.expandvars(val))
            break

    if db_path is None:
        db_path = DEFAULT_DB_PATH

    # If the file already exists and is writable, use it directly.
    if db_path.exists() and os.access(db_path, os.W_OK):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return db_path

    # Default exists but is NOT writable (e.g. permissions changed) →
    # copy to /tmp as a fallback so the system keeps working.
    if db_path.exists() and not os.access(db_path, os.W_OK):
        tmp = Path("/tmp/nexus.db")
        try:
            shutil.copy2(str(db_path), str(tmp))
        except Exception:
            pass  # swallow copy failure; fall through to ensure directory
        if tmp.exists():
            db_path = tmp

    # Ensure the parent directory exists — this is the key fix that was missing.
    # Without it, sqlite3.connect() raises "unable to open database file"
    # even when the path is technically valid.
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return db_path


def get_db_path() -> Path:
    """Convenience: return the resolved DB path (lazy, cached)."""
    # Simple module-level cache so we don't re-stat the filesystem every call.
    if not hasattr(get_db_path, "_cached"):
        get_db_path._cached = resolve_db_path()  # type: ignore[attr-defined]
    return get_db_path._cached  # type: ignore[attr-defined]


# ── Connection management ────────────────────────────────────────────────


def set_pragma(conn: sqlite3.Connection) -> None:
    """Configure connection for concurrent multi-process access.

    - WAL mode: allows concurrent readers + one writer without blocking.
    - busy_timeout: if the DB is locked, wait up to 30s before failing.
    - foreign_keys: enforce referential integrity.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")  # 30 seconds
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # balance safety + speed


def get_connection(timeout: int = 60) -> sqlite3.Connection:
    """Open a connection to the NEXUS database.

    Every caller should use ``with get_connection() as conn:`` to ensure
    the connection is properly closed.
    """
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    set_pragma(conn)
    return conn


def close_connection(conn: sqlite3.Connection | None) -> None:
    """Safely close a connection, committing any pending transaction."""
    if conn is None:
        return
    try:
        conn.commit()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def db_conn(timeout: int = 60) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a managed DB connection.

    Usage::

        with db_conn() as conn:
            rows = conn.execute(...)

    Connection is committed on success, rolled back on exception, and
    always closed on exit.
    """
    conn = get_connection(timeout=timeout)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        close_connection(conn)


# ── Schema helpers ───────────────────────────────────────────────────────


def ensure_routing_log_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the routing_log table with relaxed outcome CHECK.

    The original CHECK constraint ``CHECK(outcome IN ('correct','wrong','partial'))``
    was too restrictive — ``auto_invoke`` writes ``'pending'`` as a transient state.
    This migration replaces the constraint with the broader set.

    Idempotency: three branches. A **missing** table is created fresh with the
    relaxed CHECK. An existing **strict** table takes the one-time copy-rebuild
    (explicit 12-column copy, not ``SELECT *``, to survive width drift vs
    db/schema.sql). A table that **already allows** 'routed'/'pending' skips
    straight to the additive-ALTER block — a blind re-run of the rebuild would
    feed 20+ live columns into the 14-column temp table and crash.
    """
    existing_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='routing_log'"
    ).fetchone()
    table_exists = existing_sql_row is not None
    existing_sql = (existing_sql_row[0] if existing_sql_row else "") or ""
    already_relaxed = "'routed'" in existing_sql or "'pending'" in existing_sql
    if not table_exists:
        # Fresh DB: create the table directly with the relaxed CHECK. The
        # copy-rebuild below assumes an existing table to rename, so a missing
        # table must take this path — otherwise the RENAME crashes with
        # "no such table: routing_log".
        conn.executescript("""
        CREATE TABLE routing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_summary TEXT NOT NULL,
            project TEXT NOT NULL DEFAULT 'default',
            matched_tier INTEGER NOT NULL,
            matched_model TEXT NOT NULL,
            matched_skills TEXT NOT NULL DEFAULT '[]',
            actual_model_used TEXT,
            outcome TEXT CHECK(outcome IN ('correct','wrong','partial','pending','routed')),
            skill_name TEXT,
            tokens_used INTEGER DEFAULT 0,
            ide TEXT NOT NULL DEFAULT 'unknown' CHECK(ide IN ('opencode','claude-code','unknown','hermes','codex','mcp','auto_invoke','http','eval','pytest')),
            action_type TEXT,
            repeat_count_1h INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    elif not already_relaxed:
        conn.executescript("""
        -- Create a temporary table without the strict CHECK
        CREATE TABLE IF NOT EXISTS routing_log_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_summary TEXT NOT NULL,
            project TEXT NOT NULL DEFAULT 'default',
            matched_tier INTEGER NOT NULL,
            matched_model TEXT NOT NULL,
            matched_skills TEXT NOT NULL DEFAULT '[]',
            actual_model_used TEXT,
            outcome TEXT CHECK(outcome IN ('correct','wrong','partial','pending','routed')),
            skill_name TEXT,
            tokens_used INTEGER DEFAULT 0,
            ide TEXT NOT NULL DEFAULT 'unknown' CHECK(ide IN ('opencode','claude-code','unknown','hermes','codex','mcp','auto_invoke','http','eval','pytest')),
            action_type TEXT,
            repeat_count_1h INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Copy data if old table exists. Explicit column list (not SELECT *)
        -- so this survives column drift between db/schema.sql's routing_log and
        -- routing_log_new — a bare SELECT * crashes when the two definitions
        -- differ in width (they do: schema.sql omits action_type/repeat_count_1h).
        INSERT OR IGNORE INTO routing_log_new
            (id, task_summary, project, matched_tier, matched_model,
             matched_skills, actual_model_used, outcome, skill_name,
             tokens_used, ide, created_at)
            SELECT id, task_summary, project, matched_tier, matched_model,
                   matched_skills, actual_model_used, outcome, skill_name,
                   tokens_used, ide, created_at
            FROM routing_log;

        -- Rename tables (drop old, rename new)
        DROP TABLE IF EXISTS routing_log_old;
        ALTER TABLE routing_log RENAME TO routing_log_old;
        ALTER TABLE routing_log_new RENAME TO routing_log;
        DROP TABLE IF EXISTS routing_log_old;
    """)

    # Week1 card 4.4 — eval/observability columns (additive; guarded ALTERs so
    # the copy-migration above keeps its column-count contract untouched).
    # Writers start in Phases 2-3; adding columns early keeps one migration point.
    for _ddl in (
        "ALTER TABLE routing_log ADD COLUMN intent TEXT",
        "ALTER TABLE routing_log ADD COLUMN route_match INTEGER",
        "ALTER TABLE routing_log ADD COLUMN latency_ms REAL",
        "ALTER TABLE routing_log ADD COLUMN cost REAL",
        "ALTER TABLE routing_log ADD COLUMN injected_ctx_len INTEGER DEFAULT 0",
        "ALTER TABLE routing_log ADD COLUMN harness_tag TEXT DEFAULT 'raw-api'",
        # G12 — audit marker so a derived/auto-closed verdict is distinguishable
        # from an explicit human one. NULL on legacy rows (predate the column).
        # outcome_source ∈ {'explicit','derived','timeout'}; quality ∈ [0,1].
        "ALTER TABLE routing_log ADD COLUMN outcome_source TEXT",
        "ALTER TABLE routing_log ADD COLUMN quality REAL",
    ):
        try:
            conn.execute(_ddl)
        except sqlite3.OperationalError:
            pass  # column already present
    conn.commit()
