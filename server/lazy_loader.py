"""NEXUS v3 Two-Phase Lazy Loader — summary pool + on-demand body load + LRU eviction."""
from __future__ import annotations
from typing import Optional, Callable
import sqlite3, asyncio, threading, time
from collections import OrderedDict
from dataclasses import dataclass

# ── Lazy-loader Settings ──────────────────────────────────────────────
MAX_SUMMARY_POOL = 512                    # LRU: keep most recent N summaries
MAX_BODY_POOL = 128                         # LRU: keep most recently loaded full bodies
BODY_LOAD_TIMEOUT_MS = 20                   # fail-open if body load stalls
SUMMARY_MAX_BYTES = 64 * 1024 * 1024        # 64 MB hard cap for summary pool
BODY_MAX_BYTES = 16 * 1024 * 1024          # 16 MB hard cap for body pool

def _now() -> float:
    return time.monotonic()

@dataclass(slots=True)
class _Entry:
    summary: dict
    hits: int = 0
    last_access: float = 0.0

class LazyLoader:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._summary_pool: OrderedDict[int, _Entry] = OrderedDict()
        self._body_pool: OrderedDict[int, dict] = OrderedDict()
        self._pool_misses = 0
        self._pool_hits = 0
        self._body_misses = 0
        self._body_hits = 0

    # ── Summary Pool ─────────────────────────────────────────────────
    def warm_summary_pool(self, rows: list[tuple]):
        with self._lock:
            for row in rows:
                rid = row[0]
                if rid in self._summary_pool:
                    del self._summary_pool[rid]
                self._summary_pool[rid] = _Entry(
                    summary={
                        "id": rid,
                        "name": row[1],
                        "description": (row[2] or "")[:500],
                        "resource_type": row[3],
                        "tier": row[4] or 2,
                        "times_triggered": row[5] or 0,
                        "times_correct": row[6] or 0,
                        "times_wrong": row[7] or 0,
                        "file_path": row[8] or "",
                    },
                    last_access=_now(),
                )

    def get_summary(self, resource_id: int) -> Optional[dict]:
        with self._lock:
            entry = self._summary_pool.get(resource_id)
            if entry:
                entry.hits += 1
                entry.last_access = _now()
                self._summary_pool.move_to_end(resource_id)
                self._pool_hits += 1
                return entry.summary
            self._pool_misses += 1
            return None

    def get_or_create_summary(self, resource_id: int, row: tuple) -> dict:
        with self._lock:
            entry = self._summary_pool.get(resource_id)
            if entry:
                entry.hits += 1
                entry.last_access = _now()
                self._summary_pool.move_to_end(resource_id)
                self._pool_hits += 1
                return entry.summary
            self._pool_misses += 1
            summary = {
                "id": resource_id,
                "name": row[1],
                "description": (row[2] or "")[:500],
                "resource_type": row[3],
                "tier": row[4] or 2,
                "times_triggered": row[5] or 0,
                "times_correct": row[6] or 0,
                "times_wrong": row[7] or 0,
                "file_path": row[8] or "",
            }
            self._summary_pool[resource_id] = _Entry(
                summary=summary, last_access=_now()
            )
            self._evict_if_needed(self._summary_pool, MAX_SUMMARY_POOL, "summary")
            return summary

    def _evict_if_needed(self, pool: OrderedDict, limit: int, kind: str):
        if len(pool) > limit:
            oldest_rid = next(iter(pool))
            del pool[oldest_rid]

    # ── Body Pool ────────────────────────────────────────────────────
    def load_body(self, resource_id: int) -> Optional[dict]:
        with self._lock:
            entry = self._body_pool.get(resource_id)
            if entry:
                self._body_pool.move_to_end(resource_id)
                self._body_hits += 1
                return entry

        conn = sqlite3.connect(self.db_path, timeout=60)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, description, body_text, resource_type, file_path "
                "FROM resources WHERE id= ?",
                (resource_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return None

        full = {
            "id": row[0],
            "name": row[1],
            "description": row[2] or "",
            "body_text": row[3] or "",
            "resource_type": row[4],
            "file_path": row[5] or "",
        }
        with self._lock:
            self._body_pool[resource_id] = full
            self._body_pool.move_to_end(resource_id)
            self._evict_if_needed(self._body_pool, MAX_BODY_POOL, "body")
            self._body_misses += 1
        return full

    async def load_body_async(self, resource_id: int) -> Optional[dict]:
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, self.load_body, resource_id),
            timeout=BODY_LOAD_TIMEOUT_MS / 1000.0,
        )

    # ── Schema Promotion ─────────────────────────────────────────────
    def needs_schema(self, summary: dict, task: str) -> str | None:
        """Return a schema hint if this resource type should be deeply loaded for this task."""
        if summary["resource_type"] in ("design_system", "spec", "doc"):
            return "deep"
        kw = task.lower().split()
        if any(w in kw for w in ("api", "endpoint", "schema", "db", "model", "query", "table", "hook")):
            if summary["resource_type"] in ("skill", "cli_tool", "mcp"):
                return "shallow"
        return None

    # ── Statistics ──────────────────────────────────────────────────
    def get_stats(self) -> dict:
        with self._lock:
            return {
                "summary_pool_size": len(self._summary_pool),
                "body_pool_size": len(self._body_pool),
                "summary_hits": self._pool_hits,
                "summary_misses": self._pool_misses,
                "body_hits": self._body_hits,
                "body_misses": self._body_misses,
                "hit_rate": self._pool_hits / max(self._pool_hits + self._pool_misses, 1),
                "body_hit_rate": self._body_hits / max(self._body_hits + self._body_misses, 1),
            }

    def clear(self):
        with self._lock:
            self._summary_pool.clear()
            self._body_pool.clear()
            self._pool_hits = self._pool_misses = self._body_hits = self._body_misses = 0

def get_loader(db_path: str = None) -> LazyLoader:
    """Singleton lazy-loader instance."""
    if db_path is None:
        import os
        db_path = os.path.expandvars(os.environ.get("NEXUS_DB_PATH", str(Path.home() / "nexus/db/nexus.db")))
    if not hasattr(get_loader, "_instance"):
        with get_loader._lock:
            if not hasattr(get_loader, "_instance"):
                get_loader._instance = LazyLoader(db_path)
    return get_loader._instance


get_loader._lock = threading.Lock()