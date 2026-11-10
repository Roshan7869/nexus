"""Context assembly — one deep module behind the request-path seam.

assemble(query, project, ...) -> ContextBlock, with adapters behind it:
OKF sections (retrieved corpus, decision 0002 + Phase 1/2), repo-map
(v0 PageRank), and project notes + AGENTS.md (continuity).

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


def _adapter_okf_sections(query: str, project: str, git_root: str | None) -> tuple[str, str]:
    """Retrieve OKF sections for the query (Phase 3). Best-effort by contract.

    Uses the sections corpus explicitly (this is context assembly, not routing —
    Phase 2's verdict A governs RETRIEVAL_CORPUS on the routing path only).
    Hybrid vec stays on: quality matters more than the ~60ms here, and the
    budget loop drops this adapter (reported as ``okf_sections(budget)``) if a
    cold embedder load overshoots.
    """
    try:
        from server.db import get_db_path
        from server.router_v2.chunk_retrieval import retrieve_chunks
        # env-resolved path (NEXUS_DB_PATH override honored) — the module-level
        # DB_PATH const is captured at import and breaks DB isolation in tests.
        rc = retrieve_chunks(query, top_k=5, corpus="sections",
                             db_path=get_db_path())
        rows = rc.get("results", [])
        if not rows:
            return "", "okf_sections(empty)"
        body = "\n".join(
            f"- **{r['resource_name']}** · {r['heading'] or ''}: {r['snippet'][:160]}"
            for r in rows if r.get("resource_name")
        )
        return (f"## Relevant OKF sections\n{body}", "okf_sections") if body \
            else ("", "okf_sections(empty)")
    except Exception:
        return "", "okf_sections(error)"


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


_ADAPTER_NAMES = ("_adapter_okf_sections", "_adapter_repo_map",
                  "_adapter_project_memory", "_adapter_notes")


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
