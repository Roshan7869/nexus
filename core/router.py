#!/usr/bin/env python3
"""
Nexus Routing Engine - Slow-Learning LLM Router
OFF by default. Activates only when explicitly enabled.
Learns from request metrics and benchmark results before routing.
"""
import os, json, time, logging, statistics
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("nexus.router")

METRICS_DIR = Path.home() / ".nexus" / "metrics"
BENCHMARK_DIR = Path.home() / ".nexus" / "benchmarks"

# Weighting for routing score
WEIGHTS = {
    "accuracy": 0.50,
    "speed": 0.30,
    "cost_efficiency": 0.20,
}

# Task type to role mapping
TASK_ROLE_MAP = {
    "coding": "coding",
    "reasoning": "reasoning",
    "agentic": "agentic",
    "general": "general",
}


class BenchmarkStore:
    """Persistent benchmark results for all model/task pairs"""

    def __init__(self):
        BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
        self.results_file = BENCHMARK_DIR / "results.json"
        self.results = self._load()

    def _load(self) -> Dict:
        if self.results_file.exists():
            with open(self.results_file) as f:
                return json.load(f)
        return {}

    def save(self):
        with open(self.results_file, "w") as f:
            json.dump(self.results, f, indent=2)

    def record(self, provider: str, model: str, benchmark: str, score: float,
               num_samples: int, latency_ms: float):
        key = f"{provider}:{model}:{benchmark}"
        self.results[key] = {
            "score": score,
            "num_samples": num_samples,
            "latency_ms": latency_ms,
            "timestamp": time.time()
        }
        self.save()

    def get(self, provider: str, model: str, benchmark: str) -> Optional[Dict]:
        key = f"{provider}:{model}:{benchmark}"
        return self.results.get(key)

    def get_all_for_model(self, provider: str, model: str) -> Dict[str, Dict]:
        return {
            b: v for k, v in self.results.items()
            if k.startswith(f"{provider}:{model}:") and (b := k.split(":")[-1])
        }


class SlowLearningRouter:
    """
    Routing engine that learns from metrics and benchmarks.
    OFF by default — only activates when config.routing.enabled = true.
    """

    def __init__(self, config, metrics_store, benchmark_store: BenchmarkStore):
        self.config = config
        self.metrics = metrics_store
        self.benchmarks = benchmark_store
        self.min_samples = config.routing.min_samples_before_route
        self.route_cache: Dict[str, Dict] = {}
        self.last_update = 0

    def is_enabled(self) -> bool:
        return self.config.routing.enabled

    def get_route(self, task_type: str, preferred_model: Optional[str] = None) -> Optional[Dict]:
        """
        Get the best route for a task type.
        Returns None if not enough data or routing disabled.
        """
        if not self.is_enabled():
            return None

        # Check cache (5 min TTL)
        cache_key = f"{task_type}:{preferred_model or 'any'}"
        if cache_key in self.route_cache:
            cached = self.route_cache[cache_key]
            if time.time() - cached.get("cached_at", 0) < 300:
                return cached["route"]

        # Build routing decision from metrics + benchmarks
        route = self._compute_route(task_type, preferred_model)
        if route:
            self.route_cache[cache_key] = {
                "route": route,
                "cached_at": time.time()
            }
        return route

    def _compute_route(self, task_type: str, preferred_model: Optional[str] = None) -> Optional[Dict]:
        """Compute best route based on accumulated data"""
        all_models = self.config.get_all_models()

        if len(all_models) < 2:
            return None  # Need at least 2 models to route between

        candidates = []
        for provider, model, role in all_models:
            score = self._score_model(provider, model, task_type)
            if score is not None:
                candidates.append({
                    "provider": provider,
                    "model": model,
                    "role": role,
                    "score": score
                })

        if not candidates:
            return None

        # Sort by score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        # If preferred model is available and close to best, use preferred
        if preferred_model:
            for c in candidates:
                if c["model"] == preferred_model:
                    if c["score"] >= best["score"] * 0.95:  # within 5% of best
                        return {
                            "provider": c["provider"],
                            "model": c["model"],
                            "score": c["score"],
                            "fallback": best["model"]
                        }

        return {
            "provider": best["provider"],
            "model": best["model"],
            "score": best["score"],
            "fallback": candidates[1]["model"] if len(candidates) > 1 else None
        }

    def _score_model(self, provider: str, model: str, task_type: str) -> Optional[float]:
        """Compute composite score for a model on a task type"""

        # 1. Benchmark score (MMLU, HumanEval, etc.)
        benchmark_score = 0.0
        benchmark_count = 0
        for bench in self.config.routing.benchmarks:
            result = self.benchmarks.get(provider, model, bench)
            if result:
                benchmark_score += result["score"]
                benchmark_count += 1

        if benchmark_count == 0:
            # Fall back to request metrics
            stats = self.metrics.get_stats(provider, model, last_n=100)
            if stats.get("total_requests", 0) < self.min_samples:
                return None  # Not enough data
            benchmark_score = stats.get("success_rate", 0.5)
            benchmark_count = 1

        avg_benchmark = benchmark_score / benchmark_count if benchmark_count > 0 else 0.5

        # 2. Speed score (normalized)
        stats = self.metrics.get_stats(provider, model, last_n=100)
        avg_latency = stats.get("avg_latency_ms", 500)
        speed_score = max(0, 1 - (avg_latency / 2000))  # 2000ms = 0 score, 0ms = 1

        # 3. Cost efficiency (placeholder — use token efficiency)
        token_efficiency = stats.get("avg_tokens_per_request", 100) / 500
        cost_score = min(1.0, token_efficiency)

        composite = (
            WEIGHTS["accuracy"] * avg_benchmark +
            WEIGHTS["speed"] * speed_score +
            WEIGHTS["cost_efficiency"] * cost_score
        )

        return round(composite, 4)

    def record_outcome(self, provider: str, model: str, task_type: str,
                       success: bool, latency_ms: float, tokens: int):
        """Record actual outcome to improve future routing"""
        # This is called after each request — just log to metrics
        # The slow-learning kicks in when min_samples is reached
        pass


class BenchmarkRunner:
    """Run official benchmarks against all models"""

    BENCHMARK_SUITES = {
        "mmlu": {
            "tool": "lm-eval-harness",
            "command": ["lm_eval", "--model", "vllm", "--tasks", "mmlu", "--batch_size", "4"]
        },
        "humaneval": {
            "tool": "lm-eval-harness",
            "command": ["lm_eval", "--model", "vllm", "--tasks", "humaneval", "--batch_size", "4"]
        },
        "math500": {
            "tool": "lm-eval-harness",
            "command": ["lm_eval", "--model", "vllm", "--tasks", "math", "--num_fewshot", "5"]
        }
    }

    def __init__(self, benchmark_store: BenchmarkStore, provider_map: Dict):
        self.store = benchmark_store
        self.providers = provider_map

    async def run_benchmark(self, benchmark_name: str, provider: str, model: str) -> Optional[Dict]:
        """Run a single benchmark against a model"""
        suite = self.BENCHMARK_SUITES.get(benchmark_name)
        if not suite:
            logger.warning(f"Unknown benchmark: {benchmark_name}")
            return None

        logger.info(f"Running {benchmark_name} on {provider}/{model}")

        # TODO: Actually run lm-eval-harness or equivalent
        # For now, placeholder — would need vllm or ollama server running
        # Actual implementation would use subprocess to run lm_eval

        return None  # Placeholder

    async def run_all_benchmarks(self):
        """Run all configured benchmarks against all models"""
        results = {}
        for provider, pconfig in self.config.providers.items():
            for model in pconfig.models:
                for bench in self.config.routing.benchmarks:
                    result = await self.run_benchmark(bench, provider, model.name)
                    if result:
                        self.store.record(provider, model.name, bench,
                                         result["score"], result["num_samples"],
                                         result["latency_ms"])
                        results[f"{provider}/{model.name}/{bench}"] = result
        return results