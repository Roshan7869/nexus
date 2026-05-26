#!/usr/bin/env python3
"""Nexus Metrics Store - Request tracking, latency, token counts"""
import json, time, os, logging
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
import statistics

logger = logging.getLogger(__name__)

METRICS_DIR = Path.home() / ".nexus" / "metrics"
METRICS_DIR.mkdir(parents=True, exist_ok=True)

@dataclass
class RequestMetrics:
    request_id: str
    model: str
    provider: str
    task_type: str  # reasoning, coding, agentic, general
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: float
    success: bool
    error: Optional[str] = None
    approach: Optional[str] = None

class MetricsStore:
    def __init__(self):
        self.requests: Dict[str, list] = defaultdict(list)
        self._load_existing()

    def _load_existing(self):
        """Load recent metrics from today's file"""
        today = METRICS_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"
        if today.exists():
            with open(today) as f:
                for line in f:
                    if line.strip():
                        try:
                            m = json.loads(line)
                            key = f"{m['provider']}:{m['model']}"
                            self.requests[key].append(m)
                        except Exception:
                            pass

    def log(self, metrics: RequestMetrics):
        key = f"{metrics.provider}:{metrics.model}"
        self.requests[key].append(asdict(metrics))
        day = time.strftime("%Y-%m-%d")
        fpath = METRICS_DIR / f"{day}.jsonl"
        with open(fpath, "a") as fp:
            fp.write(json.dumps(asdict(metrics)) + "\n")

    def get_stats(self, provider: str, model: str, last_n: int = 100) -> Dict[str, Any]:
        key = f"{provider}:{model}"
        recent = self.requests[key][-last_n:]
        if not recent:
            return {"total_requests": 0}

        latencies = [r["latency_ms"] for r in recent]
        successes = [r for r in recent if r["success"]]
        task_counts = defaultdict(int)
        for r in recent:
            task_counts[r.get("task_type", "unknown")] += 1

        sorted_lat = sorted(latencies)
        return {
            "total_requests": len(recent),
            "success_rate": len(successes) / len(recent),
            "avg_latency_ms": round(statistics.mean(latencies), 2),
            "p50_latency_ms": round(statistics.median(latencies), 2),
            "p95_latency_ms": round(sorted_lat[int(len(sorted_lat) * 0.95)], 2) if len(sorted_lat) > 20 else None,
            "p99_latency_ms": round(sorted_lat[int(len(sorted_lat) * 0.99)], 2) if len(sorted_lat) > 100 else None,
            "avg_tokens_per_request": round(statistics.mean([r.get("total_tokens", 0) for r in recent]), 2),
            "task_breakdown": dict(task_counts),
        }

    def get_routing_decision_data(self, min_samples: int = 50) -> Dict[str, Any]:
        """Collect data needed for routing decisions across all model pairs"""
        data = {}
        for key, reqs in self.requests.items():
            if len(reqs) < min_samples:
                continue
            provider, model = key.split(":", 1)
            task_breakdown = defaultdict(list)
            for r in reqs:
                task_breakdown[r.get("task_type", "general")].append(r["latency_ms"])

            data[key] = {
                "provider": provider,
                "model": model,
                "sample_count": len(reqs),
                "success_rate": len([r for r in reqs if r["success"]]) / len(reqs),
                "avg_latency": round(statistics.mean([r["latency_ms"] for r in reqs]), 2),
                "task_avg_latency": {
                    k: round(statistics.mean(v), 2) for k, v in task_breakdown.items()
                }
            }
        return data