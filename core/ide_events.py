#!/usr/bin/env python3
"""
NEXUS IDE Event Collector
=========================
Collects feedback events from all connected IDEs:
- Claude Code (via MCP tool calls)
- Aider (via --no-git flag + edit confirmation)
- OpenCode (via API response events)
- HERMES (via internal events)

Events are buffered locally and flushed to NexusLearner.

Event Schema:
  - code_accepted: User accepted AI's code suggestion
  - code_rejected: User rejected AI's code suggestion
  - test_passed: Tests pass after applying AI's code
  - test_failed: Tests fail after applying AI's code
  - build_failed: Build/compilation failed
  - task_completed: User confirmed task is done
  - task_abandoned: User gave up on the task
  - edit_applied: AI's edit was applied to file
  - edit_undone: AI's edit was reverted

Usage:
  POST /nexus/event  with IDEEvent payload
  GET  /nexus/events  to list recent events
"""

import json, uuid, logging, time
from typing import Dict, Any, Optional, List
from dataclasses import asdict
from datetime import datetime

from .learner import IDEEvent, NexusLearner

logger = logging.getLogger("nexus.ide_events")

# In-memory buffer before flush to DB
_event_buffer: List[IDEEvent] = []
_BUFFER_SIZE = 10
_BUFFER_FLUSH_INTERVAL = 5.0  # seconds


def create_event(
    ide: str,
    model: str,
    provider: str,
    task_type: str,
    event_type: str,
    reward: float,
    task_description: str = "",
    confidence: float = 1.0,
    metadata: Optional[Dict[str, Any]] = None
) -> IDEEvent:
    """Factory for IDEEvent with auto-generated fields"""
    return IDEEvent(
        event_id=str(uuid.uuid4())[:12],
        ide=ide,
        model=model,
        provider=provider,
        task_type=task_type,
        event_type=event_type,
        reward=reward,
        task_description=task_description[:200] if task_description else "",
        timestamp=time.time(),
        confidence=confidence,
        metadata=metadata or {}
    )


def classify_event_reward(event_type: str, metadata: Dict = None) -> float:
    """
    Map event type to reward value in [-1, +1].

    Reward Scale:
      +1.0  = Perfect success
      +0.5  = Partial success / acceptable
       0.0  = Neutral / mixed
      -0.5  = Partial failure
      -1.0  = Complete failure
    """
    reward_map = {
        # Positive events
        "code_accepted":      0.8,
        "edit_applied":       0.6,
        "test_passed":        1.0,   # Strongest positive signal
        "task_completed":     1.0,
        "build_success":      0.7,

        # Negative events
        "code_rejected":     -0.5,
        "edit_undone":      -0.6,
        "test_failed":      -1.0,   # Strongest negative signal
        "build_failed":     -0.8,
        "task_abandoned":   -0.7,

        # Neutral
        "timeout":           -0.3,
        "error":            -0.4,
    }

    base = reward_map.get(event_type, 0.0)

    # Adjust based on metadata
    if metadata:
        # Penalize repeated failures on same model
        retry_count = metadata.get("retry_count", 0)
        if retry_count > 2:
            base *= 0.5

        # Bonus for first-time success
        if metadata.get("first_attempt") and base > 0:
            base *= 1.2

    return max(-1.0, min(1.0, base))


# ─── Event Collection API ─────────────────────────────────────────────────────

class IDEEventCollector:
    """
    Collects events from IDEs and feeds them to NexusLearner.
    Can be used via HTTP endpoint or called directly from IDE connectors.
    """

    def __init__(self, learner: NexusLearner):
        self.learner = learner
        self._buffer: List[IDEEvent] = []
        self._last_flush = time.time()

    def receive_event(self, event: IDEEvent) -> Dict[str, Any]:
        """
        Receive an event from an IDE.
        Validates, classifies, and feeds to learner.
        """
        # Auto-classify reward if not provided
        if event.reward == 0.0 and event.event_type:
            event.reward = classify_event_reward(event.event_type, event.metadata)

        # Buffer events for batch processing
        self._buffer.append(event)

        # Flush if buffer is full or enough time passed
        if len(self._buffer) >= _BUFFER_SIZE or \
           time.time() - self._last_flush > _BUFFER_FLUSH_INTERVAL:
            self._flush()

        return {
            "status": "received",
            "event_id": event.event_id,
            "buffered": len(self._buffer),
            "timestamp": datetime.fromtimestamp(event.timestamp).isoformat()
        }

    def _flush(self):
        """Flush buffered events to learner"""
        if not self._buffer:
            return
        for event in self._buffer:
            try:
                self.learner.record_ide_event(event)
            except Exception as e:
                logger.error(f"Failed to record event {event.event_id}: {e}")
        count = len(self._buffer)
        self._buffer.clear()
        self._last_flush = time.time()
        logger.info(f"Flushed {count} IDE events to learner")

    def get_recent_events(self, limit: int = 50) -> List[Dict]:
        """Get recent events from database"""
        self._flush()  # Flush pending first
        conn = self.learner.conn
        cursor = conn.execute(
            "SELECT event_id, ide, model, event_type, reward, task_type, timestamp "
            "FROM ide_events ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        return [
            {
                "event_id": row[0],
                "ide": row[1],
                "model": row[2],
                "event_type": row[3],
                "reward": row[4],
                "task_type": row[5],
                "timestamp": datetime.fromtimestamp(row[6]).isoformat()
            }
            for row in cursor.fetchall()
        ]


# ─── Quick-start event emitters for each IDE ───────────────────────────────────

def emit_claude_code_event(
    event_type: str,
    model: str,
    provider: str,
    task_type: str,
    task_description: str,
    metadata: Optional[Dict] = None
) -> IDEEvent:
    """Convenience for Claude Code events"""
    reward = classify_event_reward(event_type, metadata)
    return create_event(
        ide="claude_code",
        model=model,
        provider=provider,
        task_type=task_type,
        event_type=event_type,
        reward=reward,
        task_description=task_description,
        confidence=1.0,
        metadata=metadata or {}
    )


def emit_aider_event(
    event_type: str,
    model: str,
    provider: str,
    task_type: str,
    task_description: str,
    metadata: Optional[Dict] = None
) -> IDEEvent:
    """Convenience for Aider events"""
    reward = classify_event_reward(event_type, metadata)
    return create_event(
        ide="aider",
        model=model,
        provider=provider,
        task_type=task_type,
        event_type=event_type,
        reward=reward,
        task_description=task_description,
        confidence=1.0,
        metadata=metadata or {}
    )


def emit_opencode_event(
    event_type: str,
    model: str,
    provider: str,
    task_type: str,
    task_description: str,
    metadata: Optional[Dict] = None
) -> IDEEvent:
    """Convenience for OpenCode events"""
    reward = classify_event_reward(event_type, metadata)
    return create_event(
        ide="opencode",
        model=model,
        provider=provider,
        task_type=task_type,
        event_type=event_type,
        reward=reward,
        task_description=task_description,
        confidence=1.0,
        metadata=metadata or {}
    )