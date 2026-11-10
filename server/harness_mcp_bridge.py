"""Unified harness MCP bridge — single namespace for context + research tools.

Phase 2: delegates to ``context_harness/server.py`` implementations and registers
them on the main NEXUS MCP server (``nexus-mcp-enhanced.py``).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SERVER_PATH = Path(__file__).parent / "context_harness" / "server.py"
_harness_mod: Any = None

# Tools merged into unified NEXUS MCP (harness_* keep prefix; context_* unchanged)
HARNESS_TOOL_NAMES: tuple[str, ...] = (
    "context_harness",
    "context_search",
    "context_store",
    "context_doctor",
    "harness_fetch",
    "harness_fetch_batch",
    "harness_sherlock",
    "harness_spider_start",
    "harness_spider_status",
    "harness_spider_results",
    "harness_pattern_extract",
    "harness_research",
    "harness_arxiv",
    "harness_scholar_s2",
    "harness_github_search",
    "harness_paperswithcode",
    "harness_openalex",
    "harness_crossref",
    "harness_pubmed",
    "harness_lookup",
    "harness_target_site",
    "harness_research_plan",
    "harness_smart_research",
    "harness_study_project",
    "research_sites_list",
)

# Default harness tools injected into research-heavy ephemeral sessions
DEFAULT_RESEARCH_HARNESS_TOOLS: tuple[str, ...] = (
    "harness_smart_research",
    "harness_research_plan",
    "harness_arxiv",
    "harness_target_site",
    "context_search",
)


def _load_harness_module() -> Any:
    global _harness_mod
    if _harness_mod is not None:
        return _harness_mod

    nexus_root = str(Path(__file__).resolve().parent.parent)
    server_dir = str(Path(__file__).resolve().parent)
    for p in (nexus_root, server_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    spec = importlib.util.spec_from_file_location("nexus_harness_server", _SERVER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load harness server: {_SERVER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _harness_mod = mod
    return mod


def get_harness_callable(name: str) -> Callable[..., str]:
    mod = _load_harness_module()
    fn = getattr(mod, name, None)
    if fn is None or not callable(fn):
        raise KeyError(f"harness tool not found: {name}")
    return fn


def call_harness_tool(name: str, **kwargs: Any) -> str:
    """Dispatch a harness tool by name."""
    if name == "context_store":
        return context_store_unified(**kwargs)
    if name == "harness_smart_research":
        return harness_smart_research_unified(**kwargs)
    fn = get_harness_callable(name)
    return fn(**kwargs)


def context_store_unified(
    key: str,
    value: str,
    source: str = "memory",
    tags: str = "",
    project: str = "default",
    task_type: str = "general",
) -> str:
    """Store context with project + task_type scoping."""
    from server.context_harness.engine import ContextHarnessEngine

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    scoped_key = _scoped_cache_key(project, task_type, key)
    engine = ContextHarnessEngine()
    result = engine.store(scoped_key, value, source, tag_list, project=project, task_type=task_type)

    try:
        from server.db import db_conn
        with db_conn() as conn:
            conn.execute(
                """
                INSERT INTO project_contexts (project, context_type, title, content, tags_json)
                VALUES (?, 'learning', ?, ?, ?)
                """,
                (project, key[:120], value[:8000], json.dumps(tag_list + [task_type])),
            )
    except Exception as exc:
        logger.debug("project_contexts insert skipped: %s", exc)

    result["project"] = project
    result["task_type"] = task_type
    result["scoped_key"] = scoped_key
    return json.dumps(result, indent=2)


def harness_smart_research_unified(
    query: str,
    max_results: int = 8,
    execute: bool = True,
    project: str = "default",
    ingest: bool = False,
) -> str:
    """Run smart research and log channel plan to NEXUS learning."""
    fn = get_harness_callable("harness_smart_research")
    raw = fn(query=query, max_results=max_results, execute=execute, project=project, ingest=ingest)
    try:
        payload = json.loads(raw)
        _record_smart_research_outcome(query, payload, project=project)
    except Exception as exc:
        logger.debug("smart research outcome log failed: %s", exc)
    return raw


def _record_smart_research_outcome(query: str, payload: dict, *, project: str) -> None:
    try:
        from server.learning import record_outcome
    except Exception:
        return

    channels = payload.get("channels_executed") or [
        c.get("channel") for c in payload.get("channels", [])
        if isinstance(c, dict)
    ]
    if not channels:
        per_channel = payload.get("per_channel") or []
        channels = [row.get("channel") for row in per_channel if isinstance(row, dict)]

    label = "+".join(c for c in channels if c) or "plan_only"
    fused = payload.get("fused") or []
    # The bridge does not judge outcomes: the payload is returned as-is and the
    # verdict comes from the caller's explicit report_outcome (FIX-DESIGN S1-b).
    outcome = "unverified"
    meta = {
        "intent": payload.get("intent"),
        "detected_fields": payload.get("detected_fields"),
        "detected_publishers": payload.get("detected_publishers"),
        "channels_executed": channels,
    }
    record_outcome(
        skill_name=f"harness:smart_research:{label}",
        outcome=outcome,
        task_summary=f"{query[:160]} | {json.dumps(meta, default=str)[:300]}",
        project=project,
        ide="claude-code",
        tokens_used=0,
        quality=None,
    )


def _scoped_cache_key(project: str, task_type: str, key: str) -> str:
    project = (project or "default").strip()
    task_type = (task_type or "general").strip()
    return f"{project}:{task_type}:{key}"


def is_research_query(query: str) -> bool:
    try:
        from server.harness_engine.toolsets import is_research_intent
        return is_research_intent(query)
    except Exception:
        q = (query or "").lower()
        signals = (
            "research", "paper", "arxiv", "ssrn", "study", "survey", "journal",
            "harvard", "cite", "citation", "literature", "academic", "doi",
        )
        return any(s in q for s in signals)


def research_harness_tools_for_query(query: str) -> list[str]:
    """P3: load toolset-scoped tools for the query intent."""
    try:
        from server.harness_engine.toolsets import plan_toolsets, tools_for_toolsets
        return tools_for_toolsets(plan_toolsets(query))
    except Exception:
        if is_research_query(query):
            return list(DEFAULT_RESEARCH_HARNESS_TOOLS)
        return ["context_search", "harness_target_site"]


def register_harness_tools(mcp: Any) -> int:
    """Register all harness tools on the unified NEXUS FastMCP instance."""
    count = 0
    for name in HARNESS_TOOL_NAMES:
        if name == "context_store":
            @mcp.tool(name=name)
            def _context_store(
                key: str,
                value: str,
                source: str = "memory",
                tags: str = "",
                project: str = "default",
                task_type: str = "general",
            ) -> str:
                """Store a (key, value) context entry, namespaced by project and task type."""
                return context_store_unified(
                    key=key, value=value, source=source, tags=tags,
                    project=project, task_type=task_type,
                )
            count += 1
            continue

        if name == "harness_smart_research":
            @mcp.tool(name=name)
            def _smart_research(
                query: str,
                max_results: int = 8,
                execute: bool = True,
                project: str = "default",
                ingest: bool = False,
            ) -> str:
                """Run smart research via the harness engine for a query; returns numbered results."""
                return harness_smart_research_unified(
                    query=query, max_results=max_results, execute=execute,
                    project=project, ingest=ingest,
                )
            count += 1
            continue

        tool_name = name

        def _make(tool_name: str = tool_name):
            @mcp.tool(name=tool_name)
            def _tool(kwargs_json: str = "{}") -> str:
                """Tool args as JSON string (parsed as kwargs to the underlying harness function)."""
                kwargs = json.loads(kwargs_json)
                return call_harness_tool(tool_name, **kwargs)
            return _tool

        _make()
        count += 1

    # P3 meta tools: plan + orchestrator + browser adapter status
    try:
        @mcp.tool(name="harness_plan")
        def harness_plan(task: str, execute_research: bool = False) -> str:
            """Plan harness channels/toolsets for a task (P3). Optionally run research orchestrator."""
            from server.harness_engine.plan import plan_harness
            from server.harness_engine.orchestrator import run_research_orchestrator
            plan = plan_harness(task, execute_research=execute_research)
            out = plan.to_dict()
            if execute_research and plan.intent == "research":
                out["research"] = run_research_orchestrator(task)
            return json.dumps(out, indent=2)

        @mcp.tool(name="harness_research_orchestrator")
        def harness_research_orchestrator(
            query: str,
            channels: str = "arxiv,s2,github,pwc,scholar",
            max_results: int = 5,
        ) -> str:
            """Run research orchestrator-worker; returns Artifact[] (not skills)."""
            from server.harness_engine.orchestrator import run_research_orchestrator
            return json.dumps(
                run_research_orchestrator(
                    query, channels=channels, max_results=max_results,
                ),
                indent=2,
            )

        @mcp.tool(name="browser_harness_status")
        def browser_harness_status_tool() -> str:
            """Status of optional browser-harness channel adapter."""
            from server.harness_engine.orchestrator import browser_harness_status
            return json.dumps(browser_harness_status(), indent=2)

        @mcp.tool(name="harness_toolsets")
        def harness_toolsets(task: str = "") -> str:
            """List toolsets (core|research|browser|memory) and tools for a task."""
            from server.harness_engine.toolsets import (
                TOOLSETS, plan_toolsets, tools_for_toolsets, detect_intent,
            )
            if task:
                ts = plan_toolsets(task)
                return json.dumps({
                    "intent": detect_intent(task),
                    "toolsets": ts,
                    "tools": tools_for_toolsets(ts),
                }, indent=2)
            return json.dumps({
                "toolsets": {k: list(v) for k, v in TOOLSETS.items()},
            }, indent=2)

        count += 4
    except Exception as exc:
        logger.warning("P3 harness meta tools failed to register: %s", exc)

    logger.info("Registered %d harness tools on unified MCP", count)
    return count


def list_unified_tool_names(extra: list[str] | None = None) -> list[str]:
    names = list(HARNESS_TOOL_NAMES)
    names.extend([
        "harness_plan",
        "harness_research_orchestrator",
        "browser_harness_status",
        "harness_toolsets",
    ])
    if extra:
        names.extend(extra)
    return sorted(set(names))