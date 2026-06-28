"""NEXUS v3 Cross-Type Routing — ToolkenGPT-inspired type-aware universal search.
Finds the best resources across ALL types (not just skills), with
weighted scoring (70% semantic, 30% type preference).
"""
from __future__ import annotations
import math, sqlite3, threading, os, numpy as np
from pathlib import Path
from typing import Optional
from collections import defaultdict
from iso_embedder import IsoEmbedder, EmbedderUnavailableError

DB_PATH = os.environ.get("NEXUS_DB_PATH", str(Path.home() / "nexus/db/nexus.db"))
INDEX_PATH = os.environ.get(
    "NEXUS_INDEX_PATH",
    str(Path.home() / "nexus/db/faiss/resources.index"),
)

# ── Type directional vectors ──────────────────────────────────────────
# 8 base types × 4-dim directional embeddings (ToolkenGPT-like)
TYPE_VECTORS = {
    "skill": [+1.0, +1.0, +1.0, +1.0],
    "command": [+1.0, -1.0, +1.0, -1.0],
    "design_system": [+1.0, +1.0, -1.0, -1.0],
    "agent": [+1.0, -1.0, -1.0, +1.0],
    "hook": [-1.0, +1.0, +1.0, -1.0],
    "cli_tool": [-1.0, +1.0, -1.0, +1.0],
    "mcp": [-1.0, -1.0, +1.0, +1.0],
    "craft": [-1.0, -1.0, -1.0, -1.0],
    "doc": [+1.0, +1.0, +1.0, -1.0],
    "spec": [+1.0, -1.0, -1.0, -1.0],
}

def normalize(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(v * v for v in vec))
    return [v / mag for v in vec] if mag > 0 else vec

TYPE_VECTORS = {k: normalize(v) for k, v in TYPE_VECTORS.items()}

# ── Cross-Type Router ─────────────────────────────────────────────────
class CrossTypeRouter:
    """Routes across all 1097 resources using semantic + type-aware scoring."""

    def __init__(self, db_path: str = DB_PATH, index_path: str = INDEX_PATH):
        self.db_path = db_path
        self.index_path = index_path
        self.embedder = IsoEmbedder.get(db_path, model_id="sentence-transformers/all-MiniLM-L6-v2")
        self._conn = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=60, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _score(self,
               resource_type: str,
               semantic_score: float,
               desired_types: list[str] = None) -> float:
        """Weighted score: 70% semantic + 30% type preference."""
        type_vector = TYPE_VECTORS.get(resource_type, [0, 0, 0, 0])
        if desired_types is None:
            desired_types = ["skill", "command", "design_system", "agent"]
        # Type preference: boost if this resource_type is in the desired set
        desired_boost = 1.0 if resource_type in desired_types else 0.5
        type_boost = 0.30 * desired_boost
        return 0.70 * semantic_score + type_boost

    def route(self,
              task: str,
              project: str = "default",
              resource_types: list[str] = None,
              top_k: int = 20,
              min_score: float = 0.45) -> dict:
        """Universal route across ALL 1097 resources.

        Returns: {"matches": [...], "total_scanned": n, "latency_ms": ...}
        """
        import time
        start = time.perf_counter()

        # 1. Get candidates from FAISS (offline-safe). Fallback to keyword-only if unavailable.
        degraded_mode = False
        degraded_reason = None
        raw = []
        candidate_ids = []
        try:
            self.embedder.load(self.index_path)
            raw = self.embedder.search(task, k=top_k * 2)  # oversample
            candidate_ids = [rid for rid, score in raw if rid is not None]
        except (EmbedderUnavailableError, OSError, RuntimeError, ValueError) as e:
            degraded_mode = True
            degraded_reason = str(e)

        # 2. Fetch metadata for candidates
        conn = self._connection()
        if candidate_ids:
            placeholders = ",".join("?" * len(candidate_ids))
            where = f"WHERE id IN ({placeholders})"
            if resource_types:
                t = ",".join("?" * len(resource_types))
                where += f" AND resource_type IN ({t})"
                params = candidate_ids + resource_types
            else:
                params = candidate_ids
        else:
            where = "WHERE status = 'active'"
            params = []
            if resource_types:
                t = ",".join("?" * len(resource_types))
                where += f" AND resource_type IN ({t})"
                params.extend(resource_types)

        cur = conn.execute(
            "SELECT id, name, description, body_text, resource_type, tier, "
            "       times_triggered, times_correct, times_wrong, token_count, file_path, status "
            f"FROM resources {where}",
            params,
        )

        # 3. Score and rank
        matches = []
        semantic_map = dict(raw)
        for row in cur.fetchall():
            rid = row["id"]
            rtype = row["resource_type"]
            if degraded_mode:
                # Lightweight lexical fallback score when embeddings are unavailable.
                q_words = set(task.lower().split())
                text = f"{row['name'] or ''} {row['description'] or ''}".lower()
                overlap = len(q_words & set(text.split()))
                sem_score = min(0.2 + overlap * 0.08, 0.8)
            else:
                sem_score = min(semantic_map.get(rid, 0), 1.0)
            score = self._score(rtype, sem_score, resource_types)
            if score < min_score:
                continue
            matches.append({
                "id": rid,
                "name": row["name"],
                "description": (row["description"] or "")[:200],
                "resource_type": rtype,
                "tier": row["tier"] or 2,
                "times_triggered": row["times_triggered"] or 0,
                "times_correct": row["times_correct"] or 0,
                "times_wrong": row["times_wrong"] or 0,
                "token_count": row["token_count"] or 0,
                "status": row["status"],
                "score": round(score, 3) if isinstance(score, float) else score,
            })

        # Sort by score
        matches.sort(key=lambda x: x["score"], reverse=True)
        matches = matches[:top_k]

        # Category labels
        for m in matches:
            m["category"] = "INVOKE" if m["score"] >= 0.68 else "REVIEW" if m["score"] >= 0.45 else "SKIP"

        latency = (time.perf_counter() - start) * 1000
        return {
            "task": task,
            "project": project,
            "method": "cross-type",
            "total_scanned": len(candidate_ids),
            "top_k": top_k,
            "matches": matches,
            "latency_ms": round(latency, 2),
            "degraded_mode": degraded_mode,
            "degraded_reason": degraded_reason,
        }

    def route_universal(self,
                       query: str,
                       project: str = "default",
                       top_k: int = 20,
                       min_score: float = 0.30) -> dict:
        """Alias for route() without type filter. Searches ALL 1097 resources."""
        return self.route(query, project, resource_types=None, top_k=top_k, min_score=min_score)

    def route_skills_only(self,
                         query: str,
                         project: str = "default",
                         top_k: int = 5) -> dict:
        """v2 fallback: Only search skills (resource_type='skill')."""
        return self.route(query, project, resource_types=["skill"], top_k=top_k)


_cross_type_router_lock = threading.Lock()

def get_cross_type_router(db_path: str = DB_PATH, index_path: str = INDEX_PATH) -> CrossTypeRouter:
    if not hasattr(get_cross_type_router, "_instance"):
        with _cross_type_router_lock:
            if not hasattr(get_cross_type_router, "_instance"):
                get_cross_type_router._instance = CrossTypeRouter(db_path, index_path)
    return get_cross_type_router._instance
