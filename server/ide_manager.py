"""P5 + Phase 4: TUI IDE session manager + Harness Engine dashboard.

Manages multiple simultaneous TUI IDE sessions (Claude Code, OpenCode, Codex,
Hermes, Aider) launched under tmux. Tracks per-session harness efficiency and
exposes FTSR/MRC/TLT/CDR metrics (global + per-session).

Pattern from: Agent-of-Empires (tmux-based multi-TUI orchestration).
Metrics from: abtop (htop for AI agents) + NEXUS harness_metrics.
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
    project: str = "default"
    tool_session_id: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    tokens_consumed: int = 0
    tools_routed: int = 0
    tools_succeeded: int = 0
    tools_failed: int = 0
    tools_loaded: int = 0
    routing_time_ms: int = 0
    total_time_ms: int = 0
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

    def harness_metrics(self) -> dict[str, float]:
        from server.harness_metrics import session_metrics_from_counters
        metrics = session_metrics_from_counters(
            tools_routed=self.tools_routed,
            tools_succeeded=self.tools_succeeded,
            tools_failed=self.tools_failed,
            routing_time_ms=self.routing_time_ms,
            total_time_ms=self.total_time_ms,
            tools_loaded=self.tools_loaded,
        )
        try:
            from server.harness_metrics import compute_project_metrics
            metrics["cdr"] = compute_project_metrics(self.project).get("cdr", 0.0)
        except Exception:
            pass
        return metrics

    def to_dashboard_dict(self) -> dict:
        m = self.harness_metrics()
        return {
            "id": self.session_id,
            "type": self.ide_type,
            "project": self.project,
            "uptime": int(self.uptime_seconds),
            "tools_routed": self.tools_routed,
            "efficiency": round(self.harness_efficiency, 3),
            "tokens": self.tokens_consumed,
            "tmux": self.tmux_session,
            "tool_session_id": self.tool_session_id,
            "ftsr": m["ftsr"],
            "mrc": m["mrc"],
            "tlt": m["tlt"],
            "cdr": m["cdr"],
        }

    def to_harness_report(self) -> dict:
        m = self.harness_metrics()
        return {
            "session_id": self.session_id,
            "ide_type": self.ide_type,
            "project": self.project,
            "uptime_seconds": int(self.uptime_seconds),
            "tokens_consumed": self.tokens_consumed,
            "tools_routed": self.tools_routed,
            "tools_succeeded": self.tools_succeeded,
            "tools_failed": self.tools_failed,
            "tools_loaded": self.tools_loaded,
            "harness_efficiency": round(self.harness_efficiency, 4),
            "context_window_used": self.context_window_used,
            "avg_tokens_per_tool": self.tokens_consumed // max(self.tools_routed, 1),
            "tmux_session": self.tmux_session,
            "tool_session_id": self.tool_session_id,
            "routing_time_ms": self.routing_time_ms,
            "total_time_ms": self.total_time_ms,
            "notes": self.notes,
            "ftsr": m["ftsr"],
            "mrc": m["mrc"],
            "tlt": m["tlt"],
            "cdr": m["cdr"],
        }


IDLE_TIMEOUT_S = 1800  # 30 min


class IDEManager:
    """Manages multiple simultaneous TUI IDE sessions. Thread-safe."""

    def __init__(self) -> None:
        self.sessions: dict[str, IDESession] = {}
        self._lock = threading.Lock()

    def register(
        self,
        ide_type: str,
        pid: int,
        tmux_session: str | None = None,
        notes: str = "",
        *,
        project: str = "default",
    ) -> IDESession:
        if not ide_type:
            raise ValueError("ide_type required")
        session = IDESession(
            session_id=str(uuid.uuid4())[:8],
            ide_type=ide_type,
            pid=int(pid) if pid else 0,
            tmux_session=tmux_session,
            notes=notes,
            project=(project or "default").strip(),
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
            self.sessions = {k: v for k, v in self.sessions.items() if v.last_active > cutoff}
            return list(self.sessions.values())

    def link_tool_session(
        self,
        ide_session_id: str,
        tool_session_id: str,
        *,
        tools_loaded: int = 0,
        routing_time_ms: int = 0,
    ) -> bool:
        with self._lock:
            s = self.sessions.get(ide_session_id)
            if not s:
                return False
            s.tool_session_id = tool_session_id
            s.tools_loaded = int(tools_loaded)
            if routing_time_ms:
                s.routing_time_ms += int(routing_time_ms)
            s.last_active = time.time()
            return True

    def record_outcome(
        self,
        session_id: str,
        outcome: str,
        tokens: int = 0,
        *,
        duration_ms: int = 0,
    ) -> bool:
        with self._lock:
            s = self.sessions.get(session_id)
            if not s:
                return False
            s.tools_routed += 1
            s.last_active = time.time()
            if tokens:
                s.tokens_consumed += int(tokens)
            if duration_ms:
                s.total_time_ms += int(duration_ms)
            if outcome == "correct":
                s.tools_succeeded += 1
            elif outcome == "wrong":
                s.tools_failed += 1
            return True

    def update_context(
        self,
        session_id: str,
        tokens: int = 0,
        context_window: int = 0,
    ) -> bool:
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

    def get_dashboard(self, *, include_metrics: bool = False) -> dict:
        sessions = self.list_active()
        payload = {
            "total_sessions": len(sessions),
            "sessions": [s.to_dashboard_dict() for s in sessions],
        }
        if include_metrics:
            try:
                from server.harness_metrics import compute_global_metrics
                payload["global_metrics"] = compute_global_metrics()
            except Exception:
                payload["global_metrics"] = {}
            by_project: dict[str, list] = {}
            for s in sessions:
                by_project.setdefault(s.project, []).append(s.session_id)
            payload["by_project"] = {
                p: {"session_count": len(ids), "session_ids": ids}
                for p, ids in by_project.items()
            }
        return payload

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
    """Launch a TUI IDE in a detached tmux session. Returns (tmux_session, pid|None)."""
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


def launch_multi(
    ide_types: list[str],
    *,
    project: str = "default",
    register: bool = True,
) -> list[dict]:
    """Launch and register multiple IDE tmux sessions (multi-IDE orchestration).

    Returns a list of {ide_type, session_id, tmux_session, pid, registered}.
    Best-effort: skips IDE types when tmux/binary missing.
    """
    mgr = get_manager()
    results: list[dict] = []
    for ide_type in ide_types:
        tmux_session, pid = launch_tmux(ide_type)
        entry = {
            "ide_type": ide_type,
            "tmux_session": tmux_session,
            "pid": pid,
            "registered": False,
            "session_id": None,
        }
        if register and tmux_session:
            s = mgr.register(ide_type, pid or 0, tmux_session, project=project)
            entry["session_id"] = s.session_id
            entry["registered"] = True
        results.append(entry)
    return results


# Module-level singleton for use by the HTTP daemon
_MANAGER: IDEManager | None = None


def get_manager() -> IDEManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = IDEManager()
    return _MANAGER


if __name__ == "__main__":
    m = IDEManager()
    s = m.register("claude-code", 12345, "claude-code-1", project="nexus")
    m.link_tool_session(s.session_id, "abc123", tools_loaded=4, routing_time_ms=120)
    m.record_outcome(s.session_id, "correct", tokens=500, duration_ms=800)
    m.record_outcome(s.session_id, "wrong", tokens=300, duration_ms=400)
    m.record_outcome(s.session_id, "correct", tokens=200, duration_ms=600)
    print("dashboard:", m.get_dashboard(include_metrics=True))
    print("harness:", m.get_harness_report(s.session_id))