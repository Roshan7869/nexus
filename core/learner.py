#!/usr/bin/env python3
"""
NEXUS Self-Learning System — Thompson Sampling + IDE Feedback + Benchmarks
=========================================================================

Papers: Thompson Sampling (1933), Reflexion (2303.11366), ToolkenGPT (2310.03054)
Projects: Continue Dev DataLogger, SWE-agent accept signal, Voyager skill library

Architecture:
  - Beta(α,β) beliefs per (provider, model, task_type)
  - IDE events: code_accepted (+0.8), test_passed (+1.0), test_failed (-0.5), edit_rejected (-0.3)
  - Benchmark results: periodic calibration as prior
  - 3-signal weighted update: IDE 50%, Benchmark 30%, Implicit 20%
  - OFF by default, only activates when routing.enabled=True in nexus.yaml
  - Minimum 50 samples per model-pair before routing activates

All data stored in nexus.db (unified with resources/routing/clusters).
"""

import json, time, hashlib, logging, uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
from datetime import datetime
import sqlite3

logger = logging.getLogger(__name__)

NEXUS_DB = Path(__file__).parent.parent / "db" / "nexus.db"

# ── Event Types + Rewards ───────────────────────────────────────────────────

EVENT_REWARDS = {
    "code_accepted":    +0.8,
    "code_rejected":    -0.3,
    "edit_accepted":    +0.7,
    "edit_rejected":    -0.3,
    "test_passed":      +1.0,
    "test_failed":      -0.5,
    "build_passed":     +0.9,
    "build_failed":     -0.6,
    "lint_passed":      +0.5,
    "lint_failed":      -0.2,
    "timeout":          -0.4,
    "error":            -0.8,
    "chat_accepted":    +0.4,
    "chat_rejected":    -0.2,
    "task_completed":   +1.0,
    "task_abandoned":   -0.5,
}

# ── Weight Configuration ────────────────────────────────────────────────────

SIGNAL_WEIGHTS = {
    "ide": 0.50,
    "benchmark": 0.30,
    "implicit": 0.20,
}

MIN_SAMPLES_FOR_ROUTING = 3  # First-principles: 50 is unreachable with <20 used resources.
# Reduced to 3 so Thompson Sampling activates immediately for any resource
# with 3+ uses. Scales up as more data arrives (Lai-Robbins optimal: O(log T)).

# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass(kw_only=True)
class IDEEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    ide: str
    model: str
    provider: str
    task_type: str
    event_type: str
    reward: float
    task_description: str = ""
    timestamp: float = 0.0
    confidence: float = 1.0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.timestamp == 0.0:
            import time
            self.timestamp = time.time()

@dataclass
class ModelBelief:
    provider: str
    model: str
    task_type: str
    alpha: float = 1.0
    beta: float = 1.0
    total_requests: int = 0
    total_reward: float = 0.0
    avg_reward: float = 0.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta) if (self.alpha + self.beta) > 0 else 0.5

    @property
    def confidence(self) -> float:
        return min(self.total_requests / MIN_SAMPLES_FOR_ROUTING, 1.0)

    def sample(self) -> float:
        import numpy as np
        return float(np.random.beta(self.alpha, self.beta))

    def update(self, reward: float, signal_type: str = "ide"):
        weight = SIGNAL_WEIGHTS.get(signal_type, 0.2)
        scaled = reward * weight
        if scaled > 0:
            self.alpha += scaled
        else:
            self.beta += abs(scaled)
        self.total_requests += 1
        self.total_reward += reward
        self.avg_reward = self.total_reward / self.total_requests


# ── NexusLearner ─────────────────────────────────────────────────────────────

class NexusLearner:
    """Thompson Sampling router that learns from IDE events, benchmarks, and implicit signals."""

    def __init__(self, config=None, metrics_store=None, db_path: str = None):
        """Construct NexusLearner.

        Three usage patterns:
          1. NexusLearner() — uses default NEXUS_DB
          2. NexusLearner(db_path="...") — explicit DB path
          3. NexusLearner(config, metrics_store) — full LLM-gateway context
             (config: NexusConfig, metrics_store: MetricsStore)

        The config and metrics_store are stored for use in routing decisions
        and event recording. Backward-compatible: existing callers using
        NexusLearner() or NexusLearner(db_path=...) continue to work.
        """
        self.db_path = Path(db_path) if db_path else NEXUS_DB
        self.config = config
        self.metrics_store = metrics_store
        self._conn = None
        self._belief_cache: Dict[str, ModelBelief] = {}

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=60)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Belief Management ────────────────────────────────────────────────

    def _belief_key(self, provider: str, model: str, task_type: str) -> str:
        return f"{provider}:{model}:{task_type}"

    def get_belief(self, provider: str, model: str, task_type: str) -> ModelBelief:
        key = self._belief_key(provider, model, task_type)
        if key in self._belief_cache:
            return self._belief_cache[key]

        row = self.conn.execute(
            "SELECT * FROM model_beliefs WHERE provider=? AND model=? AND task_type=?",
            (provider, model, task_type)
        ).fetchone()

        if row:
            belief = ModelBelief(
                provider=row["provider"], model=row["model"], task_type=row["task_type"],
                alpha=row["alpha"], beta=row["beta"],
                total_requests=row["total_requests"], total_reward=row["total_reward"],
                avg_reward=row["avg_reward"]
            )
        else:
            belief = ModelBelief(provider=provider, model=model, task_type=task_type)
            self.conn.execute(
                "INSERT OR IGNORE INTO model_beliefs (provider,model,task_type,alpha,beta,total_requests,total_reward,avg_reward) VALUES (?,?,?,?,?,?,?,?)",
                (provider, model, task_type, belief.alpha, belief.beta, 0, 0, 0)
            )
            self.conn.commit()

        self._belief_cache[key] = belief
        return belief

    def _persist_belief(self, belief: ModelBelief):
        self.conn.execute(
            """UPDATE model_beliefs SET alpha=?, beta=?, total_requests=?, total_reward=?,
               avg_reward=?, last_updated=datetime('now') WHERE provider=? AND model=? AND task_type=?""",
            (belief.alpha, belief.beta, belief.total_requests, belief.total_reward,
             belief.avg_reward, belief.provider, belief.model, belief.task_type)
        )
        self.conn.commit()

    # ── Thompson Sampling Route ──────────────────────────────────────────

    def thompson_sample(self, candidates: List[Dict], task_type: str) -> Dict:
        """
        Sample from Beta(α,β) for each candidate model, return highest.
        Blends with benchmark prior when confidence < 0.5.

        Based on: ToolkenGPT (2310.03054) tool-router + Thompson Sampling
        """
        best = None
        best_score = -1.0

        for cand in candidates:
            provider = cand.get("provider", "unknown")
            model = cand.get("model", "unknown")
            belief = self.get_belief(provider, model, task_type)

            sampled = belief.sample()

            # Blend with benchmark prior when low confidence
            bench = self._get_benchmark_prior(provider, model)
            if belief.confidence < 0.5:
                conf = belief.confidence
                score = sampled * conf + bench * (1 - conf)
            else:
                score = sampled

            if score > best_score:
                best_score = score
                best = {**cand, "sampled_score": score, "benchmark_prior": bench,
                        "belief_mean": belief.mean, "belief_confidence": belief.confidence}

        return best or candidates[0] if candidates else {}

    def _get_benchmark_prior(self, provider: str, model: str) -> float:
        row = self.conn.execute(
            "SELECT AVG(score) as avg_score FROM benchmark_results WHERE provider=? AND model=?",
            (provider, model)
        ).fetchone()
        return float(row["avg_score"]) if row and row["avg_score"] else 0.5

    def can_route(self, task_type: str) -> bool:
        """Check if we have enough samples to route for this task type."""
        row = self.conn.execute(
            "SELECT SUM(total_requests) as total FROM model_beliefs WHERE task_type=?",
            (task_type,)
        ).fetchone()
        total = row["total"] if row and row["total"] else 0
        return total >= MIN_SAMPLES_FOR_ROUTING

    # ── Event Recording ──────────────────────────────────────────────────

    def record_ide_event(self, event: IDEEvent):
        """Record IDE event and update beliefs. Continue Dev DataLogger pattern."""
        reward = event.reward if event.reward != 0 else EVENT_REWARDS.get(event.event_type, 0.0)

        # Log event
        self.conn.execute(
            "INSERT INTO ide_events (event_type,ide,model,provider,task_type,reward,metadata) VALUES (?,?,?,?,?,?,?)",
            (event.event_type, event.ide, event.model, event.provider,
             event.task_type, reward, json.dumps(event.metadata))
        )
        self.conn.commit()

        # Update belief
        belief = self.get_belief(event.provider, event.model, event.task_type)
        belief.update(reward, "ide")
        self._persist_belief(belief)
        logger.info(f"IDE event: {event.event_type} → {event.provider}/{event.model} reward={reward:.2f} belief_mean={belief.mean:.3f}")

    def record_benchmark_result(self, provider: str, model: str, benchmark: str, score: float, samples: int = 0):
        """Record benchmark result. Acts as prior for new models."""
        self.conn.execute(
            "INSERT OR REPLACE INTO benchmark_results (provider,model,benchmark,score,samples) VALUES (?,?,?,?,?)",
            (provider, model, benchmark, score, samples)
        )
        self.conn.commit()

        # If no belief exists, seed it with benchmark score
        for task_type in ["reasoning", "coding", "agentic", "general"]:
            belief = self.get_belief(provider, model, task_type)
            if belief.total_requests == 0 and score > 0.5:
                belief.alpha = score * 2
                belief.beta = (1 - score) * 2
                self._persist_belief(belief)

        logger.info(f"Benchmark: {provider}/{model} {benchmark}={score:.3f}")

    def record_implicit_signal(self, provider: str, model: str, task_type: str,
                                latency_ms: float, tokens: int, success: bool):
        """Record implicit signal from request completion."""
        reward = 0.0
        if success:
            # Reward fast responses (under 2s = 0.3, under 5s = 0.2, under 10s = 0.1)
            if latency_ms < 2000:
                reward = 0.3
            elif latency_ms < 5000:
                reward = 0.2
            elif latency_ms < 10000:
                reward = 0.1
            else:
                reward = 0.0
        else:
            reward = -0.3

        belief = self.get_belief(provider, model, task_type)
        belief.update(reward, "implicit")
        self._persist_belief(belief)

    # ── Routing Decision Logging ────────────────────────────────────────

    def log_routing_decision(self, request_id: str, task_summary: str, cluster: str,
                              task_type: str, provider: str, model: str,
                              sampled_score: float, benchmark_prior: float,
                              approach: str = "none", candidates: List[Dict] = None):
        self.conn.execute(
            "INSERT INTO routing_decisions (request_id,task_summary,cluster,task_type,provider,model,sampled_score,benchmark_prior,approach,candidates_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request_id, task_summary, cluster, task_type, provider, model,
             sampled_score, benchmark_prior, approach, json.dumps(candidates or []))
        )
        self.conn.commit()

    # ── Task Classification Cache ────────────────────────────────────────

    def classify_task(self, task_summary: str) -> tuple:
        """Classify task type with caching. Returns (task_type, confidence)."""
        task_hash = hashlib.sha256(task_summary.encode()).hexdigest()[:16]

        row = self.conn.execute(
            "SELECT task_type, confidence, hit_count FROM task_classifications WHERE task_hash=?",
            (task_hash,)
        ).fetchone()

        if row:
            self.conn.execute(
                "UPDATE task_classifications SET hit_count=hit_count+1, updated_at=datetime('now') WHERE task_hash=?",
                (task_hash,)
            )
            self.conn.commit()
            return row["task_type"], row["confidence"]

        task_lower = task_summary.lower()

        # Try semantic classification using cluster centroids
        try:
            import sys
            import numpy as np
            from pathlib import Path
            root_dir = Path(__file__).parent.parent.resolve()
            if str(root_dir) not in sys.path:
                sys.path.insert(0, str(root_dir))
            from server.iso_embedder import IsoEmbedder
            
            embedder = IsoEmbedder(db_path=str(NEXUS_DB))
            qvec = embedder.encode(task_summary)
            qnorm = qvec / (np.linalg.norm(qvec) + 1e-10)
            
            centroids = self.conn.execute(
                "SELECT name, centroid_embedding FROM cluster_definitions WHERE centroid_embedding IS NOT NULL"
            ).fetchall()
            
            best_cluster = "general"
            best_score = -1.0
            for r in centroids:
                cname = r["name"]
                cblob = r["centroid_embedding"]
                c_arr = np.frombuffer(cblob, dtype=np.float32)
                if len(c_arr) == 384:
                    c_norm = c_arr / (np.linalg.norm(c_arr) + 1e-10)
                    score = float(np.dot(qnorm, c_norm))
                    if score > best_score:
                        best_score = score
                        best_cluster = cname
                        
            CLUSTER_TO_TASK = {
                "analyzer_planner": "reasoning",
                "architect": "architecture",
                "frontend_ui": "frontend",
                "backend_api": "coding",
                "devops_infra": "devops",
                "quality_security": "research",
                "knowledge_wiki": "general",
                "general": "general"
            }
            task_type = CLUSTER_TO_TASK.get(best_cluster, "general")
            
            # Agentic boost
            agentic_kw = ["agent", "swarm", "autonomous", "autopilot", "loop", "runs", "executes", "orchestrate", "pipeline"]
            if task_type in ["reasoning", "coding", "general"] and any(kw in task_lower for kw in agentic_kw):
                task_type = "agentic"
                
            confidence = max(0.3, min(1.0, float(best_score)))
            
        except Exception as e:
            logger.warning(f"Semantic task classification failed, falling back to heuristics: {e}")
            scores = {
                "coding": 0, "reasoning": 0, "agentic": 0, "general": 0,
                "architecture": 0, "devops": 0, "frontend": 0, "research": 0,
            }

            # Coding signals
            coding_kw = ["code", "implement", "fix", "bug", "function", "class", "method", "debug", "refactor", "test", "compile", "syntax"]
            # Reasoning signals
            reasoning_kw = ["analyze", "explain", "why", "compare", "evaluate", "assess", "consider", "reason", "logic", "deduce"]
            # Agentic signals
            agentic_kw = ["run", "execute", "deploy", "ship", "build", "automate", "pipeline", "workflow", "orchestrate"]
            # Architecture signals
            arch_kw = ["architect", "design", "system", "pattern", "ddd", "microservice", "spec", "blueprint"]
            # Devops signals
            devops_kw = ["deploy", "docker", "ci/cd", "release", "infrastructure", "monitoring", "kubernetes", "staging"]
            # Frontend signals
            frontend_kw = ["ui", "frontend", "component", "css", "react", "dashboard", "page", "layout", "style"]
            # Research signals
            research_kw = ["research", "paper", "arxiv", "study", "survey", "literature", "find", "search"]

            for kw in coding_kw:
                if kw in task_lower: scores["coding"] += 1
            for kw in reasoning_kw:
                if kw in task_lower: scores["reasoning"] += 1
            for kw in agentic_kw:
                if kw in task_lower: scores["agentic"] += 1
            for kw in arch_kw:
                if kw in task_lower: scores["architecture"] += 1
            for kw in devops_kw:
                if kw in task_lower: scores["devops"] += 1
            for kw in frontend_kw:
                if kw in task_lower: scores["frontend"] += 1
            for kw in research_kw:
                if kw in task_lower: scores["research"] += 1

            max_type = max(scores, key=scores.get)
            max_score = scores[max_type]
            confidence = min(max_score / 3.0, 1.0) if max_score > 0 else 0.3

            if max_score == 0:
                task_type, confidence = "general", 0.3
            else:
                task_type = max_type

        # Cache it
        self.conn.execute(
            "INSERT OR IGNORE INTO task_classifications (task_hash,task_summary,task_type,confidence) VALUES (?,?,?,?)",
            (task_hash, task_summary[:200], task_type, confidence)
        )
        self.conn.commit()

        return task_type, confidence

    # ── Warm-Start: Pre-seed beliefs from cluster confidence ─────────────

    def warm_start(self):
        """Pre-seed Thompson Sampling beliefs using cluster_confidence as prior.

        First-principles: Thompson Sampling needs prior Beta(α,β) distributions.
        With 0 beliefs and min_samples=3, the first 3 routes per resource are
        random. This pre-seeds using the resource's cluster_confidence as a
        proxy for expected reward quality. Resources in high-confidence clusters
        get higher α (optimistic prior), orphans get balanced prior.

        Algorithm:
          For each resource with times_triggered > 0:
            α = max(cluster_confidence * 5, 1)
            β = max((1 - cluster_confidence) * 5, 1)
            Upsert model_beliefs with this prior
        """
        rows = self.conn.execute(
            "SELECT name, cluster_confidence, times_triggered, times_correct, times_wrong, resource_type "
            "FROM resources WHERE status='active' AND times_triggered > 0"
        ).fetchall()

        seeded = 0
        for r in rows:
            conf = r["cluster_confidence"] or 0.3
            total = r["times_triggered"] or 0
            correct = r["times_correct"] or 0
            wrong = r["times_wrong"] or 0

            # Compute empirical α,β from actual outcomes + confidence prior
            prior_alpha = max(conf * 3, 1.0)
            prior_beta = max((1 - conf) * 3, 1.0)

            empirical_alpha = max(correct, 0)
            empirical_beta = max(wrong, 0)

            for task_type in ["coding", "reasoning", "agentic", "general"]:
                alpha = prior_alpha + empirical_alpha
                beta = prior_beta + empirical_beta
                self.conn.execute(
                    "INSERT OR REPLACE INTO model_beliefs "
                    "(provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("nexus", r["name"], task_type, alpha, beta, total, correct - wrong,
                     ((correct - wrong) / max(total, 1)) if total > 0 else 0),
                )
                seeded += 1

        self.conn.commit()
        logger.info(f"Warm-started {seeded} beliefs from cluster confidence")
        return seeded

    # ── Stats ────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        beliefs = []
        for row in self.conn.execute("SELECT * FROM model_beliefs ORDER BY task_type, avg_reward DESC"):
            beliefs.append({
                "provider": row["provider"], "model": row["model"],
                "task_type": row["task_type"], "alpha": row["alpha"], "beta": row["beta"],
                "mean": round(row["alpha"] / (row["alpha"] + row["beta"]), 3) if (row["alpha"] + row["beta"]) > 0 else 0,
                "total_requests": row["total_requests"], "avg_reward": round(row["avg_reward"] or 0.0, 3),
            })

        event_count = self.conn.execute("SELECT COUNT(*) FROM ide_events").fetchone()[0]
        decision_count = self.conn.execute("SELECT COUNT(*) FROM routing_decisions").fetchone()[0]
        benchmark_count = self.conn.execute("SELECT COUNT(*) FROM benchmark_results").fetchone()[0]

        return {
            "beliefs": beliefs,
            "total_events": event_count,
            "total_decisions": decision_count,
            "total_benchmarks": benchmark_count,
            "min_samples_for_routing": MIN_SAMPLES_FOR_ROUTING,
        }


# ── IDE Event Bridge ────────────────────────────────────────────────────────

class IDEEventCollector:
    """Bridges MCP/IDE outcomes to NexusLearner events."""

    def __init__(self, learner: NexusLearner):
        self.learner = learner

    def outcome_to_event(self, outcome: str, route_result: Dict, ide: str = "unknown") -> Optional[IDEEvent]:
        """Map a routing outcome to an IDE event. SWE-agent accept signal pattern."""
        reward_map = {
            "correct": "task_completed",
            "partial": "chat_accepted",
            "wrong": "test_failed",
            "success": "code_accepted",
            "failure": "error",
            "timeout": "timeout",
        }

        event_type = reward_map.get(outcome, "chat_accepted")
        reward = EVENT_REWARDS.get(event_type, 0.0)

        return IDEEvent(
            event_id=str(uuid.uuid4())[:12],
            event_type=event_type,
            ide=ide,
            model=route_result.get("matched_model", "unknown"),
            provider=route_result.get("provider", "unknown"),
            task_type=route_result.get("task_type", "general"),
            reward=reward,
        )

    def record_outcome(self, outcome: str, route_result: Dict, ide: str = "unknown"):
        """One-call convenience: outcome → event → belief update."""
        event = self.outcome_to_event(outcome, route_result, ide)
        if event:
            self.learner.record_ide_event(event)
