"""NEXUS Forge — turn a project brief into a parallel cluster team in tmux.

Settled by grilling 2026-08-17: lead seat (analyzer_planner) + core peers;
every seat is an interactive OpenCode TUI in the shared project dir with a
disjoint territory; briefs are FILES (send-keys only types the pointer line,
which is TUI-safe); the lead is the honesty authority — seats report via
project_note and nothing counts as done without a lead-visible artifact.

Team bookkeeping (_TEAMS) is process memory for status convenience only —
durable state lives in project_continuity's tables and .nexus/briefs/.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from server.cluster_config import CLUSTER_DEFINITIONS

LEAD_CLUSTER = "analyzer_planner"
CORE_PEERS = frozenset({"frontend_ui", "backend_api", "quality_security", "devops_infra"})
MAX_SEATS = 6
BRIEFS_SUBDIR = ".nexus/briefs"
SESSION_PREFIX = "nexus-forge-"
_SESSION_SEP = "--"  # cannot occur in a project slug, so the project is recoverable exactly
BOOT_WAIT_S = 8.0   # OpenCode TUI boot on 2-core CPU before pointer delivery
BOOT_RETRY_WAIT_S = 3.0  # wait before re-sending a pointer the TUI dropped

TERRITORIES = {
    "analyzer_planner": ".",        # lead: whole-repo review rights
    "frontend_ui": "frontend/",
    "backend_api": "backend/",
    "quality_security": "security/",
    "devops_infra": "ops/",
    "architect": "docs/architecture/",
    "knowledge_wiki": "docs/",
    "general": "notes/",
}

_TEAMS: dict[str, list[dict]] = {}  # project -> spawn records (ephemeral)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _keyword_additions(brief: str) -> list[str]:
    text = brief.lower()
    additions = []
    for name, cdef in CLUSTER_DEFINITIONS.items():
        if name == LEAD_CLUSTER or name in CORE_PEERS:
            continue
        if any(kw.lower() in text for kw in cdef.get("trigger_keywords", [])):
            additions.append(name)
    return additions


def _render_brief(seat: dict, brief: str, cdef: dict) -> str:
    skills = ", ".join([cdef.get("default_skill", "")] + cdef.get("default_skills", []))
    agents = ", ".join(cdef.get("default_agents", []))
    lines = [
        f"# NEXUS Forge Workstream — {cdef.get('display_name', seat['cluster'])}",
        "",
        f"## Mission",
        brief,
        "",
        f"## Your territory (stay inside it)",
        f"`{seat['territory']}` — do not edit outside this path without lead approval.",
        "",
        f"## Your cluster",
        cdef.get("description", ""),
        f"Invoke via routing: `{skills}` · agents: {agents or '—'}",
        "",
        "## Session protocol (mandatory)",
        "1. `project_attach(project=<name>, git_root=<repo root>)` first.",
        "2. Route every non-trivial task: `find_skills(task=..., project=...)`.",
        "3. Durable findings → `project_note(project=..., text=...)` — the lead reads these.",
        "4. When your workstream completes, report via project_note with what",
        "   exists on disk (paths). Completion is what the LEAD can verify, not what you say.",
    ]
    if seat["role"] == "lead":
        lines += [
            "",
            "## Lead responsibilities",
            "- Consume `forge_status(project=...)` regularly; review peer panes and notes.",
            "- Assign follow-ups via project_note (peers read them on their next route).",
            "- You are the honesty authority: no workstream is 'done' until you have",
            "  seen the artifact (file, test output, or command result) yourself.",
        ]
    return "\n".join(lines) + "\n"


def plan_project(brief: str, project_dir: str | None = None,
                 extra_clusters: list[str] | None = None) -> dict:
    """Map a project brief to a lead + peer team; optionally write brief files."""
    chosen: list[str] = [LEAD_CLUSTER] + sorted(CORE_PEERS)
    for name in _keyword_additions(brief) + list(extra_clusters or []):
        if name in CLUSTER_DEFINITIONS and name not in chosen:
            chosen.append(name)
    # Cap applies to additions only — lead + core peers are never dropped.
    while len(chosen) > MAX_SEATS and chosen[-1] not in CORE_PEERS | {LEAD_CLUSTER}:
        chosen.pop()

    seats = []
    for name in chosen:
        cdef = CLUSTER_DEFINITIONS.get(name, {})
        seat = {
            "cluster": name,
            "role": "lead" if name == LEAD_CLUSTER else "peer",
            "territory": TERRITORIES.get(name, "notes/"),
            "display_name": cdef.get("display_name", name),
            "brief_path": "",
        }
        if project_dir:
            bdir = Path(project_dir) / BRIEFS_SUBDIR
            bdir.mkdir(parents=True, exist_ok=True)
            bpath = bdir / f"{name}.md"
            bpath.write_text(_render_brief(seat, brief, cdef))
            seat["brief_path"] = str(bpath)
        seats.append(seat)

    return {"brief": brief, "planned_at": _now(), "seats": seats}


# ── tmux plumbing (thin, mockable) ────────────────────────────────────────

def _tmux(*args: str) -> tuple[int, str, str]:
    r = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)
    return r.returncode, r.stdout, r.stderr


def _session_name(project: str, cluster: str) -> str:
    if _SESSION_SEP in project or not project:
        raise ValueError(f"invalid forge project name {project!r}")
    return f"{SESSION_PREFIX}{project}{_SESSION_SEP}{cluster}"


def _session_project(name: str) -> str | None:
    """Project that owns a forge session name, or None if not a forge session."""
    if not name.startswith(SESSION_PREFIX) or _SESSION_SEP not in name:
        return None
    return name[len(SESSION_PREFIX):].split(_SESSION_SEP, 1)[0]


def _session_cluster(name: str) -> str | None:
    if _SESSION_SEP not in name:
        return None
    return name.split(_SESSION_SEP, 1)[1]


def _launch_seat(seat: dict, project_dir: str, project: str) -> tuple[str, int | None]:
    from server.ide_manager import launch_tmux
    name = _session_name(project, seat["cluster"])
    try:
        return launch_tmux("opencode", name, cwd=project_dir)
    except Exception:
        return "", None


def _send_pointer(line: str, session: str) -> bool:
    rc, _, _ = _tmux("send-keys", "-t", session, "-l", line)
    if rc != 0:
        return False
    _tmux("send-keys", "-t", session, "Enter")
    return True


def _discover_sessions(project: str) -> list[str]:
    """Live tmux sessions for this project — cross-process source of truth."""
    rc, out, _ = _tmux("list-sessions", "-F", "#{session_name}")
    if rc != 0:
        return []
    return [
        l.strip() for l in out.splitlines()
        if _session_project(l.strip()) == project
    ]


def _pane_has_pointer(session: str, cluster: str) -> bool:
    rc, out, _ = _tmux("capture-pane", "-p", "-t", session)
    return f"{BRIEFS_SUBDIR}/{cluster}.md" in out


def _deliver_pointer(session: str, cluster: str) -> bool:
    """Send pointer, verify the TUI shows it, retry once if dropped.

    Live lesson (2026-08-17 demo): a send into a not-yet-input-ready TUI
    vanishes silently — never trust delivery without pane evidence.
    """
    line = f"Read {BRIEFS_SUBDIR}/{cluster}.md and execute your workstream."
    if _send_pointer(line, session) is False:
        return False
    if _pane_has_pointer(session, cluster):
        return True
    time.sleep(BOOT_RETRY_WAIT_S)
    return _send_pointer(line, session) is not False


def spawn_team(plan: dict, project_dir: str, project: str = "project") -> dict:
    """Launch every seat as a detached OpenCode TUI and deliver its pointer line.

    Two-phase: launch all seats (with remain-on-exit so crashes stay visible),
    wait one boot period, THEN type pointers — typing into a booting TUI kills
    the seat (found live: the first demo team died exactly this way).
    """
    launched: list[dict] = []
    for seat in plan["seats"]:
        sess, pid = _launch_seat(seat, project_dir, project)
        record = {
            "cluster": seat["cluster"], "role": seat["role"],
            "tmux_session": sess, "pid": pid,
            "brief_path": seat["brief_path"], "delivered": False,
        }
        if sess:
            # keep dead panes inspectable instead of vanishing sessions
            _tmux("set-option", "-t", sess, "remain-on-exit", "on")
        launched.append(record)

    time.sleep(BOOT_WAIT_S)
    for record in launched:
        if record["tmux_session"]:
            record["delivered"] = _deliver_pointer(
                record["tmux_session"], record["cluster"])

    _TEAMS[project] = launched
    try:  # durable registration (best-effort — spawn must never fail on the DB)
        from server.project_continuity import attach
        attach(project, git_root=project_dir, ide="opencode")
    except Exception:
        pass
    return {"project": project, "spawned_at": _now(), "seats": launched}


def team_status(project: str) -> dict:
    """Per seat: alive + last pane lines. Honesty lives in the lead's review.

    Seats are discovered from live tmux (works across processes); the in-process
    registry only enriches metadata (role, brief path) when available.
    """
    meta_by_session = {r["tmux_session"]: r for r in _TEAMS.get(project, [])}
    names = list(dict.fromkeys(
        _discover_sessions(project) + [s for s in meta_by_session if s]))

    seats = []
    for name in names:
        rc, out, _ = _tmux("capture-pane", "-p", "-t", name)
        lines = [l for l in out.splitlines() if l.strip()][-8:]
        meta = meta_by_session.get(name, {})
        seats.append({
            "tmux_session": name,
            "cluster": _session_cluster(name) or meta.get("cluster"),
            "role": meta.get("role", "peer"),
            "brief_path": meta.get("brief_path", ""),
            "alive": rc == 0,
            "pane_tail": lines,
        })
    return {"project": project, "checked_at": _now(), "seats": seats}


def _kill_session(session: str) -> int:
    return _tmux("kill-session", "-t", session)[0]


def stop_team(project: str) -> dict:
    """Kill ONLY this project's forge sessions (discovered + registered)."""
    meta_by_session = {r["tmux_session"] for r in _TEAMS.get(project, [])}
    names = list(dict.fromkeys(_discover_sessions(project) + list(meta_by_session)))
    killed = 0
    for name in names:
        if name and _kill_session(name) == 0:
            killed += 1
    _TEAMS.pop(project, None)
    return {"project": project, "killed": killed}
