"""FTS5 full-text search wrapper for NEXUS chunk retrieval.

Wraps the ``chunks_fts`` FTS5 virtual table and the relational
``ingestion_chunks`` / ``resources`` tables to provide sanitized MATCH
queries, bm25-ranked results, and resource hydration.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# Characters that carry FTS5 query-syntax meaning. Stripping them prevents
# users from injecting operators (column qualifiers, prefix globs, phrase
# groupings, boolean AND/OR/NOT, NEAR) into a sanitized MATCH string.
_FTS5_SYNTAX_CHARS = set("*:^\"()-+")

# FTS5 reserved boolean keywords that must never leak into a sanitized query.
_FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}

# Pre-compiled tokenizer regex for prepare_query: splits on any whitespace.
_WHITESPACE_SPLIT = re.compile(r"\s+")


class FTS5Search:
    """Search the ``chunks_fts`` FTS5 index with bm25 ranking and hydration."""

    def __init__(self, db_path: str) -> None:
        """Open a read-only-ish SQLite connection to ``db_path``.

        Args:
            db_path: Path to the SQLite database containing the
                ``chunks_fts`` virtual table and related tables.
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------ #
    # Context manager                                                    #
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "FTS5Search":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()

    # ------------------------------------------------------------------ #
    # Query sanitization                                                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    def prepare_query(query: str) -> str:
        """Sanitize a raw user query into a safe FTS5 MATCH string.

        Strips FTS5 syntax characters (``* : ^ " ( ) - +``) and boolean
        keywords (``AND OR NOT NEAR``), splits on whitespace, drops empty
        tokens, wraps each token in double quotes, and joins them with
        spaces (FTS5 implicit AND).

        Args:
            query: Raw user-supplied query string.

        Returns:
            A safe FTS5 MATCH expression, e.g. ``"find" "a" "skill"``.
            Returns an empty string when ``query`` contains no usable
            tokens (callers should avoid issuing an empty MATCH).
        """
        if not query:
            return ""
        # Remove FTS5 syntax characters.
        cleaned = "".join(
            ch for ch in query if ch not in _FTS5_SYNTAX_CHARS
        )
        tokens: list[str] = []
        for raw in _WHITESPACE_SPLIT.split(cleaned):
            token = raw.strip().strip('"').strip()
            if not token:
                continue
            if token.upper() in _FTS5_KEYWORDS:
                continue
            tokens.append(f'"{token}"')
        return " ".join(tokens)

    # ------------------------------------------------------------------ #
    # Core search                                                        #
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        limit: int = 50,
        threshold: float = 10.0,
    ) -> list[dict[str, Any]]:
        """Run an FTS5 bm25-ranked search against ``chunks_fts``.

        Args:
            query: Raw user query; sanitized via :meth:`prepare_query`.
            limit: Maximum number of rows to return from the FTS index.
            threshold: Drop rows whose bm25 score exceeds this value
                (lower/negative bm25 scores indicate better matches).

        Returns:
            List of dicts with keys ``chunk_id``, ``resource_id``,
            ``heading``, ``chunk_type``, ``content``, ``score``.
        """
        prepared = self.prepare_query(query)
        if not prepared:
            return []
        sql = (
            "SELECT rowid AS chunk_id, resource_id, heading, chunk_type, "
            "content, bm25(chunks_fts) AS score "
            "FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY score LIMIT ?"
        )
        rows = self.conn.execute(sql, (prepared, limit)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            score = row["score"]
            if score > threshold:
                continue
            results.append(
                {
                    "chunk_id": row["chunk_id"],
                    "resource_id": row["resource_id"],
                    "heading": row["heading"],
                    "chunk_type": row["chunk_type"],
                    "content": row["content"],
                    "score": score,
                }
            )
        return results

    # ------------------------------------------------------------------ #
    # Hydration                                                          #
    # ------------------------------------------------------------------ #
    def hydrate(self, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Fetch relational metadata for a set of FTS chunk rowids.

        Args:
            chunk_ids: ``rowid`` values from ``chunks_fts`` (which mirror
                ``ingestion_chunks.id``).

        Returns:
            Mapping of ``chunk_id`` to a dict with keys ``chunk_id``,
            ``resource_id``, ``heading``, ``start_line``, ``end_line``,
            ``name``, ``resource_type``, ``cluster``. Missing ids are
            omitted from the result.
        """
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        sql = (
            "SELECT c.id AS chunk_id, c.resource_id, c.heading, "
            "c.start_line, c.end_line, r.name, r.resource_type, r.cluster "
            "FROM ingestion_chunks c "
            "LEFT JOIN resources r ON c.resource_id = r.id "
            f"WHERE c.id IN ({placeholders})"
        )
        rows = self.conn.execute(sql, tuple(chunk_ids)).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for row in rows:
            out[row["chunk_id"]] = {
                "chunk_id": row["chunk_id"],
                "resource_id": row["resource_id"],
                "heading": row["heading"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "name": row["name"],
                "resource_type": row["resource_type"],
                "cluster": row["cluster"],
            }
        return out

    # ------------------------------------------------------------------ #
    # Combined search + hydrate                                          #
    # ------------------------------------------------------------------ #
    def search_hydrated(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search then hydrate, merging FTS hits with relational metadata.

        Keeps ``score`` and ``content`` from the FTS result and merges in
        ``name``, ``resource_type``, ``cluster``, ``start_line``,
        ``end_line`` from hydration. Results are sorted by bm25 score
        ascending (best first).

        Args:
            query: Raw user query.
            limit: Maximum number of FTS rows to retrieve.

        Returns:
            List of merged dicts sorted by ``score`` ascending.
        """
        hits = self.search(query, limit=limit)
        if not hits:
            return []
        hydrated = self.hydrate([h["chunk_id"] for h in hits])
        merged: list[dict[str, Any]] = []
        for hit in hits:
            entry = dict(hit)
            extra = hydrated.get(hit["chunk_id"])
            if extra:
                for key, value in extra.items():
                    # Preserve FTS-derived score and content over hydration.
                    if key in ("score", "content"):
                        continue
                    entry[key] = value
            merged.append(entry)
        merged.sort(key=lambda d: d["score"])
        return merged