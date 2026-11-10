"""L0-L3 cache hierarchy for NEXUS Context Harness Engine.

Four cache levels with TTL+LRU, thread safety, and sqlite persistence.
Levels:
  L0 — Eager-loaded core resources (tier=1 + framework bundles)
  L1 — Lazy-loaded per project_type
  L2 — Cluster-activated on demand
  L3 — Per-task query result cache (TTL+LRU)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from server.db import get_db_path as _resolve_db_path

try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False

# Single source of truth (server/db.py:39) so NEXUS_DB_PATH is honoured.
DB_PATH = _resolve_db_path()


# ── Named Tuples ──────────────────────────────────────────────────────────────


class _L0Entry(NamedTuple):
    resource_id: int
    name: str
    resource_type: str
    body_snippet: str
    bundle: str
    priority: int
    token_count: int


class _L1Entry(NamedTuple):
    resource_id: int
    name: str
    resource_type: str
    body_snippet: str
    bundle: str
    priority: int
    token_count: int
    project_type: str
    loaded_at: float
    hit_count: int


class _L2Entry(NamedTuple):
    resource_id: int
    name: str
    resource_type: str
    body_text: str | None
    iso_score: float
    token_count: int
    last_used: str


class _L3Entry(NamedTuple):
    query_hash: str
    iso_scores: list[dict]
    total_tokens: int
    created_at: float
    hit_count: int


# ── Guardrails ────────────────────────────────────────────────────────────────

L0_MAX_TOKENS = 2048
L0_MAX_ENTRIES = 256
L1_MAX_TOKENS = 8192
L1_MAX_ENTRIES = 500
L2_MAX_TOKENS = 16384
L2_MAX_ENTRIES = 400
L3_MAX_TOKENS = 4096
L3_MAX_ENTRIES = 64
L3_TTL = 300.0

_L0_SQL = """
    SELECT r.id, r.name, r.resource_type, substr(r.body_text, 1, 200) as body_snippet,
           COALESCE(bm.bundle_name, '') as bundle, COALESCE(bm.priority, 50) as priority,
           r.token_count
    FROM resources r
    LEFT JOIN bundle_membership bm ON r.id = bm.resource_id AND bm.bundle_name = 'core_infra'
    WHERE r.status = 'active'
      AND (r.tier = 1 OR bm.bundle_name IS NOT NULL)
    ORDER BY bm.priority DESC NULLS LAST, r.times_correct DESC
    LIMIT 200
"""

_CLUSTER_TYPE_MAP = {
    'frontend_ui': 'web_dev', 'backend_api': 'web_dev',
    'quality_security': 'devops', 'devops_infra': 'devops',
    'analyzer_planner': 'general_dev', 'general': 'general_dev',
    'architect': 'general_dev',
    'knowledge_wiki': 'research',
}

_L1_FILL_SQL_TMPL = """
    SELECT r.id, r.name, r.resource_type, r.description,
           SUBSTR(COALESCE(r.body_text, ''), 1, 400) as body_snippet,
           COALESCE(bm.bundle_name, '') as bundle,
           COALESCE(bm.priority, 50) as priority,
           r.token_count, r.cluster
    FROM resources r
    LEFT JOIN bundle_membership bm ON r.id = bm.resource_id AND bm.bundle_kind = 'project_type'
    WHERE r.status = 'active'
      AND r.cluster IN ({cluster_placeholders})
    ORDER BY COALESCE(bm.priority, 50) ASC, r.times_correct DESC
    LIMIT 500
"""

_L2_SQL = """
    SELECT r.id, r.name, r.resource_type, r.body_text,
           COALESCE(bm.priority, 50) / 100.0 as iso_score, r.token_count,
           COALESCE(r.last_used_at, '') as last_used
    FROM resources r
    LEFT JOIN bundle_membership bm ON r.id = bm.resource_id AND bm.is_canonical = 1
    WHERE r.status = 'active' AND r.cluster = ?
    ORDER BY bm.priority DESC NULLS LAST, r.times_correct DESC
    LIMIT ?
"""


# ── DB Helpers ────────────────────────────────────────────────────────────────


def get_ro_conn() -> sqlite3.Connection:
    """Read-only SQLite connection (uri mode)."""
    conn = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro", uri=True, timeout=15, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _get_rw_conn() -> sqlite3.Connection:
    """Read-write SQLite connection."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _ensure_cache_table() -> None:
    conn = _get_rw_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cache_hierarchy (
                layer TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                value BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (layer, cache_key)
            )"""
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _serialize(obj: Any) -> bytes:
    if HAS_MSGPACK:
        return msgpack.packb(obj, default=str)
    return json.dumps(obj, default=str).encode("utf-8")


def _deserialize(data: bytes) -> Any:
    if HAS_MSGPACK:
        return msgpack.unpackb(data)
    return json.loads(data.decode("utf-8"))


def _parse_int(s: str) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


# Ensure cache_hierarchy table exists at import time
_ensure_cache_table()


# ── TTL+LRU Cache ────────────────────────────────────────────────────────────


class TTLLRUCache:
    """Generic TTL + LRU eviction cache.

    Args:
        maxsize: Maximum number of entries before LRU eviction.
        ttl: Time-to-live in seconds for each entry.
    """

    __slots__ = ("_maxsize", "_ttl", "_cache", "_lock")

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._cache:
                return None
            value, expiry = self._cache[key]
            if time.monotonic() > expiry:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            while len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
            self._cache[key] = (value, time.monotonic() + self._ttl)
            self._cache.move_to_end(key)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        with self._lock:
            now = time.monotonic()
            return sum(1 for v in self._cache.values() if v[1] > now)


# ── L0: Eager-loaded Core Cache ──────────────────────────────────────────────


class L0Cache:
    """Eager-loaded from DB on init. Holds tier=1 and core_infra bundle resources.

    Internal: OrderedDict[int, _L0Entry] keyed by resource_id.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[int, _L0Entry] = OrderedDict()
        self._lock = threading.RLock()
        try:
            self.load_from_db()
        except Exception:
            pass

    def get(self, resource_id: int) -> _L0Entry | None:
        with self._lock:
            return self._entries.get(resource_id)

    def get_all(self) -> list[_L0Entry]:
        with self._lock:
            return list(self._entries.values())

    def token_count(self) -> int:
        with self._lock:
            return sum(e.token_count for e in self._entries.values())

    def load_from_db(self) -> None:
        """Load or reload L0 cache from the resources table (fresh query)."""
        conn = get_ro_conn()
        try:
            rows = conn.execute(_L0_SQL).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        entries: OrderedDict[int, _L0Entry] = OrderedDict()
        token_total = 0
        for r in rows:
            tc = r["token_count"] or 0
            if token_total + tc > L0_MAX_TOKENS:
                break
            if len(entries) >= L0_MAX_ENTRIES:
                break
            entry = _L0Entry(
                resource_id=r["id"],
                name=r["name"],
                resource_type=r["resource_type"],
                body_snippet=r["body_snippet"],
                bundle=r["bundle"],
                priority=r["priority"],
                token_count=tc,
            )
            entries[r["id"]] = entry
            token_total += tc

        with self._lock:
            self._entries = entries

    def serialize_to_db(self) -> None:
        """Persist current L0 cache to cache_hierarchy table."""
        with self._lock:
            data = [
                {
                    "resource_id": e.resource_id,
                    "name": e.name,
                    "resource_type": e.resource_type,
                    "body_snippet": e.body_snippet,
                    "bundle": e.bundle,
                    "priority": e.priority,
                    "token_count": e.token_count,
                }
                for e in self._entries.values()
            ]
        blob = _serialize(data)
        conn = _get_rw_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO cache_hierarchy (layer, cache_key, value, updated_at)
                   VALUES ('L0', 'default', ?, datetime('now'))""",
                (blob,),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def _load_from_cache_table(self) -> bool:
        """Try to restore from cache_hierarchy table. Returns True on success."""
        conn = get_ro_conn()
        try:
            row = conn.execute(
                "SELECT value FROM cache_hierarchy WHERE layer='L0' AND cache_key='default'"
            ).fetchone()
            if row is None:
                return False
            data = _deserialize(row["value"])
        except Exception:
            return False
        finally:
            conn.close()

        entries: OrderedDict[int, _L0Entry] = OrderedDict()
        for item in data:
            e = _L0Entry(**item)
            entries[e.resource_id] = e
        with self._lock:
            self._entries = entries
        return True


# ── L1: Lazy-loaded Project Cache ────────────────────────────────────────────


class L1Cache:
    """Lazy-loaded per project_type cache.

    Internal: dict[str, dict[int, _L1Entry]] — project_type → resource_id → entry.
    LRU eviction: keeps the last 2 accessed project types in memory.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[int, _L1Entry]] = {}
        self._lock = threading.RLock()
        self._load_order: list[str] = []

    @staticmethod
    def _clusters_for_type(project_type: str) -> list[str]:
        return [c for c, pt in _CLUSTER_TYPE_MAP.items() if pt == project_type]

    def _evict_lru(self) -> None:
        while len(self._load_order) > 2:
            oldest = self._load_order.pop(0)
            if oldest in self._entries:
                self._serialize_one_to_db(oldest)
                del self._entries[oldest]

    def _serialize_one_to_db(self, project_type: str) -> None:
        pool = self._entries.get(project_type)
        if not pool:
            return
        data = [
            {
                "resource_id": e.resource_id,
                "name": e.name,
                "resource_type": e.resource_type,
                "body_snippet": e.body_snippet,
                "bundle": e.bundle,
                "priority": e.priority,
                "token_count": e.token_count,
                "project_type": e.project_type,
                "loaded_at": e.loaded_at,
                "hit_count": e.hit_count,
            }
            for e in pool.values()
        ]
        blob = _serialize(data)
        conn = _get_rw_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO cache_hierarchy (layer, cache_key, value, updated_at)
                   VALUES ('L1', ?, ?, datetime('now'))""",
                (project_type, blob),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def fill(self, project_type: str, db_path: str | None = None) -> None:
        """Load resources for a project type from DB.

        Queries resources whose cluster maps to the given project type.
        Enforces hard caps: 8192 tokens, 500 entries.
        If db_path is provided, uses it for the connection.
        """
        clusters = self._clusters_for_type(project_type)
        if not clusters:
            with self._lock:
                self._entries[project_type] = {}
                if project_type in self._load_order:
                    self._load_order.remove(project_type)
                self._load_order.append(project_type)
                self._evict_lru()
            return

        placeholders = ','.join('?' for _ in clusters)
        sql = _L1_FILL_SQL_TMPL.format(cluster_placeholders=placeholders)

        conn = get_ro_conn()
        if db_path:
            try:
                conn.close()
                conn = sqlite3.connect(
                    f"file:{db_path}?mode=ro", uri=True, timeout=15, check_same_thread=False
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=15000")
            except Exception:
                pass

        try:
            rows = conn.execute(sql, clusters).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        now = time.time()
        entries: dict[int, _L1Entry] = {}
        token_total = 0
        for r in rows:
            tc = r["token_count"] or 0
            if token_total + tc > L1_MAX_TOKENS:
                break
            if len(entries) >= L1_MAX_ENTRIES:
                break
            entry = _L1Entry(
                resource_id=r["id"],
                name=r["name"],
                resource_type=r["resource_type"],
                body_snippet=r["body_snippet"],
                bundle=r["bundle"],
                priority=r["priority"],
                token_count=tc,
                project_type=project_type,
                loaded_at=now,
                hit_count=0,
            )
            entries[r["id"]] = entry
            token_total += tc

        with self._lock:
            self._entries[project_type] = entries
            if project_type in self._load_order:
                self._load_order.remove(project_type)
            self._load_order.append(project_type)
            self._evict_lru()

    def get(self, project_type: str, resource_id: int) -> _L1Entry | None:
        """O(1) lookup. Does NOT increment hit_count — use hit() for that."""
        with self._lock:
            pool = self._entries.get(project_type)
            if pool is None:
                return None
            return pool.get(resource_id)

    def get_bundle(self, project_type: str) -> dict[int, _L1Entry]:
        """Returns all entries for a project type, or {} if not loaded."""
        with self._lock:
            pool = self._entries.get(project_type)
            if pool is None:
                return {}
            return dict(pool)

    def swap(self, project_type: str) -> None:
        """Keep only this project_type in memory, evict others.

        Serializes old bundles to DB before clearing.
        """
        with self._lock:
            for pt in list(self._entries.keys()):
                if pt != project_type:
                    self._serialize_one_to_db(pt)
            self._entries.clear()
            self._load_order.clear()
        self.fill(project_type)

    def token_count(self, project_type: str) -> int:
        """Sum of token_count for loaded entries of this project type."""
        with self._lock:
            pool = self._entries.get(project_type)
            if pool is None:
                return 0
            return sum(e.token_count for e in pool.values())

    def loaded_project_types(self) -> list[str]:
        """Which project types are currently loaded in memory."""
        with self._lock:
            return list(self._entries.keys())

    def hit(self, project_type: str, resource_id: int) -> None:
        """Increment hit_count for an entry."""
        with self._lock:
            pool = self._entries.get(project_type)
            if pool is None:
                return
            entry = pool.get(resource_id)
            if entry is not None:
                pool[resource_id] = entry._replace(hit_count=entry.hit_count + 1)

    def serialize_to_db(self, sqlite_conn: sqlite3.Connection | None = None) -> None:
        """Persist to cache_hierarchy table.

        If sqlite_conn is provided, uses it (caller manages commit/close).
        Otherwise, manages its own connection per project type.
        """
        if sqlite_conn is not None:
            with self._lock:
                for pt, pool in self._entries.items():
                    data = [
                        {
                            "resource_id": e.resource_id,
                            "name": e.name,
                            "resource_type": e.resource_type,
                            "body_snippet": e.body_snippet,
                            "bundle": e.bundle,
                            "priority": e.priority,
                            "token_count": e.token_count,
                            "project_type": e.project_type,
                            "loaded_at": e.loaded_at,
                            "hit_count": e.hit_count,
                        }
                        for e in pool.values()
                    ]
                    blob = _serialize(data)
                    try:
                        sqlite_conn.execute(
                            """INSERT OR REPLACE INTO cache_hierarchy (layer, cache_key, value, updated_at)
                               VALUES ('L1', ?, ?, datetime('now'))""",
                            (pt, blob),
                        )
                    except Exception:
                        pass
            return

        with self._lock:
            for pt in list(self._entries.keys()):
                self._serialize_one_to_db(pt)

    def load_from_db(self, sqlite_conn: sqlite3.Connection | None = None) -> None:
        """Restore from cache_hierarchy table.

        If sqlite_conn is provided, uses it (caller manages).
        Otherwise, creates its own read-only connection.
        """
        if sqlite_conn is not None:
            try:
                rows = sqlite_conn.execute(
                    "SELECT cache_key, value FROM cache_hierarchy WHERE layer='L1'"
                ).fetchall()
            except Exception:
                rows = []
        else:
            conn = get_ro_conn()
            try:
                rows = conn.execute(
                    "SELECT cache_key, value FROM cache_hierarchy WHERE layer='L1'"
                ).fetchall()
            except Exception:
                rows = []
            finally:
                conn.close()

        restored: dict[str, dict[int, _L1Entry]] = {}
        for row in rows:
            try:
                data = _deserialize(row["value"])
                pool: dict[int, _L1Entry] = {}
                for item in data:
                    e = _L1Entry(**item)
                    pool[e.resource_id] = e
                restored[row["cache_key"]] = pool
            except Exception:
                pass
        if restored:
            with self._lock:
                self._entries = restored
                self._load_order = list(restored.keys())

    def clear(self) -> None:
        """Evict all project type bundles from memory."""
        with self._lock:
            self._entries.clear()
            self._load_order.clear()


# ── L2: Cluster-activated Cache ──────────────────────────────────────────────


class L2Cache:
    """Loaded on cluster activation.

    Internal: dict[str, dict[int, _L2Entry]] — cluster → resource_id → entry.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[int, _L2Entry]] = {}
        self._lock = threading.RLock()

    def activate(self, cluster_names: list[str]) -> None:
        """Load entries for given clusters from DB."""
        conn = get_ro_conn()
        try:
            for cluster in cluster_names:
                if cluster in self._entries:
                    continue
                rows = conn.execute(_L2_SQL, (cluster, L2_MAX_ENTRIES)).fetchall()
                pool: dict[int, _L2Entry] = {}
                token_total = 0
                for r in rows:
                    tc = r["token_count"] or 0
                    if token_total + tc > L2_MAX_TOKENS:
                        break
                    if len(pool) >= L2_MAX_ENTRIES:
                        break
                    entry = _L2Entry(
                        resource_id=r["id"],
                        name=r["name"],
                        resource_type=r["resource_type"],
                        body_text=r["body_text"],
                        iso_score=r["iso_score"],
                        token_count=tc,
                        last_used=r["last_used"],
                    )
                    pool[r["id"]] = entry
                    token_total += tc
                with self._lock:
                    self._entries[cluster] = pool
        except Exception:
            pass
        finally:
            conn.close()

    def get(self, cluster: str, resource_id: int) -> _L2Entry | None:
        with self._lock:
            pool = self._entries.get(cluster)
            if pool is None:
                return None
            return pool.get(resource_id)

    def get_all_for_cluster(self, cluster: str) -> list[_L2Entry]:
        with self._lock:
            pool = self._entries.get(cluster)
            if pool is None:
                return []
            return list(pool.values())

    def token_count(self, cluster: str) -> int:
        with self._lock:
            pool = self._entries.get(cluster)
            if pool is None:
                return 0
            return sum(e.token_count for e in pool.values())

    def invalidate(self, cluster: str | None = None) -> None:
        """Invalidate a specific cluster or all clusters if None."""
        with self._lock:
            if cluster is None:
                self._entries.clear()
            else:
                self._entries.pop(cluster, None)

    def serialize_to_db(self) -> None:
        """Persist all L2 cluster caches to cache_hierarchy table."""
        with self._lock:
            for cluster, pool in self._entries.items():
                data = [
                    {
                        "resource_id": e.resource_id,
                        "name": e.name,
                        "resource_type": e.resource_type,
                        "body_text": e.body_text,
                        "iso_score": e.iso_score,
                        "token_count": e.token_count,
                        "last_used": e.last_used,
                    }
                    for e in pool.values()
                ]
                blob = _serialize(data)
                conn = _get_rw_conn()
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO cache_hierarchy (layer, cache_key, value, updated_at)
                           VALUES ('L2', ?, ?, datetime('now'))""",
                        (cluster, blob),
                    )
                    conn.commit()
                except Exception:
                    pass
                finally:
                    conn.close()

    def load_from_db(self) -> None:
        """Restore all L2 cluster caches from cache_hierarchy table."""
        conn = get_ro_conn()
        try:
            rows = conn.execute(
                "SELECT cache_key, value FROM cache_hierarchy WHERE layer='L2'"
            ).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        restored: dict[str, dict[int, _L2Entry]] = {}
        for row in rows:
            try:
                data = _deserialize(row["value"])
                pool: dict[int, _L2Entry] = {}
                for item in data:
                    e = _L2Entry(**item)
                    pool[e.resource_id] = e
                restored[row["cache_key"]] = pool
            except Exception:
                pass
        if restored:
            with self._lock:
                self._entries = restored


# ── L3: Per-task Query Cache ──────────────────────────────────────────────────


class L3Cache:
    """Per-task query result cache backed by TTLLRUCache.

    Keys are truncated MD5 hashes of the query string.
    """

    def __init__(self) -> None:
        self._cache = TTLLRUCache(maxsize=L3_MAX_ENTRIES, ttl=L3_TTL)

    @staticmethod
    def _hash(query: str, project: str = "", task_type: str = "") -> str:
        scope = f"{project}:{task_type}:{query}" if project or task_type else query
        return hashlib.md5(scope.encode("utf-8")).hexdigest()[:16]

    def get(
        self,
        query: str,
        *,
        project: str = "",
        task_type: str = "",
    ) -> list[dict] | None:
        """Return cached iso_scores for query, or None on miss/expiry."""
        key = self._hash(query, project, task_type)
        entry = self._cache.get(key)
        if entry is None:
            return None
        iso_scores, total_tokens, created_at, hit_count = entry
        updated = (iso_scores, total_tokens, created_at, hit_count + 1)
        self._cache.set(key, updated)
        return iso_scores

    def set(
        self,
        query: str,
        iso_scores: list[dict],
        *,
        project: str = "",
        task_type: str = "",
    ) -> None:
        """Cache iso_scores for query. Drops oldest if over capacity."""
        key = self._hash(query, project, task_type)
        total_tokens = sum(s.get("token_count", 0) for s in iso_scores)
        if total_tokens > L3_MAX_TOKENS:
            idx = 0
            running = 0
            for i, s in enumerate(iso_scores):
                running += s.get("token_count", 0)
                if running > L3_MAX_TOKENS:
                    idx = i
                    break
            else:
                idx = len(iso_scores)
            iso_scores = iso_scores[:idx]
            total_tokens = sum(s.get("token_count", 0) for s in iso_scores)
        entry = (iso_scores, total_tokens, time.time(), 0)
        self._cache.set(key, entry)

    def invalidate(self, query: str) -> None:
        key = self._hash(query)
        self._cache.invalidate(key)

    def clear(self) -> None:
        self._cache.clear()

    def token_count(self, query: str) -> int:
        """Return cached token count for a query, or 0 if not cached."""
        key = self._hash(query)
        entry = self._cache.get(key)
        if entry is None:
            return 0
        return entry[1]

    def __len__(self) -> int:
        return len(self._cache)


# ── Singleton Accessors ──────────────────────────────────────────────────────

_l0_cache: L0Cache | None = None
_l1_cache: L1Cache | None = None
_l2_cache: L2Cache | None = None
_l3_cache: L3Cache | None = None
_singleton_lock = threading.RLock()


def get_l0_cache() -> L0Cache:
    global _l0_cache
    if _l0_cache is None:
        with _singleton_lock:
            if _l0_cache is None:
                _l0_cache = L0Cache()
    return _l0_cache


def get_l1_cache() -> L1Cache:
    global _l1_cache
    if _l1_cache is None:
        with _singleton_lock:
            if _l1_cache is None:
                _l1_cache = L1Cache()
    return _l1_cache


def get_l2_cache() -> L2Cache:
    global _l2_cache
    if _l2_cache is None:
        with _singleton_lock:
            if _l2_cache is None:
                _l2_cache = L2Cache()
    return _l2_cache


def get_l3_cache() -> L3Cache:
    global _l3_cache
    if _l3_cache is None:
        with _singleton_lock:
            if _l3_cache is None:
                _l3_cache = L3Cache()
    return _l3_cache


__all__ = [
    "_L0Entry",
    "_L1Entry",
    "_L2Entry",
    "_L3Entry",
    "TTLLRUCache",
    "L0Cache",
    "L1Cache",
    "L2Cache",
    "L3Cache",
    "get_l0_cache",
    "get_l1_cache",
    "get_l2_cache",
    "get_l3_cache",
    "get_ro_conn",
    "L0_MAX_TOKENS",
    "L0_MAX_ENTRIES",
    "L1_MAX_TOKENS",
    "L1_MAX_ENTRIES",
    "L2_MAX_TOKENS",
    "L2_MAX_ENTRIES",
    "L3_MAX_TOKENS",
    "L3_MAX_ENTRIES",
    "L3_TTL",
]
