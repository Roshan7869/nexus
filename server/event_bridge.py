#!/usr/bin/env python3
"""
NEXUS Event Bridge — connects MCP server outcomes to Thompson Sampling beliefs

Based on:
- Continue Dev DataLogger: every interaction logged with preference signal
- SWE-agent accept signal: binary success from test harness
- Reflexion (2303.11366): outcome stored in memory for future routing

This bridge is called from nexus-mcp-enhanced.py after every tool outcome.
It converts routing outcomes into IDE events that update model beliefs.
"""

import json, os, logging
from pathlib import Path
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

LEARNING_ENABLED = os.environ.get("NEXUS_THOMPSON_ROUTING", "false").lower() == "true"


class EventBridge:
    """Bridges MCP server outcomes to NexusLearner events."""

    def __init__(self):
        self._learner = None
        self._collector = None

    @property
    def learner(self):
        if self._learner is None:
            import sys
            sys.path.insert(0, str(Path.home() / 'nexus'))
            from core.learner import NexusLearner, IDEEventCollector
            self._learner = NexusLearner()
            self._collector = IDEEventCollector(self._learner)
        return self._learner

    @property
    def collector(self):
        if self._collector is None:
            _ = self.learner  # triggers init
        return self._collector

    def close(self):
        if self._learner:
            self._learner.close()
            self._learner = None
            self._collector = None

    def outcome_to_event(self, outcome: str, route_result: Dict, ide: str = "unknown"):
        """Map routing outcome to IDE event and update beliefs.

        Outcome mapping (SWE-agent accept signal pattern):
          correct  → task_completed (+1.0)
          partial  → chat_accepted (+0.4)
          wrong    → test_failed (-0.5)
          error    → error (-0.8)
          timeout  → timeout (-0.4)

        Non-terminal outcomes (routed, pending, empty) are logged to routing_log
        but never update Thompson beliefs (arXiv:2604.02869 / P0 design plan).
        """
        if not LEARNING_ENABLED:
            return None

        TERMINAL_OUTCOMES = {"correct", "partial", "wrong", "error", "timeout"}
        if outcome not in TERMINAL_OUTCOMES:
            logger.debug("Event bridge: ignoring non-terminal outcome %r", outcome)
            return None

        try:
            from core.ide_events import create_event
            from core.learner import EVENT_REWARDS

            # Map outcome to event_type
            outcome_map = {
                "correct": "task_completed",
                "partial": "chat_accepted",
                "wrong": "test_failed",
                "error": "error",
                "timeout": "timeout",
            }
            event_type = outcome_map[outcome]
            reward = EVENT_REWARDS.get(event_type, 0.0)

            provider = route_result.get("provider", "unknown") if route_result else "unknown"
            model = route_result.get("model", "unknown") if route_result else "unknown"
            task_type = route_result.get("task_type", "general") if route_result else "general"

            event = create_event(
                ide=ide,
                model=model,
                provider=provider,
                task_type=task_type,
                event_type=event_type,
                reward=reward,
                metadata=route_result,
            )
            self.learner.record_ide_event(event)
            logger.info(f"Event bridge: {outcome} → {provider}/{model} reward={event.reward:.2f}")
            return event
        except Exception as e:
            logger.warning(f"Event bridge failed: {e}")
            return None

    def record_metrics_signal(self, provider: str, model: str, task_type: str,
                                latency_ms: float, tokens: int, success: bool):
        """Record implicit signal from request metrics."""
        if not LEARNING_ENABLED:
            return
        try:
            self.learner.record_implicit_signal(provider, model, task_type,
                                                  latency_ms, tokens, success)
        except Exception as e:
            logger.warning(f"Implicit signal failed: {e}")

    def record_benchmark(self, provider: str, model: str, benchmark: str,
                          score: float, samples: int = 0):
        """Record benchmark result as prior."""
        try:
            self.learner.record_benchmark_result(provider, model, benchmark,
                                                   score, samples)
            logger.info(f"Benchmark recorded: {provider}/{model} {benchmark}={score:.3f}")
        except Exception as e:
            logger.warning(f"Benchmark recording failed: {e}")

    def get_learning_stats(self) -> Dict:
        """Get current learning statistics."""
        if not LEARNING_ENABLED:
            return {"enabled": False}
        try:
            return self.learner.get_stats()
        except Exception as e:
            return {"enabled": True, "error": str(e)}


# ── Module-level singleton ────────────────────────────────────────────────

_bridge: Optional[EventBridge] = None

def get_bridge() -> EventBridge:
    global _bridge
    if _bridge is None:
        _bridge = EventBridge()
    return _bridge

_OUTCOME_TO_LEARNING = {"error": "wrong", "timeout": "skip"}


def record_outcome(outcome: str, route_result: Dict, ide: str = "unknown"):
    """Delegate to learning.record_outcome: one writer for outcome counters.

    Only the learning module updates resource counters and beliefs
    (FIX-DESIGN S1-e). Returns None when learning is disabled.
    """
    if not LEARNING_ENABLED:
        return None
    from server.learning import record_outcome as _learning_record
    route_result = route_result or {}
    label = _OUTCOME_TO_LEARNING.get(outcome, outcome)
    return _learning_record(
        skill_name=route_result.get("skill_name") or route_result.get("task_type") or "unknown",
        outcome=label,
        task_summary=str(route_result.get("task_summary", ""))[:200],
        project=route_result.get("project", "default"),
        ide=ide,
    )

def shutdown_bridge():
    global _bridge
    if _bridge:
        _bridge.close()
        _bridge = None


def get_learning_stats() -> dict:
    """Return Thompson Sampling belief stats for all models."""
    if not LEARNING_ENABLED:
        return {"enabled": False, "total_observations": 0}
    bridge = get_bridge()
    try:
        return bridge.get_learning_stats()
    except Exception as e:
        return {"status": "error", "error": str(e)}