"""Gap 6: link chunk_embeddings → resources via ingestion_chunks.resource_id.

chunk_id already equals ingestion_chunks.id for all rows; resource_id was
missing on the embedding table so retrieval could not map vectors → skills.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

NEXUS_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = NEXUS_ROOT / "db" / "nexus.db"


def ensure_resource_id_column(conn: sqlite3.Connection) -> bool:
    """Add resource_id column if missing. Returns True if column exists/created."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunk_embeddings)").fetchall()}
    if "resource_id" in cols:
        return True
    conn.execute("ALTER TABLE chunk_embeddings ADD COLUMN resource_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_emb_resource "
        "ON chunk_embeddings(resource_id)"
    )
    conn.commit()
    return True


def backfill_chunk_resource_ids(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    owns = conn is None
    if owns:
        conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=120)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
    try:
        ensure_resource_id_column(conn)
        before = conn.execute(
            "SELECT COUNT(*) FROM chunk_embeddings WHERE resource_id IS NOT NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        # Primary path: chunk_id == ingestion_chunks.id
        conn.execute(
            """
            UPDATE chunk_embeddings
            SET resource_id = (
                SELECT ic.resource_id FROM ingestion_chunks ic
                WHERE ic.id = chunk_embeddings.chunk_id
            )
            WHERE resource_id IS NULL
            """
        )
        # Refresh any already-linked rows that drifted (optional re-sync)
        conn.execute(
            """
            UPDATE chunk_embeddings
            SET resource_id = (
                SELECT ic.resource_id FROM ingestion_chunks ic
                WHERE ic.id = chunk_embeddings.chunk_id
            )
            WHERE EXISTS (
                SELECT 1 FROM ingestion_chunks ic
                WHERE ic.id = chunk_embeddings.chunk_id
                  AND ic.resource_id IS NOT NULL
                  AND (chunk_embeddings.resource_id IS NULL
                       OR chunk_embeddings.resource_id != ic.resource_id)
            )
            """
        )
        # Fallback: text_hash == content_hash when id join missed
        conn.execute(
            """
            UPDATE chunk_embeddings
            SET resource_id = (
                SELECT ic.resource_id FROM ingestion_chunks ic
                WHERE ic.content_hash = chunk_embeddings.text_hash
                  AND ic.resource_id IS NOT NULL
                LIMIT 1
            )
            WHERE resource_id IS NULL
            """
        )
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) FROM chunk_embeddings WHERE resource_id IS NOT NULL"
        ).fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT resource_id) FROM chunk_embeddings "
            "WHERE resource_id IS NOT NULL"
        ).fetchone()[0]
        linked_pct = round(100.0 * after / max(total, 1), 2)
        return {
            "total_embeddings": total,
            "linked_before": before,
            "linked_after": after,
            "distinct_resources": distinct,
            "linked_pct": linked_pct,
            # Gate: majority of embeddings map to a resource
            "chunk_resource_link_gate_pass": linked_pct >= 50.0 and after >= 1000,
        }
    finally:
        if owns:
            conn.close()


def chunk_resource_stats(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = conn is None
    if owns:
        conn = sqlite3.connect(str(DEFAULT_DB), timeout=30)
        conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chunk_embeddings)").fetchall()}
        if "resource_id" not in cols:
            return {
                "has_resource_id_column": False,
                "linked_pct": 0.0,
                "chunk_resource_link_gate_pass": False,
            }
        total = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM chunk_embeddings WHERE resource_id IS NOT NULL"
        ).fetchone()[0]
        return {
            "has_resource_id_column": True,
            "total_embeddings": total,
            "linked": linked,
            "linked_pct": round(100.0 * linked / max(total, 1), 2),
            "chunk_resource_link_gate_pass": total > 0
            and (100.0 * linked / max(total, 1)) >= 50.0,
        }
    finally:
        if owns:
            conn.close()
