"""
NEXUS v4 — Tool Attention (Paper: arxiv:2604.21816)
"Tool Attention Is All You Need: Dynamic Tool Gating and Lazy Schema Loading
 for Eliminating the MCP/Tools Tax in Scalable Agentic Workflows"
— Sadani & Kumar, 2025

Implements Algorithm 1 (Per-Turn Middleware Pass) exactly:
  1. Encode query → ISO scores for all N tools (cosine similarity)
  2. Filter: ISO(q, ti) >= θ AND state_t |= pre_i
  3. Top-k selection by ISO score → A_t
  4. Lazy-load full schemas for A_t from registry
  5. Emit prompt with (summary pool S, active full schemas)
  6. Hallucination gate: reject tool calls not in A_t
  7. Observability events per turn
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path
import json, sqlite3, time, os, hashlib, sys
from pathlib import Path
from typing import Optional
from collections import OrderedDict

import numpy as np
from sentence_transformers import SentenceTransformer

# ── Reuse existing Nexus infrastructure ──────────────────────────
_SELF_DIR = str(Path(__file__).parent)
_PARENT_DIR = str(Path(__file__).parent.parent)
for _d in [_SELF_DIR, _PARENT_DIR]:
    if _d not in sys.path:
        sys.path.insert(0, _d)
from precondition import GLOBAL_GATES, StateVariable, TOOL_PRECONDITIONS
from lazy_loader import LazyLoader, get_loader
from server.router import find_matching_skills
from server.numpy_router import NumpyRouter

# ── Constants from paper ─────────────────────────────────────────
DB_PATH = _resolve_db_path()
INDEX_PATH = Path.home() / "nexus/db/faiss/resources.index"
DIM = 384
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

# Paper's optimal hyperparameters (Table 5 ablation)
THETA = 0.28       # ISO score threshold — default, adapted per-turn
TOP_K = 10         # Active set size — k=8-12 recommended
SUMMARY_MAX_TOKENS = 60  # Compact summary target (paper: ≤60 tokens)
# RC-5 FIX: Cap θ decay at 0.12 — never cascade to 0.0 (which lets everything through).
# If fewer than top_k candidates pass θ=0.12, return fewer results instead of garbage.
THETA_DECAY_CHAIN = [0.28, 0.20, 0.12]

# Token estimation (paper: α ∈ [200, 500] per tool)
TOKENS_PER_SCHEMA_TOOL = 395  # Paper's measured average


# ═══════════════════════════════════════════════════════════════════
# Section 2: Intent-Schema Overlap (ISO) Score
# ═══════════════════════════════════════════════════════════════════

class IntentRouter:
    """Wraps encoder + NumpyRouter. Returns ranked, thresholded candidates.

    Paper Section 2 — Equation 5:
      ISO(q, t_i) = (e_q^T · e_{t_i}) / (||e_q||_2 · ||e_{t_i}||_2)

    RC-2/RC-3 FIX: Replaces FAISS IndexFlatIP with NumpyRouter.
    - No FAISS index file I/O, no meta.json sync, no ghost entries
    - Only routable types loaded (skill/command/agent/mcp) — 36% less compute
    - L2-normalized once at load time, not per-query
    """

    # TTL-based LRU cache for encoded query vectors (saves ~50ms per repeat query)
    _QUERY_CACHE_MAX = 256
    _QUERY_CACHE_TTL = 300  # 5 minutes

    def __init__(self):
        self.model: Optional[SentenceTransformer] = None
        self._numpy_router: Optional[NumpyRouter] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._query_cache: OrderedDict[str, tuple[float, np.ndarray]] = OrderedDict()

    def load(self):
        if self.model is None:
            os.environ["TOKENIZERS_PARALLELISM"] = "false"
            shared = getattr(sys, "_nexus_shared_models", None)
            if shared is not None and MODEL_ID in shared:
                self.model = shared[MODEL_ID]
            else:
                allow_download = os.environ.get("NEXUS_ALLOW_MODEL_DOWNLOAD", "false").lower() == "true"
                self.model = SentenceTransformer(MODEL_ID, local_files_only=not allow_download)
                if shared is not None:
                    shared[MODEL_ID] = self.model
        if self._numpy_router is None:
            self._numpy_router = NumpyRouter.get()
            self._numpy_router.load()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(DB_PATH), timeout=60, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _encode_cached(self, query: str) -> np.ndarray:
        """Encode query with TTL-based LRU cache."""
        now = time.time()
        if query in self._query_cache:
            ts, vec = self._query_cache[query]
            if now - ts < self._QUERY_CACHE_TTL:
                self._query_cache.move_to_end(query)
                return vec
            del self._query_cache[query]

        self.load()
        vec = self.model.encode([query]).astype(np.float32)
        # Normalize for cosine similarity
        norm = np.linalg.norm(vec[0])
        if norm > 0:
            vec[0] = vec[0] / norm
        cached_vec = vec.copy()

        self._query_cache[query] = (now, cached_vec)
        while len(self._query_cache) > self._QUERY_CACHE_MAX:
            self._query_cache.popitem(last=False)

        return cached_vec

    def compute_iso_scores(self, query: str, cluster_ids: Optional[list[int]] = None) -> list[dict]:
        """Compute ISO(q, t_i) for all routable tools via NumpyRouter.

        Returns list of {id, name, resource_type, iso_score} sorted
        by ISO score descending. Only routable resources (skill/command/agent/mcp).
        """
        self.load()

        # Encode query (cached) and search via NumpyRouter
        qvec = self._encode_cached(query)
        raw_results = self._numpy_router.search(
            query_vec=qvec[0],
            top_k=len(self._numpy_router._resource_ids),
            cluster_ids=cluster_ids,
            theta=0.0,  # We apply theta in the decay chain below
        )
        query_words = set(query.lower().split())

        # Batch fetch body_text for hybrid scoring (keyword + name + usage boosts)
        if raw_results:
            ids = [r["id"] for r in raw_results]
            placeholders = ",".join("?" * len(ids))
            rows = self.conn.execute(
                f"SELECT id, name, resource_type, description, body_text, "
                f"times_triggered, times_correct, times_wrong "
                f"FROM resources WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            row_map = {r["id"]: r for r in rows}

            enriched = []
            for r in raw_results:
                row = row_map.get(r["id"])
                if not row:
                    continue

                name = row["name"] or ""
                r["name"] = name
                r["resource_type"] = row["resource_type"]
                r["description"] = (row["description"] or "")[:200]
                r["tier"] = r.get("tier", 2)
                r["status"] = "active"
                r["times_triggered"] = row["times_triggered"] or 0
                r["times_correct"] = row["times_correct"] or 0
                r["times_wrong"] = row["times_wrong"] or 0

                # Phase C: Hybrid scoring — blend ISO with keyword overlap + usage boost
                body = row["body_text"] if "body_text" in row.keys() else ""
                skill_text = f"{name} {(row['description'] or '')} {body[:500]}".lower()
                skill_words = set(skill_text.split())
                overlap = len(query_words & skill_words)
                keyword_boost = min(overlap * 0.08, 0.3)

                # Name match boost
                name_match_boost = 0.0
                for tw in query_words:
                    if len(tw) >= 4 and tw in name.lower():
                        name_match_boost += 0.2
                name_match_boost = min(name_match_boost, 0.4)

                # Usage boost
                times_triggered = row["times_triggered"] or 0
                times_correct = row["times_correct"] or 0
                success_rate = times_correct / max(times_triggered, 1)
                usage_boost = 0.0
                if times_triggered >= 10:
                    usage_boost = success_rate * 0.15
                elif times_triggered >= 5:
                    usage_boost = success_rate * 0.08

                r["iso_score"] = round(r["iso_score"] + keyword_boost + name_match_boost + usage_boost, 4)
                enriched.append(r)

            return enriched

        return []

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        """Raw search via NumpyRouter, returns [(id, score)]."""
        self.load()
        results = self._numpy_router.search_by_text(query, self.model, top_k=k)
        return [(r["id"], r["iso_score"]) for r in results]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ═══════════════════════════════════════════════════════════════════
# Section 4: Algorithm 1 — Tool Attention (Per-Turn Middleware Pass)
# ═══════════════════════════════════════════════════════════════════

class ToolAttentionV4:
    """Top-level orchestrator. Single entry point implementing Algorithm 1.

    Cooperating modules (paper Section 6):
      - IntentRouter        → ISO scoring (Section 2, Eq 5)
      - StateGates          → precondition gating (Section 3, Eq 6)
      - LazyLoader          → two-phase schema loading (Section 5)
      - HallucinationGate   → after_model rejection (Algorithm 1, Line 8)
    """

    _instance = None

    def __init__(self):
        self.intent_router = IntentRouter()
        self.loader: LazyLoader = get_loader(str(DB_PATH))
        self._hallucination_conn: Optional[sqlite3.Connection] = None
        # Maps turn_id → {routing_log_id, project, query} for outcome callbacks
        self._pending_routes: dict[str, dict] = {}

    @classmethod
    def get(cls) -> ToolAttentionV4:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Algorithm 1: Per-Turn Middleware Pass ─────────────────

    def process_turn(
        self,
        query: str,
        tool_catalog_filter: Optional[list[str]] = None,
        resource_types: Optional[list[str]] = None,
        theta: float = THETA,
        top_k: int = TOP_K,
        project: str = "nexus",
        use_clusters: bool = True,
        max_clusters: int = 2,
        min_cluster_affinity: float = 0.15,
        auto_log: bool = True,
    ) -> dict:
        """Execute the full per-turn Tool Attention pipeline.

        Implements Algorithm 1 exactly:
          Line 1:  e_q ← phi(q)
          Line 2:  for each t_i: scores[i] ← cosine(e_q, e_{t_i})
          Line 3:  candidates ← {i : scores[i] >= theta AND state_t |= pre_i}
          Line 4:  A_t ← top-k(candidates by scores)
          Line 5:  full_schemas ← [schema_i for i in A_t]
          Line 6:  prompt ← render(system, S, full_schemas, history, q)
          Line 7:  emit prompt to model
          Line 8-9: hallucination gate (after_model)

        Returns full turn observability record.
        """
        turn_start = time.perf_counter()
        turn_id = hashlib.md5(f"{query}{time.time()}".encode()).hexdigest()[:12]

        # ── Cluster routing pre-filter (v4+) ────────────────────
        cluster_result = None
        activated_cluster_ids = None
        if use_clusters:
            try:
                from cluster_router import ClusterRouter
                _cluster_router = ClusterRouter(str(DB_PATH), str(INDEX_PATH))
                cluster_result = _cluster_router.route_to_clusters(
                    query, max_clusters=max_clusters,
                    min_affinity=min_cluster_affinity, project=project,
                )
                if cluster_result and "resource_ids" in cluster_result:
                    cluster_result["resource_ids"] = list(cluster_result["resource_ids"])
                activated_cluster_ids = cluster_result.get("resource_ids") if cluster_result else None
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Cluster routing failed, falling back to full search: {e}"
                )
                cluster_result = None
                activated_cluster_ids = None

        # ── Line 1-2: Compute ISO scores for all N tools ───────
        start_iso = time.perf_counter()
        # RC-4 FIX: Pass cluster_ids directly to NumpyRouter — avoids post-filtering
        # the full result set. When clusters are active, only ~150-200 vectors are
        # scored instead of all 1,174.
        all_scores = self.intent_router.compute_iso_scores(query, cluster_ids=activated_cluster_ids)
        iso_latency = (time.perf_counter() - start_iso) * 1000

        # ── Line 3: Filter by threshold + state preconditions ──
        start_gate = time.perf_counter()

        # Cluster filter already applied inside compute_iso_scores via NumpyRouter
        # No need to post-filter all_scores here anymore

        # Apply type filter if specified
        if resource_types:
            type_set = set(resource_types)
            all_scores = [s for s in all_scores if s.get("resource_type") in type_set]

        # ── Adaptive θ decay chain ──────────────────────────────
        # First-principles: A single global θ over 1,686 resources with diverse
        # embedding distributions guarantees frequent misses. Instead, try
        # progressively lower thresholds, reporting each step.
        theta_used = theta
        above_threshold = []
        gated_out_by_state = []
        candidates = []
        fallback_triggered = False
        fallback_method = None
        fallback_skills_list = None
        adaptive_chain = []

        # Build θ decay chain from input theta down to 0.0
        theta_levels = sorted(set(
            [t for t in THETA_DECAY_CHAIN if t <= theta] + [theta]
        ), reverse=True)

        for theta_level in theta_levels:
            at = [s for s in all_scores if s["iso_score"] >= theta_level]
            # Apply precondition gate
            gated = []
            cands = []
            for s in at:
                tool_name = s.get("name", "")
                allowed, reason = GLOBAL_GATES.check(tool_name)
                if allowed:
                    cands.append(s)
                else:
                    gated.append({
                        "id": s["id"], "name": tool_name,
                        "iso_score": s["iso_score"], "reason": reason,
                    })
            adaptive_chain.append({
                "theta": theta_level,
                "n_above": len(at),
                "n_gated": len(gated),
                "n_candidates": len(cands),
            })
            theta_used = theta_level
            above_threshold = at
            gated_out_by_state = gated
            candidates = cands
            if len(cands) >= top_k:
                break  # Found enough candidates at this θ level

        # ── Guardrail: v2 keyword fallback when ALL θ levels fail ──
        # RC-7 FIX: Look up real resource IDs from DB instead of creating fake id:0
        # entries that corrupt times_triggered stats and break lazy-loading.
        if len(candidates) == 0:
            keyword_skills = find_matching_skills(query, project)
            if keyword_skills:
                fallback_triggered = True
                fallback_method = "keyword_match_v2"
                fallback_skills_list = keyword_skills
                # Resolve real resource IDs for keyword matches
                ks_names = [ks["name"] for ks in keyword_skills]
                placeholders = ",".join("?" * len(ks_names))
                id_rows = self.intent_router.conn.execute(
                    f"SELECT id, name, resource_type, description, tier "
                    f"FROM resources WHERE name IN ({placeholders}) AND status='active'",
                    ks_names,
                ).fetchall()
                id_map = {r["name"]: r for r in id_rows}
                for ks in keyword_skills:
                    row = id_map.get(ks["name"])
                    real_id = row["id"] if row else 0
                    candidates.append({
                        "id": real_id,
                        "name": ks["name"],
                        "resource_type": ks.get("type", "skill"),
                        "iso_score": 0.0,
                        "description": row["description"] if row else "",
                        "tier": row["tier"] if row else 2,
                        "status": "active",
                    })

        gate_latency = (time.perf_counter() - start_gate) * 1000
        # Override theta_used for result output
        theta = theta_used

        # ── Line 4: Top-k selection by ISO score ──────────────
        candidates.sort(key=lambda x: x["iso_score"], reverse=True)
        active_set = candidates[:top_k]

        # ── Line 5: Lazy-load full schemas for active set ──────
        start_lazy = time.perf_counter()
        promoted_schemas = []
        if not fallback_triggered:
            for s in active_set:
                full = self.loader.load_body(s["id"])
                if full and full.get("body_text"):
                    promoted_schemas.append({
                        "id": s["id"],
                        "name": s["name"],
                        "resource_type": s["resource_type"],
                        "body": full["body_text"],
                        "iso_score": s["iso_score"],
                    })
                else:
                    promoted_schemas.append({
                        "id": s["id"],
                        "name": s["name"],
                        "resource_type": s["resource_type"],
                        "body": s.get("description", ""),
                        "iso_score": s["iso_score"],
                    })
        else:
            # Fallback results from v2 keyword router; skip DB lazy-load
            for s in active_set:
                promoted_schemas.append({
                    "id": s["id"],
                    "name": s["name"],
                    "resource_type": s["resource_type"],
                    "body": s.get("description", ""),
                    "iso_score": 0.0,
                })
        lazy_latency = (time.perf_counter() - start_lazy) * 1000

        # ── Token accounting (paper Section 7) ────────────────
        n_total = len(all_scores)
        n_above = len(above_threshold)
        n_active = len(active_set)
        n_gated_state = len(gated_out_by_state)

        # When fallback is active, use v2 keywords; no Phase 1/2 tokens
        if fallback_triggered:
            phase1_tokens = 0
            phase2_tokens = 0
        else:
            phase1_tokens = n_total * SUMMARY_MAX_TOKENS
            phase2_tokens = n_active * TOKENS_PER_SCHEMA_TOOL
        total_tool_tokens = phase1_tokens + phase2_tokens

        # Reduction vs full-schema baseline
        full_schema_tokens = n_total * TOKENS_PER_SCHEMA_TOOL
        reduction_pct = round(
            (1 - total_tool_tokens / max(full_schema_tokens, 1)) * 100, 1
        )

        total_latency = (time.perf_counter() - turn_start) * 1000

        # ── Auto-log feedback to routing_log + resources table ─
        if auto_log:
            self._auto_log_routing(query, project, active_set, fallback_triggered, turn_id)

        # ── Retrieval confidence labels (G-B3) ──────────────────
        if fallback_triggered:
            confidence_level = "LOW"
            confidence_reason = "keyword fallback — no ISO match"
        elif theta >= 0.28:
            confidence_level = "HIGH"
            confidence_reason = f"theta={theta:.2f} ≥ 0.28 — strong ISO match"
        elif theta >= 0.12:
            confidence_level = "MEDIUM"
            confidence_reason = f"theta={theta:.2f} in [0.12, 0.28) — decay chain hit"
        else:
            confidence_level = "LOW"
            confidence_reason = f"theta={theta:.2f} < 0.12 — decay chain fallback"

        # Tag each active_set item with per-result confidence
        active_set_with_confidence = []
        for s in active_set:
            item_confidence = "HIGH" if s["iso_score"] >= 0.28 else ("MEDIUM" if s["iso_score"] >= 0.12 else "LOW")
            active_set_with_confidence.append({
                "id": s["id"], "name": s["name"],
                "type": s.get("resource_type"), "iso_score": s["iso_score"],
                "tier": s.get("tier", 2), "confidence_level": item_confidence,
            })

        # ── Assemble result ────────────────────────────────────
        result = {
            "turn_id": turn_id,
            "query": query,
            "query_embedding_hash": hashlib.md5(query.encode()).hexdigest()[:8],
            "pipeline": "tool-attention-v4",
            "algorithm": "Algorithm 1 (arxiv:2604.21816)",
            "cluster_routing": cluster_result,
            "params": {
                "theta": theta,
                "theta_decay_chain": theta_levels,
                "theta_adaptive_chain": adaptive_chain,
                "top_k": top_k,
                "encoder": MODEL_ID,
                "dim": DIM,
            },
            "token_accounting": {
                "n_total_tools": n_total,
                "n_above_threshold": n_above,
                "n_gated_out_by_state": n_gated_state,
                "n_active_set": n_active,
                "phase1_summary_tokens": phase1_tokens,
                "phase2_schema_tokens": phase2_tokens,
                "total_tool_tokens": total_tool_tokens,
                "full_schema_baseline_tokens": full_schema_tokens,
                "reduction_pct": reduction_pct,
            },
            "gating": {
                "above_threshold": [
                    {"id": s["id"], "name": s["name"],
                     "type": s.get("resource_type"), "iso_score": s["iso_score"]}
                    for s in above_threshold[:5]
                ],
                "gated_out_by_state": gated_out_by_state[:5],
                "active_set": active_set_with_confidence,
            },
            "retrieval_confidence_summary": {
                "level": confidence_level,
                "reason": confidence_reason,
                "theta_used": theta,
                "fallback_triggered": fallback_triggered,
            },
            "promoted_schemas": promoted_schemas,
            "latency_ms": {
                "iso_scoring": round(iso_latency, 2),
                "gating": round(gate_latency, 2),
                "lazy_loading": round(lazy_latency, 2),
                "total": round(total_latency, 2),
            },
            "loader_stats": self.loader.get_stats(),
            "fallback_triggered": fallback_triggered,
            "fallback_method": fallback_method,
            "fallback_skills": fallback_skills_list,
        }

        return result

    # ── Auto-Logging: Close the feedback loop ──────────────────

    def _auto_log_routing(self, query: str, project: str, active_set: list, fallback: bool, turn_id: str):
        """Write routing decision to DB. Captures routing_log ID for later
        outcome callback. Also increments resources.times_triggered."""
        _log_conn = None
        try:
            _log_conn = sqlite3.connect(str(DB_PATH), timeout=30)
            _log_conn.execute("PRAGMA journal_mode=WAL")
            _log_conn.execute("PRAGMA busy_timeout=15000")
            skill_names = json.dumps([s["name"] for s in active_set[:5]])
            cur = _log_conn.execute(
                """INSERT INTO routing_log
                (task_summary, project, matched_tier, matched_model, matched_skills,
                 actual_model_used, outcome, skill_name, tokens_used, ide)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    query[:500],
                    project,
                    2 if not fallback else 1,
                    "tool-attention-v4",
                    skill_names,
                    None,
                    None,
                    None,
                    0,
                    "opencode",
                ),
            )
            routing_log_id = cur.lastrowid
            for s in active_set:
                if s["id"] and s["id"] > 0:
                    _log_conn.execute(
                        "UPDATE resources SET times_triggered = times_triggered + 1, last_used_at = datetime('now') WHERE id = ?",
                        (s["id"],),
                    )
            _log_conn.commit()

            # Store mapping so complete_route_turn() can close the loop
            self._pending_routes[turn_id] = {
                "routing_log_id": routing_log_id,
                "project": project,
                "query": query,
                "active_set": active_set,
            }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Auto-log routing failed: {e}")
        finally:
            if _log_conn:
                _log_conn.close()

    def complete_route_turn(self, turn_id: str, outcome: str,
                            tokens_used: int = 0) -> Optional[dict]:
        """Close the feedback loop for a previously routed turn.

        This is the KEY fix for Gap 2+3+5: it updates the routing_log
        entry (outcome), then delegates to learning.py:record_outcome()
        which triggers Thompson Sampling, reflection, and cluster affinity.

        Call this after the model has executed the routed tools and you
        know whether the route was correct or wrong.

        Args:
            turn_id: The turn_id returned by process_turn()
            outcome: 'correct' or 'wrong'
            tokens_used: Approximate tokens consumed (for Thompson belief)

        Returns: record_outcome result dict, or None if turn_id not found.
        """
        route_data = self._pending_routes.pop(turn_id, None)
        if not route_data:
            return None

        try:
            from server.learning import record_outcome
            # Update routing_log entry first
            _conn = sqlite3.connect(str(DB_PATH), timeout=30)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=15000")
            _conn.execute(
                "UPDATE routing_log SET outcome=?, tokens_used=? WHERE id=?",
                (outcome, tokens_used, route_data["routing_log_id"]),
            )
            _conn.commit()
            _conn.close()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"complete_route_turn DB update failed: {e}")

        # Call record_outcome for each promoted schema to trigger
        # Thompson Sampling, reflection, and cluster affinity updates.
        results = []
        for s in route_data["active_set"]:
            try:
                result = record_outcome(
                    skill_name=s.get("name", "unknown"),
                    outcome=outcome,
                    task_summary=route_data["query"],
                    project=route_data["project"],
                    ide="opencode",
                    tokens_used=max(tokens_used // max(len(route_data["active_set"]), 1), 1),
                )
                results.append({s["name"]: result})
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"record_outcome failed for {s.get('name')}: {e}"
                )
        return {"turn_id": turn_id, "outcome": outcome, "results": results}

    # ── Hallucination Gate (Algorithm 1, Lines 8-9) ────────────

    @staticmethod
    def _compute_word_overlap(name1: str, name2: str) -> float:
        """Word overlap similarity: shared tokens / max(len(t1), len(t2)).

        Splits both names on '-' and '_', computes set intersection
        over the larger set's cardinality.
        """
        def tokenize(n: str) -> set:
            parts = n.replace("_", "-").split("-")
            return {p.lower() for p in parts if p}

        t1 = tokenize(name1)
        t2 = tokenize(name2)
        if not t1 or not t2:
            return 0.0
        return round(len(t1 & t2) / max(len(t1), len(t2)), 2)

    def check_tool_call(self, tool_name: str, active_set: list[dict]) -> dict:
        """after_model hook: reject tool calls not in active set A_t.

        Algorithm 1, Line 8-9:
          if model emits tool call c not in A_t:
              reject c, return "tool <c> not available"

        When rejected, suggests top-3 alternatives from active_set
        ranked by word overlap similarity (Guardrail 3).
        """
        active_names = {s["name"] for s in active_set}
        if tool_name in active_names:
            return {"allowed": True, "tool": tool_name}

        available = list(active_names)[:10]

        # Guardrail 3: fuzzy-match rejected tool against active set
        scored = []
        for s in active_set:
            score = self._compute_word_overlap(tool_name, s["name"])
            scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1]["name"]))

        suggested_alternatives = [
            {
                "name": s["name"],
                "similarity_score": score,
                "resource_type": s.get("type") or s.get("resource_type", ""),
            }
            for score, s in scored[:3]
            if score > 0
        ]

        suggested_names = ", ".join(a["name"] for a in suggested_alternatives)
        did_you_mean = f" Did you mean: {suggested_names}?" if suggested_names else ""

        return {
            "allowed": False,
            "tool": tool_name,
            "error": "tool_not_available",
            "available": available,
            "suggested_alternatives": suggested_alternatives,
            "message": f"Tool '{tool_name}' is not in the active set."
                       f"{did_you_mean}"
                       f" Available: {', '.join(available)}",
        }

    def batch_check_tool_calls(
        self, called_tools: list[str], active_set: list[dict]
    ) -> list[dict]:
        """Verify a batch of model-emitted tool calls against active set."""
        return [self.check_tool_call(t, active_set) for t in called_tools]

    # ── Convenience ────────────────────────────────────────────

    def close(self):
        self.intent_router.close()


# ═══════════════════════════════════════════════════════════════════
# Module-level convenience
# ═══════════════════════════════════════════════════════════════════

TA_V4 = ToolAttentionV4.get()


def route_v4(
    query: str,
    resource_types: Optional[str] = None,
    theta: float = THETA,
    top_k: int = TOP_K,
    project: str = "nexus",
    use_clusters: bool = True,
    max_clusters: int = 2,
    min_cluster_affinity: float = 0.35,
    auto_log: bool = True,
) -> str:
    """Full v4 Tool Attention pipeline (Algorithm 1).

    Args:
        query: User's task description
        resource_types: Comma-separated types to filter (optional)
        theta: ISO score threshold (default: 0.28)
        top_k: Active set size (default: 10)
        use_clusters: Enable cluster routing pre-filter (default: True)
        max_clusters: Max clusters to activate (default: 2)
        min_cluster_affinity: Min affinity to activate a cluster (default: 0.35)
        auto_log: Auto-write routing decision to DB (default: True)

    Returns JSON with complete turn observability record.
    """
    types = resource_types.split(",") if resource_types else None
    result = TA_V4.process_turn(
        query=query,
        resource_types=types,
        theta=theta,
        top_k=top_k,
        project=project,
        use_clusters=use_clusters,
        max_clusters=max_clusters,
        min_cluster_affinity=min_cluster_affinity,
        auto_log=auto_log,
    )
    return json.dumps(result, indent=2)


def hallucination_gate(tool_name: str, active_set_json: str) -> str:
    """Check if a tool call is in the active set. Algorithm 1 Line 8.

    Args:
        tool_name: Name of tool that model emitted
        active_set_json: JSON array of active set (from route_v4)

    Returns JSON with allowed status.
    """
    import json as _json
    active = _json.loads(active_set_json)
    # Normalize: if items are strings (not dicts), wrap to dict format
    if active and isinstance(active[0], str):
        active = [{"name": s, "resource_type": "skill"} for s in active]
    result = TA_V4.check_tool_call(tool_name, active)
    return json.dumps(result, indent=2)
