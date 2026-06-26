"""P5: TUI IDE session manager.

Manages multiple simultaneous TUI IDE sessions (Claude Code, OpenCode, Codex,
Hermes, Aider) launched under tmux. Tracks per-session harness efficiency:
tools routed vs succeeded, tokens consumed, uptime.

Pattern from: Agent-of-Empires (tmux-based multi-TUI orchestration).
Metrics from: abtop (htop for AI agents).

This module is stateful in-process. A future persistent backend can swap the
dict store for sqlite by implementing the same interface. For now, sessions
live for the daemon process lifetime and are pruned after 30 min idle.
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IDESession:
    session_id: str
    ide_type: str  # "claude-code" | "opencode" | "codex" | "hermes" | "aider"
    pid: int
    tmux_session: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    tokens_consumed: int = 0
    tools_routed: int = 0
    tools_succeeded: int = 0
    tools_failed: int = 0
    context_window_used: int = 0
    notes: str = ""

    @property
    def harness_efficiency(self) -> float:
        if self.tools_routed == 0:
            return 0.0
        return self.tools_succeeded / self.tools_routed

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    def to_dashboard_dict(self) -> dict:
        return {
            "id": self.session_id,
            "type": self.ide_type,
            "uptime": int(self.uptime_seconds),
            "tools_routed": self.tools_routed,
            "efficiency": round(self.harness_efficiency, 3),
            "tokens": self.tokens_consumed,
            "tmux": self.tmux_session,
        }

    def to_harness_report(self) -> dict:
        return {
            "session_id": self.session_id,
            "ide_type": self.ide_type,
            "uptime_seconds": int(self.uptime_seconds),
            "tokens_consumed": self.tokens_consumed,
            "tools_routed": self.tools_routed,
            "tools_succeeded": self.tools_succeeded,
            "tools_failed": self.tools_failed,
            "harness_efficiency": round(self.harness_efficiency, 4),
            "context_window_used": self.context_window_used,
            "avg_tokens_per_tool": self.tokens_consumed // max(self.tools_routed, 1),
            "tmux_session": self.tmux_session,
            "notes": self.notes,
        }


IDLE_TIMEOUT_S = 1800  # 30 min


class IDEManager:
    """Manages multiple simultaneous TUI IDE sessions. Thread-safe."""

    def __init__(self) -> None:
        self.sessions: dict[str, IDESession] = {}
        self._lock = threading.Lock()

    def register(self, ide_type: str, pid: int, tmux_session: str | None = None,
                 notes: str = "") -> IDESession:
        if not ide_type or pid <= 0:
            raise ValueError("ide_type and positive pid required")
        session = IDESession(
            session_id=str(uuid.uuid4())[:8],
            ide_type=ide_type,
            pid=int(pid),
            tmux_session=tmux_session,
            notes=notes,
        )
        with self._lock:
            self.sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> IDESession | None:
        with self._lock:
            return self.sessions.get(session_id)

    def list_active(self) -> list[IDESession]:
        cutoff = time.time() - IDLE_TIMEOUT_S
        with self._lock:
            # prune idle
            self.sessions = {k: v for k, v in self.sessions.items() if v.last_active > cutoff}
            return list(self.sessions.values())

    def record_outcome(self, session_id: str, outcome: str,
                       tokens: int = 0) -> bool:
        with self._lock:
            s = self.sessions.get(session_id)
            if not s:
                return False
            s.tools_routed += 1
            s.last_active = time.time()
            if tokens:
                s.tokens_consumed += int(tokens)
            if outcome == "correct":
                s.tools_succeeded += 1
            elif outcome == "wrong":
                s.tools_failed += 1
            return True

    def update_context(self, session_id: str, tokens: int = 0,
                       context_window: int = 0) -> bool:
        with self._lock:
            s = self.sessions.get(session_id)
            if not s:
                return False
            if tokens:
                s.tokens_consumed += int(tokens)
            if context_window:
                s.context_window_used = int(context_window)
            s.last_active = time.time()
            return True

    def get_harness_report(self, session_id: str) -> dict:
        s = self.get(session_id)
        return s.to_harness_report() if s else {}

    def get_dashboard(self) -> dict:
        sessions = self.list_active()
        return {
            "total_sessions": len(sessions),
            "sessions": [s.to_dashboard_dict() for s in sessions],
        }

    def unregister(self, session_id: str) -> bool:
        with self._lock:
            return self.sessions.pop(session_id, None) is not None


# ── tmux launch helpers ────────────────────────────────────────────────

TMUX_LAUNCH_CMD = {
    "claude-code": "claude",
    "opencode": "opencode",
    "codex": "codex",
    "hermes": "hermes",
    "aider": "aider",
}


def launch_tmux(ide_type: str, session_name: str | None = None) -> tuple[str, int | None]:
    """Launch a TUI IDE in a detached tmux session. Returns (tmux_session, pid|None).

    Best-effort: returns ('', None) if tmux or the IDE binary is missing.
    """
    cmd = TMUX_LAUNCH_CMD.get(ide_type)
    if not cmd:
        raise ValueError(f"unknown ide_type {ide_type!r}")
    session_name = session_name or f"{ide_type}-{str(uuid.uuid4())[:6]}"
    try:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, cmd],
            check=True, capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "", None
    # best-effort pid lookup
    pid = None
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if out:
            pid = int(out.splitlines()[0])
    except Exception:
        pass
    return session_name, pid


# Module-level singleton for use by the HTTP daemon
_MANAGER: IDEManager | None = None


def get_manager() -> IDEManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = IDEManager()
    return _MANAGER


if __name__ == "__main__":
    # smoke test (no tmux needed)
    m = IDEManager()
    s = m.register("claude-code", 12345, "claude-code-1")
    m.record_outcome(s.session_id, "correct", tokens=500)
    m.record_outcome(s.session_id, "wrong", tokens=300)
    m.record_outcome(s.session_id, "correct", tokens=200)
    print("dashboard:", m.get_dashboard())
    print("harness:", m.get_harness_report(s.session_id))