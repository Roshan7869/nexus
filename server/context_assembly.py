"""Context assembly — one deep module behind the request-path seam.

assemble(query, project, ...) -> ContextBlock, with adapters behind it:
repo-map (v0 PageRank), project notes + AGENTS.md (continuity), and a
reserved OKF-sections slot (decision 0002: backfill sections as chunks first).

Interface rules: hard budget_ms (adapters past budget are skipped and
reported), empty corpora are safe, and the block is pure text a caller can
prepend to any prompt. The inline lookups in tool_session_broker migrate
into adapters in a follow-up; today the broker PREPENDS this block.
"""
from __future__ import annotations

import time
from pathlib import Path


def _git_root_for(project: str) -> str | None:
    """Best-effort git_root from the continuity registry when caller has none."""
    try:
        from server.db import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT git_root FROM projects WHERE name=?", (project,)).fetchone()
        finally:
            conn.close()
        return row["git_root"] if row else None
    except Exception:
        return None


def _adapter_repo_map(query: str, project: str, git_root: str | None) -> tuple[str, str]:
    git_root = git_root or _git_root_for(project)
    if not git_root or not Path(git_root).is_dir():
        return "", "repo_map(skipped: no git_root)"
    from server.repo_map import build
    m = build(git_root, budget_tokens=400)["map"]
    return (f"## Project map\n```\n{m}\n```" if m else ""), "repo_map"


def _adapter_project_memory(query: str, project: str, git_root: str | None) -> tuple[str, str]:
    try:
        from server.project_continuity import _read_agents_md
        agents = _read_agents_md(git_root)
        parts = []
        if agents["exists"]:
            parts.append(f"## AGENTS.md (excerpt)\n{agents['excerpt']}")
        return ("\n".join(parts), "project_memory") if parts else ("", "project_memory(empty)")
    except Exception:
        return "", "project_memory(error)"


def _adapter_notes(query: str, project: str, git_root: str | None) -> tuple[str, str]:
    try:
        from server.db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT text FROM project_notes WHERE project=? "
                "ORDER BY created_at DESC, id DESC LIMIT 5", (project,)).fetchall()
        finally:
            conn.close()
        if not rows:
            return "", "notes(empty)"
        body = "\n".join(f"- {r['text'][:200]}" for r in rows)
        return f"## Project notes\n{body}", "notes"
    except Exception:
        return "", "notes(error)"


_ADAPTER_NAMES = ("_adapter_repo_map", "_adapter_project_memory", "_adapter_notes")


def assemble(query: str, project: str, task_type: str = "general",
             budget_ms: int = 800, git_root: str | None = None) -> dict:
    """Assemble a ContextBlock within a hard time budget."""
    started = time.monotonic()
    sections: list[str] = []
    sources: list[str] = []
    for name in _ADAPTER_NAMES:  # late-bound: tests/patches can swap adapters
        adapter = globals()[name]
        remaining = budget_ms - (time.monotonic() - started) * 1000
        if remaining <= 0:
            sources.append(f"{name}(budget)")
            continue
        try:
            text, src = adapter(query, project, git_root)
        except Exception:
            text, src = "", f"{name}(error)"
        sources.append(src)
        if text:
            sections.append(text)
    header = f"# NEXUS context — {project}" + (f" · {task_type}" if task_type else "")
    block = "\n\n".join([header] + sections) if sections else ""
    return {
        "block": block,
        "sources": sources,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "budget_ms": budget_ms,
    }
