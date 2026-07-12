"""Harness data models — artifacts never become resources.name skill rows."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Artifact:
    """External knowledge unit from a harness channel (paper, repo, page)."""

    title: str
    url: str = ""
    snippet: str = ""
    score: float = 0.0
    channel: str = ""
    kind: str = "document"  # document | repo | page | memory
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChannelResult:
    channel_id: str
    artifacts: list[Artifact] = field(default_factory=list)
    error: Optional[str] = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


@dataclass
class HarnessPlan:
    """What harness channels/tools a session should load (not execute yet)."""

    intent: str  # general | research | browse | memory
    channels: list[str] = field(default_factory=list)
    toolsets: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    execute_research: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
