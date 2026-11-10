"""Harness registry — one Protocol per agent surface (cf. OpenResearch harness/mod.rs).

Adding a harness = one new file/class + one REGISTRY line. No dispatcher edits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class WirePart:  # cf. OpenResearch WirePart (text/tool/prompt)
    kind: str               # "text" | "tool" | "prompt"
    text: str | None = None
    tool: str | None = None
    state: dict = field(default_factory=dict)


@dataclass
class HarnessInfo:
    id: str
    name: str
    installed: bool
    models: list[str] = field(default_factory=list)


@runtime_checkable
class Harness(Protocol):
    def id(self) -> str: ...
    def name(self) -> str: ...
    def supports_chat(self) -> bool: ...
    def detect(self) -> HarnessInfo | None: ...
    async def run_turn(self, session: object, message: str) -> list[WirePart]: ...
    def skill_shim(self) -> str | None: ...


class UnsupportedHarness(Exception):
    """Raised when a harness lacks a capability (detection-only / install-only)."""


class OpenCodeHarness:
    """First registry impl. Thin surface over M8 adapter facts."""

    def id(self) -> str:
        return "opencode"

    def name(self) -> str:
        return "OpenCode"

    def supports_chat(self) -> bool:
        return True

    def detect(self) -> HarnessInfo | None:
        from server.opencode_adapter import get_opencode_config  # lazy, may not exist pre-M8
        try:
            cfg = get_opencode_config()
            return HarnessInfo(id=self.id(), name=self.name(),
                               installed=bool(cfg), models=[])
        except Exception:
            return HarnessInfo(id=self.id(), name=self.name(), installed=False)

    async def run_turn(self, session: object, message: str) -> list[WirePart]:
        raise UnsupportedHarness("streaming run_turn lands in A2; use task_start payload path")

    def skill_shim(self) -> str | None:
        return None


REGISTRY: list[Harness] = [OpenCodeHarness()]  # ONE line per future harness


def get(ide: str | None) -> Harness:
    """Resolve harness by IDE id; default OpenCode; never raise on unknown (compat)."""
    want = (ide or "opencode").strip().lower()
    for h in REGISTRY:
        try:
            if h.id() == want:
                return h
        except Exception:
            continue
    return REGISTRY[0]
