"""Coach schema management — table creation and DBTables dataclass."""
from server.db import get_db_path as _resolve_db_path

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


DB_PATH = os.environ.get(
    "NEXUS_DB_PATH",
    str(_resolve_db_path()),
)
SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"


@dataclass(frozen=True)
class DBTables:
    """Logical table names for the Coach schema."""

    CACHE_META: str = "coach_cache_meta"
    PRACTICE_SCORES: str = "coach_practice_scores"
    ANTIPATTERNS: str = "coach_antipatterns"
    SESSIONS: str = "coach_sessions"
    SKILL_FINDER: str = "coach_skill_finder"
    DAILY_ACTIVITY: str = "coach_daily_activity"
    BURNDOWN: str = "coach_burndown"


def get_connection() -> sqlite3.Connection:
    """Get a writable SQLite connection to the NEXUS database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_coach_schema(conn: sqlite3.Connection | None = None) -> bool:
    """Create all Coach tables if they don't exist.

    Args:
        conn: Optional existing connection. If None, opens and closes one.

    Returns:
        True on success, False on error.
    """
    close = conn is None
    try:
        if conn is None:
            conn = get_connection()
        sql_path = SCHEMA_SQL_PATH
        if not sql_path.exists():
            sql_path = Path("/home/roshan/nexus/server/coach/schema.sql")
        sql = sql_path.read_text()
        conn.executescript(sql)
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        if close and conn:
            conn.close()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None
