"""Ephemeral tool session broker — NEXUS Second Brain core.

Per task: route → select ≤8 tools → inject context → IDE executes → dump + learn.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from server.db import db_conn, get_connection, get_db_path
from server.ephemeral_mcp_proxy import BASELINE_TOOLS, CONTEXT_BASELINE_TOOLS, merge_tool_surface
from server.harness_mcp_bridge import research_harness_tools_for_query
from server.service_layer import SERVICE_BUNDLES, bundle_for_cluster

logger = logging.getLogger(__name__)

MAX_SESSION_TOOLS = 8
SCHEMA_PATH = get_db_path().parent.parent / "migrations" / "015_tool_sessions.sql"


@dataclass
class ToolSession:
    session_id: str
    project: str
    query: str
    cluster: str | None = None
    task_type: str = "general"
    tools_loaded: list[str] = field(default_factory=list)
    harness_tools: list[str] = field(default_factory=list)
    context_block: str = ""
    context_hash: str = ""
    ide: str = "claude-code"
    status: str = "active"
    outcome: str | None = None
    summary: str = ""
    model: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["visible_tools"] = merge_tool_surface(
            self.tools_loaded, include_harness=True, harness_tools=self.harness_tools,
        )
        data["tool_count"] = len(self.tools_loaded)
        data["harness_tool_count"] = len(self.harness_tools)
        return data


def ensure_tool_session_schema(conn: sqlite3.Connection | None = None) -> None:
    """Apply tool_sessions + project_contexts migration idempotently."""
    owns = conn is None
    if owns:
        conn = get_connection()
    try:
        if SCHEMA_PATH.exists():
            conn.executescript(SCHEMA_PATH.read_text())
        else:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tool_sessions (
                    session_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL DEFAULT 'default',
                    query TEXT NOT NULL,
                    cluster TEXT,
                    task_type TEXT NOT NULL DEFAULT 'general',
                    tools_json TEXT NOT NULL DEFAULT '[]',
                    context_block TEXT NOT NULL DEFAULT '',
                    context_hash TEXT,
                    ide TEXT NOT NULL DEFAULT 'claude-code',
                    status TEXT NOT NULL DEFAULT 'active',
                    outcome TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    model TEXT,
                    started_at TEXT NOT NULL DEFAULT (datetime('now')),
                    ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS project_contexts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    context_type TEXT NOT NULL DEFAULT 'session_archive',
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    source_session_id TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
        conn.commit()
    finally:
        if owns:
            conn.close()


class ToolSessionBroker:
    """Manage ephemeral IDE tool sessions backed by SQLite."""

    def __init__(self) -> None:
        self._active: dict[str, ToolSession] = {}
        ensure_tool_session_schema()

    def start_session(
        self,
        project: str,
        query: str,
        *,
        ide: str = "claude-code",
        use_embedding: bool = False,
        max_tools: int = MAX_SESSION_TOOLS,
        ide_session_id: str | None = None,
    ) -> ToolSession:
        project = (project or "default").strip()
        query = (query or "").strip()
        if not query:
            raise ValueError("query is required")

        route = self._route(query, project, ide=ide, use_embedding=use_embedding)
        cluster = route.get("cluster") or "general"
        task_type = route.get("task_type") or "general"
        tools = self._select_tools(route, cluster, max_tools=max_tools)
        # P3/P4: harness tools from route plan when present, else intent toolsets
        harness_tools = route.get("harness_tools") or research_harness_tools_for_query(query)
        if isinstance(harness_tools, list):
            harness_tools = harness_tools[:16]
        context_block = self._build_context_block(project, query, route, tools)

        session_id = uuid.uuid4().hex[:16]
        ctx_hash = hashlib.sha256(context_block.encode()).hexdigest()[:16]

        session = ToolSession(
            session_id=session_id,
            project=project,
            query=query,
            cluster=cluster,
            task_type=task_type,
            tools_loaded=tools,
            harness_tools=harness_tools,
            context_block=context_block,
            context_hash=ctx_hash,
            ide=ide,
            model=route.get("model"),
        )
        self._active[session_id] = session
        self._persist_session(session)
        if ide_session_id:
            try:
                from server.ide_manager import get_manager
                get_manager().link_tool_session(
                    ide_session_id,
                    session_id,
                    tools_loaded=len(tools),
                    routing_time_ms=int((time.time() - session.started_at) * 1000),
                )
            except Exception as exc:
                logger.debug("ide_session link skipped: %s", exc)
        logger.info(
            "tool_session started id=%s project=%s cluster=%s tools=%d",
            session_id, project, cluster, len(tools),
        )
        return session

    def end_session(
        self,
        session_id: str,
        outcome: str = "correct",
        summary: str = "",
        ide_session_id: str | None = None,
        *,
        record_learning: bool = True,
    ) -> dict[str, Any]:
        session = self._resolve_session(session_id)
        if session.status != "active":
            return {"status": "already_closed", "session": session.to_dict()}

        outcome = self._normalize_outcome(outcome)
        session.status = "completed" if outcome == "correct" else "abandoned"
        session.outcome = outcome
        session.summary = (summary or "").strip()
        session.ended_at = time.time()

        self._archive_context(session)
        # P4: execution_plane may record primary skill itself (with quality)
        if record_learning:
            self._record_learning(session, outcome)
        self._update_persisted_session(session)
        self._active.pop(session_id, None)

        if ide_session_id:
            try:
                from server.ide_manager import get_manager
                duration_ms = int((session.ended_at - session.started_at) * 1000) if session.ended_at else 0
                get_manager().record_outcome(
                    ide_session_id,
                    self._normalize_outcome(outcome),
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                logger.debug("ide_session outcome skipped: %s", exc)

        return {
            "status": "dumped",
            "session_id": session_id,
            "outcome": outcome,
            "tools_removed": session.tools_loaded,
            "baseline_tools": sorted(BASELINE_TOOLS),
            "project": session.project,
        }

    def get_session(self, session_id: str) -> ToolSession | None:
        try:
            return self._resolve_session(session_id)
        except KeyError:
            return None

    def get_visible_tools(self, session_id: str) -> list[str]:
        session = self._resolve_session(session_id)
        return merge_tool_surface(
            session.tools_loaded, include_harness=True, harness_tools=session.harness_tools,
        )

    def get_context_block(self, session_id: str) -> str:
        return self._resolve_session(session_id).context_block

    def list_active_sessions(self, project: str | None = None) -> list[dict[str, Any]]:
        sessions = [
            s.to_dict() for s in self._active.values()
            if project is None or s.project == project
        ]
        return sorted(sessions, key=lambda x: x.get("started_at", 0), reverse=True)

    def get_mcp_proxy_config(self, session_id: str) -> dict[str, Any]:
        session = self._resolve_session(session_id)
        return {
            "session_id": session.session_id,
            "project": session.project,
            "visible_tools": merge_tool_surface(
                session.tools_loaded, include_harness=True, harness_tools=session.harness_tools,
            ),
            "tool_count": len(session.tools_loaded),
            "harness_tools": session.harness_tools,
            "context_chars": len(session.context_block),
            "cluster": session.cluster,
        }

    # ── internals ────────────────────────────────────────────────────────

    def _route(
        self, query: str, project: str, *, ide: str, use_embedding: bool,
    ) -> dict[str, Any]:
        # P4: prefer production cutover (v5) so sessions match HTTP /route
        try:
            from server.router_cutover import route_for_task
            return route_for_task(
                query, project, max_results=MAX_SESSION_TOOLS,
                open_session=False, ide=ide,
            )
        except Exception as exc:
            logger.warning("route_for_task failed: %s — falling back to route_task", exc)
        try:
            from server.router import route_task
            return route_task(
                query, project, use_embedding=use_embedding, ide=ide,
                write_feedback=False,
            )
        except Exception as exc:
            logger.warning("route_task failed: %s", exc)
            return {
                "cluster": "general",
                "task_type": "general",
                "skills": [],
                "model": None,
            }

    def _select_tools(self, route: dict[str, Any], cluster: str, *, max_tools: int) -> list[str]:
        seen: set[str] = set()
        selected: list[str] = []

        def add(name: str) -> None:
            name = (name or "").strip()
            if not name or name in seen:
                return
            seen.add(name)
            selected.append(name)

        for skill in route.get("skills") or []:
            if isinstance(skill, dict):
                add(skill.get("name", ""))
            elif isinstance(skill, str):
                add(skill)
            if len(selected) >= max_tools:
                return selected[:max_tools]

        for name in bundle_for_cluster(cluster):
            add(name)
            if len(selected) >= max_tools:
                break

        if len(selected) < max_tools:
            for names in SERVICE_BUNDLES.values():
                for name in names:
                    add(name)
                    if len(selected) >= max_tools:
                        break
                if len(selected) >= max_tools:
                    break

        return selected[:max_tools]

    def _build_context_block(
        self,
        project: str,
        query: str,
        route: dict[str, Any],
        tools: list[str],
    ) -> str:
        parts: list[str] = [
            f"# NEXUS Task Context — {project}",
            f"Query: {query}",
            f"Cluster: {route.get('cluster', 'general')}",
            f"Task type: {route.get('task_type', 'general')}",
            f"Tools loaded ({len(tools)}): {', '.join(tools)}",
            "",
        ]

        # assemble() seam (decision: context rules concentrate in one module).
        # Inline learnings/resources below migrate into adapters next; today
        # the assembled block (repo-map + AGENTS.md + notes) is prepended.
        try:
            from server.context_assembly import assemble
            asm = assemble(query, project, route.get("task_type", "general"))
            if asm["block"]:
                parts.append(asm["block"])
                parts.append("")
        except Exception:
            pass

        try:
            from server.context_harness.nexus_bridge import search_learnings
            learnings = search_learnings(query, limit=3)
            for row in learnings:
                # Exact project match only — 'default'/blank notes leaked into
                # every project (functional audit 2026-08-17).
                if row.get("project") == project:
                    parts.append(f"- Learning: {row.get('note', '')[:300]}")
        except Exception:
            pass

        try:
            from server.context_harness.nexus_bridge import search_resources
            for row in search_resources(query, max_results=3):
                desc = (row.get("description") or "")[:200]
                parts.append(f"- Resource `{row.get('name')}`: {desc}")
        except Exception:
            pass

        for skill in (route.get("skills") or [])[:3]:
            if isinstance(skill, dict) and skill.get("_summary"):
                parts.append(f"- Research: {skill.get('name')}: {skill['summary'][:200]}")

        try:
            from server.site_registry import research_context_for_project
            ctx = research_context_for_project(project, query)
            if ctx.get("urls"):
                parts.append(f"- Project sites ({project}): {', '.join(ctx['urls'][:4])}")
            if ctx.get("matched_publishers"):
                parts.append(f"- Publisher hints: {', '.join(ctx['matched_publishers'][:4])}")
        except Exception:
            pass

        text = "\n".join(parts)
        return text[:4000]

    def _persist_session(self, session: ToolSession) -> None:
        with db_conn() as conn:
            conn.execute(
                """
                INSERT INTO tool_sessions (
                    session_id, project, query, cluster, task_type,
                    tools_json, context_block, context_hash, ide, status, model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    session.session_id,
                    session.project,
                    session.query,
                    session.cluster,
                    session.task_type,
                    json.dumps(session.tools_loaded),
                    session.context_block,
                    session.context_hash,
                    session.ide,
                    session.model,
                ),
            )

    def _update_persisted_session(self, session: ToolSession) -> None:
        with db_conn() as conn:
            conn.execute(
                """
                UPDATE tool_sessions
                SET status = ?, outcome = ?, summary = ?, ended_at = datetime('now')
                WHERE session_id = ?
                """,
                (session.status, session.outcome, session.summary, session.session_id),
            )

    def _archive_context(self, session: ToolSession) -> None:
        if not session.context_block and not session.summary:
            return
        content = session.context_block
        if session.summary:
            content += f"\n\n## Completion summary\n{session.summary}"
        title = f"{session.query[:80]} ({session.outcome or 'done'})"
        with db_conn() as conn:
            conn.execute(
                """
                INSERT INTO project_contexts (
                    project, context_type, title, content, source_session_id, tags_json
                ) VALUES (?, 'session_archive', ?, ?, ?, ?)
                """,
                (
                    session.project,
                    title,
                    content[:8000],
                    session.session_id,
                    json.dumps([session.cluster, session.task_type]),
                ),
            )

    def _record_learning(self, session: ToolSession, outcome: str) -> None:
        try:
            from server.learning import record_outcome
        except Exception:
            return

        tools = list(session.tools_loaded or [])
        # Gap 3: primary call carries full co_skills bundle for co_triggered edges;
        # remaining tools get counter/Thompson updates only (no re-pair upsert).
        if not tools:
            return
        primary = tools[0]
        rest = tools[1:]
        try:
            record_outcome(
                skill_name=primary,
                outcome=outcome,
                task_summary=session.query[:200],
                project=session.project,
                ide=session.ide,
                tokens_used=0,
                co_skills=rest,
            )
        except Exception as exc:
            logger.debug("record_outcome failed for %s: %s", primary, exc)
        for tool_name in rest:
            try:
                record_outcome(
                    skill_name=tool_name,
                    outcome=outcome,
                    task_summary=session.query[:200],
                    project=session.project,
                    ide=session.ide,
                    tokens_used=0,
                )
            except Exception as exc:
                logger.debug("record_outcome failed for %s: %s", tool_name, exc)

    def _resolve_session(self, session_id: str) -> ToolSession:
        if session_id in self._active:
            return self._active[session_id]
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tool_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"unknown session: {session_id}")
        return ToolSession(
            session_id=row["session_id"],
            project=row["project"],
            query=row["query"],
            cluster=row["cluster"],
            task_type=row["task_type"],
            tools_loaded=json.loads(row["tools_json"] or "[]"),
            context_block=row["context_block"] or "",
            context_hash=row["context_hash"] or "",
            ide=row["ide"],
            status=row["status"],
            outcome=row["outcome"],
            summary=row["summary"] or "",
            model=row["model"],
        )

    @staticmethod
    def _normalize_outcome(outcome: str) -> str:
        o = (outcome or "correct").strip().lower()
        if o in {"correct", "success", "passed", "done", "complete", "completed"}:
            return "correct"
        if o in {"wrong", "failed", "fail", "error"}:
            return "wrong"
        if o in {"partial", "mixed"}:
            return "partial"
        if o in {"skip", "skipped", "cancel", "cancelled"}:
            return "partial"  # DB CHECK has no skip; partial is least-biased close
        return "correct"


_broker: ToolSessionBroker | None = None


def get_tool_session_broker() -> ToolSessionBroker:
    global _broker
    if _broker is None:
        _broker = ToolSessionBroker()
    return _broker