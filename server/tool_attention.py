"""NEXUS v3 Tool Attention Router - complete integration."""
from server.db import get_db_path as _resolve_db_path
import os, json, sqlite3, threading, numpy as np, faiss
from pathlib import Path
from typing import Optional
from sentence_transformers import SentenceTransformer

from lazy_loader import LazyLoader
from cross_type_router import CrossTypeRouter

DB_PATH = _resolve_db_path()
INDEX_PATH = Path.home() / "nexus/db/faiss/resources.index"
DIM = 384
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
SIM_THRESHOLD = 0.30

class ToolAttention:
    _instance = None

    def __init__(self):
        self.model = None
        self.index = None
        self.resource_ids = []
        self.conn = None
        self.loader = LazyLoader(str(DB_PATH))
        self.cross_router = CrossTypeRouter(str(DB_PATH), str(INDEX_PATH))

    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "ToolAttention":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def load(self):
        if self.model is None:
            os.environ["TOKENIZERS_PARALLELISM"] = "false"
            shared = getattr(sys, "_nexus_shared_models", None)
            if shared is not None and MODEL_ID in shared:
                self.model = shared[MODEL_ID]
            else:
                try:
                    self.model = SentenceTransformer(MODEL_ID, local_files_only=True)
                except TypeError:
                    self.model = SentenceTransformer(MODEL_ID)
                if shared is not None:
                    shared[MODEL_ID] = self.model
        if self.index is None:
            self.index = faiss.read_index(str(INDEX_PATH))
            with open(str(INDEX_PATH) + ".meta.json") as f:
                self.resource_ids = json.load(f)["resource_ids"]
        if self.conn is None:
            self.conn = sqlite3.connect(str(DB_PATH), timeout=60, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        try:
            self.load()
            qvec = self.model.encode([query]).astype(np.float32)
            faiss.normalize_L2(qvec)
            dists, idxs = self.index.search(qvec, k)
            return [
                (self.resource_ids[i], float(d))
                for d, i in zip(dists[0], idxs[0])
                if i < len(self.resource_ids)
            ]
        except Exception:
            return []

    def get_candidates(self, query: str, top_k: int = 10) -> list[int]:
        results = self.search(query, top_k)
        return [rid for rid, score in results if score >= SIM_THRESHOLD]

    def find_resources(
        self,
        task: str,
        project: str = "default",
        resource_types: Optional[list[str]] = None,
        max_candidates: int = 20,
        load_bodies: bool = False,
        require_active: bool = True,
    ) -> list[dict]:
        if resource_types is None:
            result = self.cross_router.route(
                task, project=project, resource_types=None,
                top_k=max_candidates, min_score=SIM_THRESHOLD,
            )
            return result.get("matches", [])

        try:
            self.load()
        except Exception:
            # Semantic stack unavailable: fallback through cross-type lexical route.
            result = self.cross_router.route(
                task,
                project=project,
                resource_types=resource_types,
                top_k=max_candidates,
                min_score=0.2,
            )
            return result.get("matches", [])
        candidate_ids = self.get_candidates(task, top_k=max_candidates * 2)
        if not candidate_ids:
            return []

        placeholders = ",".join("?" * len(candidate_ids))
        where_parts = [f"id IN ({placeholders})"]
        params = list(candidate_ids)
        if resource_types:
            t_ph = ",".join("?" * len(resource_types))
            where_parts.append(f"resource_type IN ({t_ph})")
            params.extend(resource_types)
        if require_active:
            where_parts.append("status = 'active' OR status IS NULL")

        sql = f"""
            SELECT id, name, description, body_text, resource_type, tier,
                   times_triggered, times_correct, times_wrong, token_count, file_path
            FROM resources WHERE {" AND ".join(where_parts)}
        """
        rows = self.conn.execute(sql, params).fetchall()

        scored = []
        for row in rows:
            rid = row[0]
            rtype = row[4]

            # Use lazy loader summary
            summary = self.loader.get_summary(rid)
            if summary is None:
                # not yet cached, create from row
                summary = {
                    "id": rid, "name": row[1], "description": (row[2] or "")[:500],
                    "resource_type": rtype, "tier": row[5] or 2,
                    "times_triggered": row[6] or 0, "times_correct": row[7] or 0,
                    "times_wrong": row[8] or 0, "token_count": row[9] or 0,
                    "file_path": row[10] or "", "loaded": False,
                }
                self.loader._summary_pool[rid] = summary

            # Schema promotion
            schema_hint = self.loader.needs_schema(summary, task)

            # Scoring
            sim_score = SIM_THRESHOLD
            desc_len = len((row[2] or ""))
            if desc_len > 50:
                sim_score += 0.2
            if desc_len > 200:
                sim_score += 0.1
            s_rate = 0
            if (row[6] or 0) > 0:
                s_rate = (row[7] or 0) / row[6]
            sim_score += s_rate * 0.3
            sim_score = min(sim_score, 1.0)

            scored.append({
                "id": rid, "name": summary["name"],
                "description": summary["description"],
                "body_text": None, "body_loaded": False,
                "resource_type": rtype, "tier": summary["tier"],
                "score": round(sim_score, 3),
                "times_triggered": summary["times_triggered"],
                "times_correct": summary["times_correct"],
                "times_wrong": summary["times_wrong"],
                "token_count": summary["token_count"],
                "file_path": summary["file_path"],
                "schema_hint": schema_hint,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)

        # Lazy body loading for top 5
        if load_bodies and scored:
            for entry in scored[:5]:
                full = self.loader.load_body(entry["id"])
                if full:
                    entry["body_text"] = full.get("body_text", "")
                    entry["body_loaded"] = True

        # Category labels
        for s in scored:
            s["category"] = "INVOKE" if s["score"] >= 0.68 else "REVIEW" if s["score"] >= 0.45 else "SKIP"

        return scored[:max_candidates]

    def route_v3(self, task: str, project: str = "default") -> dict:
        candidates = self.find_resources(task, project, max_candidates=20)
        top = candidates[0] if candidates else None

        # Determine tier from top candidate
        if top:
            tier = top["tier"]
        else:
            tier = 2

        tier_config = {
            1: {"name": "T1 Fast", "model": "gemini-3-flash-preview:cloud", "limit": 512},
            2: {"name": "T2 Standard", "model": "devstral-small-2:24b-cloud", "limit": 2048},
            3: {"name": "T3 Complex", "model": "qwen3-coder:480b-cloud", "limit": 0},
        }
        tc = tier_config.get(tier, tier_config[2])

        return {
            "task": task, "project": project, "method": "tool-attention-v3",
            "tier": tier, "tier_name": tc["name"], "model": tc["model"],
            "token_limit": tc["limit"], "candidates": candidates,
            "candidate_count": len(candidates),
            "loader_stats": self.loader.get_stats(),
        }

    def route_universal(self, task: str, project: str = "default",
                        types: Optional[list[str]] = None) -> dict:
        result = self.cross_router.route(task, project, resource_types=types, top_k=30)
        return {
            "task": task, "project": project, "method": "universal-v3",
            "total_candidates": result["total_scanned"],
            "matches": result["matches"],
            "latency_ms": result["latency_ms"],
            "loader_stats": self.loader.get_stats(),
        }

    def get_skill_text(self, resource_id: int) -> Optional[str]:
        full = self.loader.load_body(resource_id)
        return full["body_text"] if full else None

    def get_pool_stats(self) -> dict:
        return self.loader.get_stats()

    def clear_cache(self):
        self.loader.clear()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None


# Module-level convenience
TA = ToolAttention.get()

def route_v3(task: str, project: str = "default") -> dict:
    return TA.route_v3(task, project)

def route_universal(task: str, project: str = "default", types=None) -> dict:
    return TA.route_universal(task, project, types)

def find_resources(task: str, project: str = "default",
                  resource_types=None, max_candidates: int = 20) -> list[dict]:
    return TA.find_resources(task, project, resource_types=resource_types, max_candidates=max_candidates)
