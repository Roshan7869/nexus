"""Content routing and window packing for NEXUS Context Harness Engine.

P3-SA2: ContentRouter takes ranked resource IDs from the Router (P2),
loads full content, applies compression (P3-SA1), and produces the final
context window for the LLM.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import NamedTuple, TypedDict

from .cache import L0Cache, L2Cache, get_ro_conn
from .compression import (
    CompressedWindow,
    PackedSection,
    VolatileContentDetector,
    WindowPacker,
    estimate_tokens,
)

logger = logging.getLogger(__name__)

_SECTION_CACHE_SIZE = 512
_SNIPPET_MAX_CHARS = 500


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ContentRequest(TypedDict):
    resource_ids: list[int]
    query: str
    project_type: str | None
    max_tokens: int
    compress: bool
    include_snippets: bool


class ContentWindow(NamedTuple):
    windows: list[CompressedWindow]
    total_tokens: int
    num_resources: int
    compression_savings: int
    volatile_tags_found: int
    latency_ms: float


# ---------------------------------------------------------------------------
# Internal LRU for section rows
# ---------------------------------------------------------------------------


class _SectionRowLRU:
    __slots__ = ("_maxsize", "_cache", "_lock")

    def __init__(self, maxsize: int = _SECTION_CACHE_SIZE) -> None:
        self._maxsize = maxsize
        self._cache: OrderedDict[tuple[int, ...], list[dict]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: tuple[int, ...]) -> list[dict] | None:
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def set(self, key: tuple[int, ...], sections: list[dict]) -> None:
        with self._lock:
            while len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
            self._cache[key] = sections

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# ---------------------------------------------------------------------------
# Content Router
# ---------------------------------------------------------------------------


class ContentRouter:
    """Routes ranked resource IDs through compression into packed windows.

    Steps:
        1. Resolve resource IDs to sections from DB (or cache)
        2. Build PackedSection per section (volatile detection + normalization)
        3. Sort sections by salience descending
        4. Pack into CompressedWindows via WindowPacker
        5. Return ContentWindow with statistics
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._detector = VolatileContentDetector()
        self._latency_samples: list[float] = []
        self._sections_processed = 0
        self._compression_savings_total = 0
        self._section_lru = _SectionRowLRU()
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_content(self, request: ContentRequest) -> ContentWindow:
        """Load, compress, and pack content for the LLM.

        Parameters
        ----------
        request:
            ContentRequest with ranked resource IDs, query, and options.

        Returns
        -------
        ContentWindow with packed windows and statistics.
        """
        start = time.perf_counter()
        resource_ids = request.get("resource_ids", [])
        max_tokens = request.get("max_tokens", 8192)
        compress = request.get("compress", True)
        include_snippets = request.get("include_snippets", False)

        # Step 1: Resolve resource IDs to sections
        sections_data = self._load_sections_from_db(resource_ids)

        # Step 2: Build PackedSection for each section
        packed_sections: list[PackedSection] = []
        total_volatile_tags = 0
        total_compression_savings = 0

        for order, row in enumerate(sections_data):
            if include_snippets:
                row = dict(row)
                content = row.get("content") or ""
                row["content"] = content[:_SNIPPET_MAX_CHARS]

            packed = self._build_packed_section(row, order, compress)
            packed_sections.append(packed)
            total_volatile_tags += len(packed.volatile_stripped)

            if compress:
                orig_tokens = estimate_tokens(row.get("content") or "")
                saved = orig_tokens - packed.body_tokens
                total_compression_savings += max(0, saved)

        # Step 3: Sort by salience descending
        packed_sections.sort(key=lambda ps: ps.salience, reverse=True)

        # Step 4: Pack into windows
        packer = WindowPacker(max_tokens=max_tokens)
        windows: list[CompressedWindow] = []

        for ps in packed_sections:
            if not packer.add_section(ps):
                windows.extend(packer.flush())
                packer.add_section(ps)

        windows.extend(packer.flush())

        # Step 5: Track statistics
        elapsed = (time.perf_counter() - start) * 1000.0
        self._latency_samples.append(elapsed)
        self._sections_processed += len(packed_sections)
        self._compression_savings_total += total_compression_savings

        total_tokens = sum(w.total_tokens for w in windows)

        return ContentWindow(
            windows=windows,
            total_tokens=total_tokens,
            num_resources=len(packed_sections),
            compression_savings=total_compression_savings,
            volatile_tags_found=total_volatile_tags,
            latency_ms=round(elapsed, 2),
        )

    def load_content_from_cache(
        self,
        resource_ids: list[int],
        cache: L2Cache | L0Cache,
    ) -> list[PackedSection]:
        """Load content from cache first, falling back to DB.

        Parameters
        ----------
        resource_ids:
            Ranked resource IDs to load.
        cache:
            L2Cache (preferred — has full body_text) or L0Cache (snippet only).

        Returns
        -------
        List of PackedSection objects.
        """
        packed: list[PackedSection] = []
        uncached_ids: list[int] = []

        is_l2 = hasattr(cache, "get_all_for_cluster")

        for rid in resource_ids:
            entry = None
            if is_l2 and hasattr(cache, "get") and hasattr(cache, "_entries"):
                for cluster in list(cache._entries.keys()):
                    entry = cache.get(cluster, rid)
                    if entry is not None:
                        break
            elif hasattr(cache, "get"):
                entry = cache.get(rid)

            if entry is not None:
                body = getattr(entry, "body_text", None) or getattr(entry, "body_snippet", None) or ""
                name = getattr(entry, "name", "") or ""
                rtype = getattr(entry, "resource_type", "") or ""
                section = self._build_packed_section(
                    {
                        "resource_id": rid,
                        "section_id": 0,
                        "heading": name,
                        "content": body,
                        "resolved_name": name,
                        "resource_type": rtype,
                    },
                    order=len(packed),
                    compress=True,
                )
                packed.append(section)
            else:
                uncached_ids.append(rid)

        if uncached_ids:
            rows = self._load_sections_from_db(uncached_ids)
            for order, row in enumerate(rows):
                section = self._build_packed_section(
                    row, len(packed) + order, True,
                )
                packed.append(section)

        return packed

    def get_stats(self) -> dict:
        """Return aggregate statistics.

        Returns dict with keys:
        - latency_p95 — 95th percentile latency in ms
        - total_sections_processed
        - avg_compression_savings — average tokens saved per section
        """
        samples = sorted(self._latency_samples)
        n = len(samples)
        if n == 0:
            p95 = 0.0
        elif n == 1:
            p95 = samples[0]
        else:
            idx = min(int(n * 0.95), n - 1)
            p95 = samples[idx]

        avg_savings = (
            self._compression_savings_total / max(self._sections_processed, 1)
        ) if self._sections_processed > 0 else 0.0

        return {
            "latency_p95": round(p95, 2),
            "total_sections_processed": self._sections_processed,
            "avg_compression_savings": round(avg_savings, 2),
        }

    def clear_cache(self) -> None:
        """Reset the internal LRU cache for section content."""
        self._section_lru.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_sections_from_db(self, resource_ids: list[int]) -> list[dict]:
        """Query resource_sections + resources for the given IDs.

        Returns a list of dicts with keys: resource_id, section_id, heading,
        content, resolved_name, resource_type.
        """
        if not resource_ids:
            return []

        key = tuple(sorted(resource_ids))
        cached = self._section_lru.get(key)
        if cached is not None:
            return cached

        placeholders = ",".join("?" for _ in resource_ids)
        sql = f"""
            SELECT rs.resource_id, rs.id AS section_id, rs.heading,
                   rs.content, r.name AS resolved_name, r.resource_type
            FROM resource_sections rs
            JOIN resources r ON rs.resource_id = r.id
            WHERE r.status = 'active' AND rs.resource_id IN ({placeholders})
            ORDER BY rs.resource_id, rs.sort_order
        """

        if self._db_path:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True, timeout=15,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=15000")
        else:
            conn = get_ro_conn()

        try:
            rows = conn.execute(sql, resource_ids).fetchall()
            result = [dict(r) for r in rows]
        except Exception:
            logger.exception(
                "Failed to load sections for %d resource(s)", len(resource_ids),
            )
            result = []
        finally:
            conn.close()

        self._section_lru.set(key, result)
        return result

    def _build_packed_section(
        self,
        row: dict,
        order: int,
        compress: bool,
    ) -> PackedSection:
        """Build a PackedSection from a DB row, running volatile detection.

        Parameters
        ----------
        row:
            Dict with keys: resource_id, section_id, heading, content,
            resolved_name, resource_type.
        order:
            Position in the ranked list (0 = highest rank).
        compress:
            Whether to run volatile detection and normalization.

        Returns
        -------
        PackedSection ready for window packing.
        """
        body = row.get("content") or ""
        heading = row.get("heading") or ""
        resolved_name = row.get("resolved_name") or ""
        resource_id = row["resource_id"]
        section_id = row["section_id"]

        if compress and body:
            tags = self._detector.detect_all(body)
            normalized = self._detector.normalize(body, tags)
            volatile_stripped = [t.original for t in tags]
        else:
            tags = []
            normalized = body
            volatile_stripped = []

        body_tokens = estimate_tokens(normalized)
        salience = 1.0 / (order + 1)
        compression_ratio = (
            len(body) - len(normalized)
        ) / max(len(body), 1) if compress and body else 0.0

        return PackedSection(
            resource_id=resource_id,
            section_id=section_id,
            section_title=heading,
            resolved_name=resolved_name,
            body=normalized,
            body_tokens=body_tokens,
            salience=salience,
            compression_ratio=compression_ratio,
            volatile_stripped=volatile_stripped,
        )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_context_window(
    windows: list[CompressedWindow],
    format: str = "llm",
) -> str:
    """Format CompressedWindows for consumption.

    Parameters
    ----------
    windows:
        Compressed windows from ContentRouter.load_content().
    format:
        'llm'   — concatenated with section markers (default)
        'debug' — with token counts and compression info
        'json'  — serialized JSON

    Returns
    -------
    Formatted string.
    """
    if format == "json":
        return json.dumps(
            [
                {
                    "window_id": w.window_id,
                    "slot": w.slot,
                    "cache_level": w.cache_level,
                    "total_tokens": w.total_tokens,
                    "max_tokens": w.max_tokens,
                    "sections": [
                        {
                            "resource_id": s.resource_id,
                            "section_id": s.section_id,
                            "section_title": s.section_title,
                            "resolved_name": s.resolved_name,
                            "body": s.body,
                            "body_tokens": s.body_tokens,
                            "salience": s.salience,
                            "compression_ratio": s.compression_ratio,
                            "volatile_stripped": s.volatile_stripped,
                        }
                        for s in w.sections
                    ],
                }
                for w in windows
            ],
            indent=2,
        )

    parts: list[str] = []

    for w in windows:
        if format == "debug":
            parts.append(
                f"--- Window slot={w.slot} tokens={w.total_tokens}/{w.max_tokens} "
                f"id={w.window_id} ---"
            )

        for s in w.sections:
            header = f"## {s.resolved_name}"
            if s.section_title:
                header += f" \u2014 {s.section_title}"
            parts.append(header)

            if format == "debug":
                parts.append(
                    f"(section={s.section_id} tokens={s.body_tokens} "
                    f"salience={s.salience:.3f} ratio={s.compression_ratio:.3f} "
                    f"volatile={len(s.volatile_stripped)})"
                )

            parts.append(s.body)

    return "\n\n".join(parts)


__all__ = [
    "ContentRequest",
    "ContentWindow",
    "ContentRouter",
    "format_context_window",
]
