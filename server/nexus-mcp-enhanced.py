#!/usr/bin/env python3
"""
nexus-mcp-enhanced.py — Enhanced NEXUS MCP Server
Includes find_skills, get_skill, report_outcome, audit_routing, list_resources
AND the new orchestrator tools: orchestrate, design_plan, execute_phase, verify_phase, audit_plan
"""
import json, sqlite3, sqlite_vec, sys, time, os, shutil
from pathlib import Path
from typing import List, Dict, Optional
from contextlib import contextmanager

# ── Ensure project root is on sys.path BEFORE any local imports ──
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# ── FastMCP import
from fastmcp import FastMCP

# ── DB — consolidated from server/db.py (single source of truth)
from server.db import get_db_path, get_connection, ensure_routing_log_schema

DB_PATH = get_db_path()

mcp = FastMCP("NEXUS Orchestrator")

# ── DB helper ──────────────────────────────────────────
def get_db():
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
    except Exception as e:
        print(f"[DB_FATAL] get_db connect failed: {e}", file=sys.stderr, flush=True)
        print(f"[DB_FATAL] DB_PATH={DB_PATH} exists={DB_PATH.exists()} stat={DB_PATH.stat() if DB_PATH.exists() else 'N/A'}", file=sys.stderr, flush=True)
        print(f"[DB_FATAL] NEXUS_DB_PATH env={os.environ.get('NEXUS_DB_PATH', 'NOT SET')}", file=sys.stderr, flush=True)
        print(f"[DB_FATAL] W_OK={os.access(DB_PATH, os.W_OK)} R_OK={os.access(DB_PATH, os.R_OK)}", file=sys.stderr, flush=True)
        raise
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_conn():
    """Context manager that always closes the DB connection."""
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()

# ── Router import ──────────────────────────────────────
from server.router_cutover import route_for_task

# ── Learning pipeline import ──
from learning import record_outcome, get_learning_stats, auto_invoke_retry
from broker_runtime import (
    initialize_runtime,
    create_task_run,
    get_task_run as get_broker_task_run,
    review_task_run,
    add_user_learning,
    search_user_learnings,
    _close_routing_outcomes,
)
from server.tool_session_broker import get_tool_session_broker

# ── Existing Tools ─────────────────────────────────────

@mcp.tool()
def find_skills(task: str, project: str = "default", max_results: int = 5) -> str:
    """Route a task to the best model tier and find matching skills.

    Args:
        task: Short description of what you need to do (e.g. 'fix login bug')
        project: Project name (kaari, vihaar, hermes, trading, or default)
        max_results: Maximum number of skills to return

    Returns JSON with tier, recommended model, token limit, and matched skills.

    Phase 7 cutover — route selection by USE_UNIFIED_ROUTER env var:
      =0 → legacy v4 route_task (rollback / A/B baseline)
      =1 → router_v2 unified_route (v2 unifier)
      =2 → unified_route_v5 (production SoT, default after Phase 7)

    Rollback: export USE_UNIFIED_ROUTER=0 and restart MCP.
    """
    # P1: route_for_task opens session with outcome='routed' (no Thompson).
    result = route_for_task(
        task, project, max_results,
        open_session=True,
        ide=os.environ.get("NEXUS_IDE", "claude-code"),
    )

    # Limit results
    result['skills'] = result.get('skills', [])[:max_results]
    return json.dumps(result, indent=2)


# ── Project continuity (unified-MCP U1) — cross-IDE attach/resume/notes ────

@mcp.tool()
def project_attach(project: str, git_root: str = "") -> str:
    """Attach this IDE session to a project. Call once at session start.

    Registers/refreshes the project (last IDE, git root) and returns a resume
    hint — where the project left off. Enables context continuity when you
    switch IDEs: attach in the new IDE, then call project_resume.
    """
    from server.project_continuity import attach
    return json.dumps(
        attach(project, git_root=git_root or None,
               ide=os.environ.get("NEXUS_IDE", "claude-code")),
        indent=2)


@mcp.tool()
def project_resume(project: str, limit: int = 10) -> str:
    """Return the working state of a project — for IDE switches and new sessions.

    Includes recent routed tasks, durable notes, the project's AGENTS.md
    excerpt, and current top beliefs. Call after project_attach.
    """
    from server.project_continuity import resume
    return json.dumps(resume(project, limit=limit), indent=2)


@mcp.tool()
def project_note(project: str, text: str) -> str:
    """Save a durable project note (survives IDE switches).

    Persists to the NEXUS DB and appends to <project>/.nexus/memory.md so
    every IDE (and git) can see it.
    """
    from server.project_continuity import note
    return json.dumps(
        note(project, text, ide=os.environ.get("NEXUS_IDE", "claude-code")),
        indent=2)


@mcp.tool()
def repo_map(git_root: str, budget_tokens: int = 1000) -> str:
    """Aider-style repository map: top symbols ranked by reference-graph
    PageRank, within a token budget. Gives any agent the project's shape
    without reading every file.
    """
    from server.repo_map import build
    return json.dumps(build(git_root, budget_tokens=budget_tokens), indent=2)


# ── NEXUS Forge — project → parallel cluster team in tmux (interactive TUIs) ─

@mcp.tool()
def forge_plan(brief: str, project: str, project_dir: str,
               extra_clusters: str = "") -> str:
    """Plan a project team: lead (analyzer_planner) + core peers (frontend,
    backend, security, devops) + keyword-matched additions. Writes a brief
    per seat to <project_dir>/.nexus/briefs/. Review before spawning.
    """
    from server.forge import plan_project
    extras = [c.strip() for c in extra_clusters.split(",") if c.strip()]
    plan = plan_project(brief, project_dir=project_dir, extra_clusters=extras or None)
    return json.dumps(plan, indent=2)


@mcp.tool()
def forge_spawn(project: str, project_dir: str, brief: str = "",
                extra_clusters: str = "") -> str:
    """Spawn the team as detached OpenCode tmux sessions in project_dir.
    Each seat is told to read its brief file and execute its workstream.
    Pass brief to plan+spawn in one step; omit it to spawn from briefs
    already written by a prior forge_plan.
    """
    from server.forge import plan_project, spawn_team, LEAD_CLUSTER, TERRITORIES
    if brief:
        extras = [c.strip() for c in extra_clusters.split(",") if c.strip()]
        plan = plan_project(brief, project_dir=project_dir, extra_clusters=extras or None)
    else:
        from pathlib import Path as _P
        bdir = _P(project_dir) / ".nexus" / "briefs"
        plan = {"brief": "(from existing briefs)", "seats": [
            {"cluster": f.stem,
             "role": "lead" if f.stem == LEAD_CLUSTER else "peer",
             "territory": TERRITORIES.get(f.stem, "notes/"),
             "brief_path": str(f)}
            for f in sorted(bdir.glob("*.md"))
        ]}
    return json.dumps(spawn_team(plan, project_dir, project=project), indent=2)


@mcp.tool()
def forge_status(project: str) -> str:
    """Per-seat status: alive + last pane lines. The LEAD seat consumes this
    to review peers — the honesty authority for workstream completion.
    """
    from server.forge import team_status
    return json.dumps(team_status(project), indent=2)


@mcp.tool()
def forge_stop(project: str) -> str:
    """Kill ONLY this project's nexus-forge-* tmux sessions."""
    from server.forge import stop_team
    return json.dumps(stop_team(project), indent=2)


@mcp.tool()
def route_ab_compare(task: str, project: str = "default",
                     top_k: int = 10) -> str:
    """A/B comparison: v4 vs v2 vs v5 on one query (no DB writes).

    Offline gate check before/after USE_UNIFIED_ROUTER cutover. Reports
    top-k overlap and top-1 match across all three router paths.
    """
    from server.router_cutover import compare_routers
    return json.dumps(compare_routers(task, project, top_k), indent=2)


@mcp.tool()
def router_status() -> str:
    """Active router flag, production SoT path, and cutover metadata (I-05)."""
    from server.router_cutover import get_router_status
    return json.dumps(get_router_status(), indent=2)


@mcp.tool()
def router_ab_suite(top_k: int = 5) -> str:
    """20-query offline A/B suite — v4 vs v5 top-1 agreement gate."""
    from server.router_cutover import run_ab_suite
    return json.dumps(run_ab_suite(top_k=top_k), indent=2)


@mcp.tool()
def process_status() -> str:
    """NEXUS process health — supervisord, MCP, HTTP daemon, dashboard (I-10)."""
    from server.process_supervisor import get_process_status
    return json.dumps(get_process_status(), indent=2)


@mcp.tool()
def ops_dashboard() -> str:
    """Unified ops snapshot — processes, KG, router, harness, routing metrics."""
    from server.process_supervisor import get_ops_snapshot
    return json.dumps(get_ops_snapshot(), indent=2)


@mcp.tool()
def ops_bootstrap(start_dashboard: bool = True) -> str:
    """Start supervisord + dashboard without restarting IDE-managed MCP."""
    from server.process_supervisor import bootstrap_ops
    return json.dumps(bootstrap_ops(start_dashboard=start_dashboard), indent=2)


@mcp.tool()
def research_synthesis(
    topic: str,
    project: str = "default",
    sources: str = "",
    ingest_top: int = 0,
    persist: bool = False,
) -> str:
    """Multi-source research — arXiv, GitHub, SSRN, local FTS + gap mapping."""
    from server.research_service import run_research_synthesis
    src_list = [s.strip() for s in sources.split(",") if s.strip()] or None
    return json.dumps(
        run_research_synthesis(
            topic,
            sources=src_list,
            project=project,
            ingest_top=ingest_top,
            persist=persist,
        ),
        indent=2,
    )


@mcp.tool()
def research_sources() -> str:
    """List research pipeline sources, publishers, and project sites."""
    from server.research_service import list_research_sources, get_research_status
    return json.dumps({
        "status": get_research_status(),
        "sources": list_research_sources(),
    }, indent=2)


@mcp.tool()
def get_skill(name: str) -> str:
    """Read full skill/topic body by name, including OKF sections when indexed.

    Args:
        name: Exact resource name (from find_skills / wiki_brain_query)
    
    Returns body text plus structured OKF sections when available.
    """
    with db_conn() as conn:
        row = conn.execute("""
            SELECT id, name, description, body_text, resource_type, file_path,
                   cluster, repo_name, times_triggered, times_correct, times_wrong
            FROM resources WHERE name=? AND status='active'
        """, (name,)).fetchone()
        if not row:
            return json.dumps({"error": f"Resource '{name}' not found"})

        sections = conn.execute("""
            SELECT section_order, heading, content, token_count
            FROM resource_sections WHERE resource_id=?
            ORDER BY section_order ASC, id ASC
        """, (row["id"],)).fetchall()

    payload = {
        "name": row["name"],
        "description": row["description"],
        "body": row["body_text"],
        "type": row["resource_type"],
        "cluster": row["cluster"],
        "file_path": row["file_path"],
        "repo": row["repo_name"],
        "okf_sections": [
            {
                "order": s["section_order"],
                "heading": s["heading"],
                "content": s["content"],
                "tokens": s["token_count"],
            }
            for s in sections
        ],
        "stats": {
            "triggered": row["times_triggered"],
            "correct": row["times_correct"],
            "wrong": row["times_wrong"],
            "success_rate": round(row["times_correct"]*100.0/max(row["times_triggered"],1), 1)
        },
    }
    if sections:
        payload["okf_markdown"] = "\n\n".join(
            f"## {s['heading']}\n\n{s['content']}" if s["heading"] else s["content"]
            for s in sections
        )
    return json.dumps(payload, indent=2)


@mcp.tool()
def wiki_brain_query(task: str, top_k: int = 5, include_sections: bool = True) -> str:
    """Query the wiki vault knowledge brain for IDE task assistance.

    Searches OKF-indexed vault topics (system design, agents, security, v3, etc.)
    via hybrid routing, then returns ranked topics with section snippets.

    Args:
        task: Natural-language task or question from the IDE
        top_k: Number of topics to return (1-15)
        include_sections: Include OKF section content in results
    """
    from server.export.okf_export import search_export

    top_k = max(1, min(top_k, 15))
    hits = search_export(task, db_path=str(DB_PATH), top_k=top_k)
    wiki_hits = [h for h in hits if "wiki" in (h.get("file_path") or "")
                 or h.get("cluster") == "knowledge_wiki"]
    results = wiki_hits or hits

    formatted = []
    for h in results[:top_k]:
        entry = {
            "name": h["name"],
            "cluster": h.get("cluster"),
            "score": h.get("score"),
            "description": (h.get("description") or "")[:300],
            "resource_type": h.get("resource_type"),
        }
        if include_sections:
            entry["sections"] = [
                {
                    "heading": s.get("heading"),
                    "content": (s.get("content") or "")[:1200],
                }
                for s in (h.get("sections") or [])[:6]
            ]
        formatted.append(entry)

    return json.dumps({
        "task": task,
        "topics": formatted,
        "count": len(formatted),
        "hint": "Call get_skill(name) for full OKF body on any topic above.",
    }, indent=2)


@mcp.tool()
def audit_routing() -> str:
    """Generate a health report of routing accuracy and degraded skills."""
    with db_conn() as conn:
        high_error = conn.execute("""
            SELECT name, ROUND(times_wrong*100.0/MAX(times_triggered,1),1) as error_rate,
                   times_triggered as uses
            FROM resources
            WHERE times_triggered >= 3 AND resource_type='skill'
            ORDER BY error_rate DESC LIMIT 10
        """).fetchall()
        
        degraded = conn.execute("""
            SELECT name FROM resources WHERE status='degraded'
        """).fetchall()
        
        total_routes = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
        correct_routes = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='correct'").fetchone()[0]
        wrong_routes = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='wrong'").fetchone()[0]
        accuracy = round(correct_routes*100.0/max(total_routes,1), 1)
        
        oc_routes = conn.execute("SELECT COUNT(*) FROM routing_log WHERE ide='opencode'").fetchone()[0]
        cc_routes = conn.execute("SELECT COUNT(*) FROM routing_log WHERE ide='claude-code'").fetchone()[0]
        
        recent = conn.execute("""
            SELECT task_summary, matched_model, outcome, created_at
            FROM routing_log ORDER BY created_at DESC LIMIT 5
        """).fetchall()
    
    report = {
        "overall": {
            "total_routes": total_routes,
            "correct": correct_routes,
            "wrong": wrong_routes,
            "accuracy_pct": accuracy
        },
        "by_ide": {
            "opencode": oc_routes,
            "claude_code": cc_routes
        },
        "high_error_skills": [{"name": r[0], "error_rate": r[1], "uses": r[2]} for r in high_error],
        "degraded_skills": [r[0] for r in degraded],
        "recent_activity": [{"task": r[0], "model": r[1], "outcome": r[2], "at": r[3]} for r in recent]
    }
    
    return json.dumps(report, indent=2)


@mcp.tool()
def list_resources(resource_type: str = "skill", status: str = "active") -> str:
    """Browse indexed resources by type and status.
    
    Args:
        resource_type: skill, agent, command, cli_tool, mcp, hook, design_system, craft, doc, spec or 'all'
        status: active, degraded, broken, archived, or 'all'
    
    Returns JSON list of matching resources.
    """
    with db_conn() as conn:
        query = "SELECT name, resource_type, repo_name, tier, times_triggered, status FROM resources WHERE 1=1"
        params = []
        
        if resource_type != 'all':
            query += " AND resource_type=?"
            params.append(resource_type)
        if status != 'all':
            query += " AND status=?"
            params.append(status)
        
        query += " ORDER BY resource_type, name LIMIT 200"
        
        rows = conn.execute(query, params).fetchall()
    
    return json.dumps([{
        "name": r["name"],
        "type": r["resource_type"],
        "repo": r["repo_name"],
        "tier": r["tier"],
        "uses": r["times_triggered"],
        "status": r["status"]
    } for r in rows], indent=2)


# ── New Orchestrator Tools ───────────────────────────────

sys.path.insert(0, str(Path.home() / 'nexus/server'))
from orchestrator import orchestrate, Phase, Plan

@mcp.tool()
def design_plan(task: str, project: str = "default") -> str:
    """Design a typed execution plan using the adaptive-imagining-cat workflow.
    
    This is the default workflow for complex tasks. It analyzes the current state,
    creates a typed DAG plan, and maps resources to each step.
    
    Args:
        task: High-level task description (e.g. 'Build a trading dashboard')
        project: Project context (kaari, vihaar, hermes, trading, default)
    
    Returns a typed plan with phases, resource mappings, and estimated tokens.
    The plan is stored in the DB and needs explicit approval before execution.
    """
    print(f"[design_plan] {task} / {project}", file=sys.stderr, flush=True)
    
    result = orchestrate(task, project, auto_execute=False)
    
    # Store plan in DB
    try:
        with db_conn() as conn:
            phases_json = json.dumps(result.get("plan", {}).get("phases", []))
            conn.execute("""
                INSERT INTO plans(goal, project, phases_json, status, plan_type)
                VALUES(?,?,?,?,?)
            """, (task, project, phases_json, 'planning', 'adaptive'))
            conn.commit()
            plan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            result["plan_id"] = plan_id
    except Exception as e:
        print(f"[design_plan] DB error: {e}", file=sys.stderr)
        result["plan_id"] = None
    
    return json.dumps(result, indent=2)


@mcp.tool()
def execute_phase(plan_id: int, phase_id: str, force: bool = False) -> str:
    """Execute a specific phase of an existing plan.
    
    Args:
        plan_id: Plan ID from design_plan
        phase_id: Phase ID (e.g. 'phase-3-implement')
        force: Skip verification and execute immediately
    
    Returns execution output with model assignments, skills used, and status.
    """
    print(f"[execute_phase] Plan {plan_id}, Phase {phase_id}", file=sys.stderr, flush=True)
    
    # Retrieve plan
    with db_conn() as conn:
        row = conn.execute("""
            SELECT goal, project, phases_json FROM plans WHERE id=?
        """, (plan_id,)).fetchone()
        
        if not row:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        
        goal = row["goal"]
        project = row["project"]
        phases = json.loads(row["phases_json"])
        
        target = next((p for p in phases if p["id"] == phase_id), None)
        if not target:
            return json.dumps({"error": f"Phase {phase_id} not found in plan {plan_id}"})
        
        task = f"{target['name']}: {target['description']}"
        routing = route_for_task(
            task, project, max_results=5, open_session=False
        )
        
        conn.execute("""
            INSERT INTO execution_log(plan_id, phase_id, task_summary, matched_model, skills_used, outcome, ide)
            VALUES(?,?,?,?,?,?,?)
        """, (plan_id, phase_id, task, routing['model'],
              json.dumps([s['name'] for s in routing.get('skills', [])]),
              None, 'opencode'))
        conn.commit()
    
    return json.dumps({
        "plan_id": plan_id,
        "phase": target,
        "routing": routing,
        "status": "dispatched",
        "message": f"Phase {phase_id} dispatched to {routing['model']} with {len(routing['skills'])} skills"
    }, indent=2)


@mcp.tool()
def verify_phase(plan_id: int, phase_id: str) -> str:
    """Verify a specific phase's output.
    
    Args:
        plan_id: Plan ID
        phase_id: Phase ID
    
    Returns verification results with pass/fail status.
    """
    with db_conn() as conn:
        row = conn.execute("""
            SELECT goal, phases_json FROM plans WHERE id=?
        """, (plan_id,)).fetchone()
        
        if not row:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        
        phases = json.loads(row["phases_json"])
        target = next((p for p in phases if p["id"] == phase_id), None)
        if not target:
            return json.dumps({"error": f"Phase {phase_id} not found"})
        
        # Honest verification: inspect execution_log for real evidence.
        exec_row = conn.execute("""
            SELECT outcome FROM execution_log
            WHERE plan_id=? AND phase_id=? ORDER BY created_at DESC LIMIT 1
        """, (plan_id, phase_id)).fetchone()

        checks = {
            "executed": exec_row is not None,
            "has_outcome": exec_row is not None and bool(exec_row["outcome"]),
            "outcome_terminal": exec_row is not None and exec_row["outcome"] in {
                "correct", "partial", "wrong", "error", "timeout"
            },
            "coverage_80": False,  # requires real test coverage run
        }
        if not checks["executed"]:
            verdict, message = "NOT_EXECUTED", "Phase has not been executed"
        elif not checks["outcome_terminal"]:
            verdict, message = "PENDING", "Phase dispatched but no terminal outcome recorded"
        elif checks["outcome_terminal"] and exec_row["outcome"] in {"correct", "partial"}:
            verdict, message = "PASS", "Terminal outcome indicates success"
        else:
            verdict, message = "FAIL", f"Outcome recorded as {exec_row['outcome']}"

    return json.dumps({
        "plan_id": plan_id,
        "phase_id": phase_id,
        "checks": checks,
        "verdict": verdict,
        "message": message
    }, indent=2)


@mcp.tool()
def audit_plan(plan_id: int) -> str:
    """Run the full audit on a completed plan.
    
    Args:
        plan_id: Plan ID
    
    Returns comprehensive audit report with token usage, accuracy, and learnings.
    """
    with db_conn() as conn:
        row = conn.execute("""
            SELECT goal, project, phases_json, status, created_at FROM plans WHERE id=?
        """, (plan_id,)).fetchone()
        
        if not row:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        
        goal = row["goal"]
        phases = json.loads(row["phases_json"])
        
        execs = conn.execute("""
            SELECT phase_id, task_summary, matched_model, outcome, tokens_used, created_at
            FROM execution_log WHERE plan_id=? ORDER BY phase_id
        """, (plan_id,)).fetchall()
        
        total_routes = conn.execute("""
            SELECT COUNT(*) FROM routing_log WHERE project=?
        """, (row["project"],)).fetchone()[0]
        
        conn.execute("UPDATE plans SET status='completed', updated_at=datetime('now') WHERE id=?", (plan_id,))
        conn.commit()
    
    report = {
        "goal": goal,
        "project": row["project"],
        "status": "completed",
        "phases_total": len(phases),
        "phases_executed": len(execs),
        "total_routes_for_project": total_routes,
        "execution_summary": [{
            "phase": e[0],
            "model": e[2],
            "outcome": e[3],
            "tokens": e[4],
            "at": e[5]
        } for e in execs],
        "learnings": [
            "Document all architectural decisions in ADR-XXX",
            "Use find_skills() before committing to a path",
            "Verify layout changes with actual screenshot"
        ]
    }
    
    return json.dumps(report, indent=2)


@mcp.tool()
def task_start(
    query: str,
    project: str = "default",
    ide: str = "claude-code",
    max_tools: int = 8,
    ide_session_id: str = "",
) -> str:
    """Start an ephemeral tool session — load only task-relevant tools into the IDE surface.

    NEXUS routes the query, selects up to max_tools specialized resources, and
    returns a context block. Call task_complete when done to dump tools and learn.
    """
    initialize_runtime()
    broker = get_tool_session_broker()
    session = broker.start_session(
        project=project,
        query=query,
        ide=ide,
        use_embedding=False,
        max_tools=min(max(1, max_tools), 8),
        ide_session_id=ide_session_id or None,
    )
    payload = session.to_dict()
    payload["recommendations_are_advisory"] = True
    payload["mcp_proxy"] = broker.get_mcp_proxy_config(session.session_id)
    payload["instructions"] = (
        "Recommendations in visible_tools are advisory. "
        "You can search and retrieve any capability in the catalog using capability_search, capability_get, and skill_read at any time. "
        "Call task_complete(session_id, outcome, summary) when finished."
    )
    return json.dumps(payload, indent=2)


@mcp.tool()
def task_complete(
    session_id: str,
    outcome: str = "correct",
    summary: str = "",
    ide_session_id: str = "",
) -> str:
    """End an ephemeral tool session — dump tools from IDE surface and record learning."""
    initialize_runtime()
    # P4: prefer execution_plane report (belief_delta)
    try:
        from server.execution_plane import report_session
        result = report_session(
            session_id,
            outcome,
            notes=summary,
            ide_session_id=ide_session_id or None,
        )
        return json.dumps(result, indent=2)
    except Exception:
        broker = get_tool_session_broker()
        result = broker.end_session(
            session_id, outcome=outcome, summary=summary,
            ide_session_id=ide_session_id or None,
        )
        return json.dumps(result, indent=2)


@mcp.tool()
def nexus_session_open(
    task: str,
    project: str = "default",
    budget: int = 8,
    ide: str = "claude-code",
) -> str:
    """P4 execution-plane session open (stable contract alias)."""
    initialize_runtime()
    from server.execution_plane import open_session
    return json.dumps(
        open_session(task, project, budget=budget, ide=ide),
        indent=2,
    )


@mcp.tool()
def nexus_session_report(
    session_id: str,
    outcome: str,
    notes: str = "",
    quality: float = 1.0,
    skill_name: str = "",
) -> str:
    """P4 execution-plane session report with belief_delta."""
    initialize_runtime()
    from server.execution_plane import report_session
    return json.dumps(
        report_session(
            session_id,
            outcome,
            notes=notes,
            quality=quality,
            skill_name=skill_name or None,
        ),
        indent=2,
    )


# ── Shared Capability Service Client Bridge (Phase 4) ───────────────────
def _capability_identity(harness_id: str = "") -> tuple[str, str]:
    """Return one consistent transport identity for this MCP client."""
    configured = os.environ.get("NEXUS_IDE", "codex").strip().lower() or "codex"
    harness = (harness_id or configured).strip().lower()
    if harness != configured:
        raise ValueError(f"harness_id {harness!r} does not match MCP identity {configured!r}")
    return f"nexus-{configured}", configured


def _capability_error(status: str, detail: str) -> dict:
    return {"error": True, "status": status, "detail": detail}


def _capability_http(method: str, path: str, body: dict = None, timeout: float = 5.0) -> dict:
    import urllib.request, urllib.error
    daemon_url = os.environ.get("NEXUS_DAEMON_URL", "http://127.0.0.1:8000")
    url = f"{daemon_url}/capabilities/v1{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    principal_id, harness_id = _capability_identity()
    headers = {"X-Principal-Id": principal_id, "X-Harness-Id": harness_id}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            return {"error": True, "status": e.code, "detail": json.loads(err_body)}
        except Exception:
            return {"error": True, "status": e.code, "detail": err_body}
    except Exception as e:
        return {"error": True, "status": "NETWORK_ERROR", "detail": str(e)}


@mcp.tool()
def capability_search(query: str = "", limit: int = 20, cursor: str = "") -> str:
    """Search or list canonical capabilities from the Shared Capability Service.

    Advisory and exhaustive: search does not restrict which capabilities can be invoked.
    """
    import urllib.parse
    params = []
    if query:
        params.append(f"query={urllib.parse.quote(query)}")
    if limit:
        params.append(f"limit={min(max(1, limit), 100)}")
    if cursor:
        params.append(f"cursor={urllib.parse.quote(cursor)}")
    qs = ("?" + "&".join(params)) if params else ""
    res = _capability_http("GET", f"/catalog{qs}")
    return json.dumps(res, indent=2)


@mcp.tool()
def capability_get(capability_id: str, version: str = "") -> str:
    """Retrieve full descriptor and metadata for a capability by its canonical ID."""
    import urllib.parse
    path = f"/catalog/{urllib.parse.quote(capability_id, safe='')}"
    if version:
        path += f"?version={urllib.parse.quote(version)}"
    res = _capability_http("GET", path)
    return json.dumps(res, indent=2)


@mcp.tool()
def skill_read(capability_id: str, version: str = "", path: str = "SKILL.md") -> str:
    """Read full verified skill markdown or bundled asset from the canonical catalog."""
    import urllib.parse
    encoded_id = urllib.parse.quote(capability_id, safe='')
    selected_version = version
    if not selected_version:
        descriptor_result = _capability_http("GET", f"/catalog/{encoded_id}")
        descriptor = descriptor_result.get("descriptor") if isinstance(descriptor_result, dict) else None
        if not descriptor or descriptor_result.get("error"):
            return json.dumps(descriptor_result, indent=2)
        selected_version = descriptor.get("version", "")
        if not selected_version:
            return json.dumps(_capability_error("INVALID_DESCRIPTOR", "Capability descriptor has no version"), indent=2)
    api_path = f"/skills/{encoded_id}/content"
    params = []
    params.append(f"version={urllib.parse.quote(selected_version)}")
    params.append(f"path={urllib.parse.quote(path or 'SKILL.md')}")
    if params:
        api_path += "?" + "&".join(params)
    res = _capability_http("GET", api_path)
    return json.dumps(res, indent=2)


@mcp.tool()
def capability_recommend(session_id: str, query: str, k: int = 5, seed: int = None) -> str:
    """Request advisory capability recommendations for a session and query.

    Recommendations are advisory rankings and do not filter or restrict catalog access.
    """
    body = {"session_id": session_id, "query": query, "k": min(max(1, k), 50)}
    if seed is not None:
        body["seed"] = seed
    res = _capability_http("POST", "/recommend", body=body)
    return json.dumps(res, indent=2)


@mcp.tool()
def capability_session_open(
    project: str = "default",
    harness_id: str = "",
    session_id: str = "",
    context: dict = None,
    ttl_seconds: int = 3600,
) -> str:
    """Open a capability-service session bound to this MCP client's identity."""
    try:
        _, resolved_harness = _capability_identity(harness_id)
    except ValueError as e:
        return json.dumps(_capability_error("INVALID_ARGUMENT", str(e)), indent=2)
    body = {
        "harness_id": resolved_harness,
        "project": project,
        "context": context or {},
        "ttl_seconds": ttl_seconds,
    }
    if session_id:
        body["session_id"] = session_id
    res = _capability_http("POST", "/sessions", body=body)
    return json.dumps(res, indent=2)


@mcp.tool()
def capability_invoke(
    capability_id: str,
    method: str = "",
    args: dict = None,
    session_id: str = "",
    version: str = "",
    idempotency_key: str = "",
) -> str:
    """Invoke a tool capability using a required session and stable idempotency key."""
    if not session_id:
        return json.dumps(_capability_error("INVALID_ARGUMENT", "session_id is required"), indent=2)
    if not idempotency_key:
        return json.dumps(_capability_error("INVALID_ARGUMENT", "idempotency_key is required"), indent=2)
    import urllib.parse
    encoded_id = urllib.parse.quote(capability_id, safe='')
    selected_version = version
    if not selected_version:
        descriptor_result = _capability_http("GET", f"/catalog/{encoded_id}")
        descriptor = descriptor_result.get("descriptor") if isinstance(descriptor_result, dict) else None
        if not descriptor or descriptor_result.get("error"):
            return json.dumps(descriptor_result, indent=2)
        selected_version = descriptor.get("version", "")
        if not selected_version:
            return json.dumps(_capability_error("INVALID_DESCRIPTOR", "Capability descriptor has no version"), indent=2)
    if method:
        descriptor_result = _capability_http(
            "GET", f"/catalog/{encoded_id}?version={urllib.parse.quote(selected_version)}")
        descriptor = descriptor_result.get("descriptor") if isinstance(descriptor_result, dict) else None
        if not descriptor or descriptor_result.get("error"):
            return json.dumps(descriptor_result, indent=2)
        descriptor_method = descriptor.get("method")
        if not descriptor_method or method != descriptor_method:
            return json.dumps(_capability_error(
                "INVALID_ARGUMENT", "method does not match the selected capability descriptor"), indent=2)
    body = {
        "capability_id": capability_id,
        "version": selected_version,
        "session_id": session_id,
        "arguments": args or {},
        "idempotency_key": idempotency_key,
    }
    res = _capability_http("POST", "/invoke", body=body)
    return json.dumps(res, indent=2)


@mcp.tool()
def capability_session_close(
    session_id: str,
    outcome: str = "correct",
    evidence: dict = None,
    notes: str = "",
) -> str:
    """Close a shared capability session and record its outcome and notes."""
    import urllib.parse
    if evidence:
        evidence_note = "Legacy evidence payload (not independently verified): " + json.dumps(evidence, sort_keys=True)
        notes = f"{notes}\n{evidence_note}".strip()
    body = {"outcome": outcome, "notes": notes}
    res = _capability_http("POST", f"/sessions/{urllib.parse.quote(session_id, safe='')}/close", body=body)
    return json.dumps(res, indent=2)


@mcp.tool()
def harness_dashboard(project: str = "") -> str:
    """Harness Engine dashboard — FTSR, MRC, TLT, CDR per project + active IDE sessions."""
    from server.harness_metrics import build_harness_dashboard
    return json.dumps(build_harness_dashboard(project or None), indent=2)


@mcp.tool()
def task_status(session_id: str) -> str:
    """Inspect an active or completed tool session."""
    initialize_runtime()
    broker = get_tool_session_broker()
    session = broker.get_session(session_id)
    if session is None:
        return json.dumps({"error": f"unknown session: {session_id}"})
    data = session.to_dict()
    data["mcp_proxy"] = broker.get_mcp_proxy_config(session_id)
    return json.dumps(data, indent=2)


@mcp.tool()
def autopilot_run(task: str, project: str = "default", ide_name: str = "claude-code",
                  mode: str = "safemode", auto_execute: bool = False) -> str:
    """Run a task through the adaptive-imagining-cat broker/autopilot layer."""
    initialize_runtime()
    result = create_task_run(
        task,
        ide_name=ide_name,
        mode=mode,
        project=project,
        cwd=str(Path.cwd()),
        repo_context={"transport": "mcp"},
        auto_execute=auto_execute,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def get_task_run(task_run_id: str) -> str:
    """Read a broker task run, including preview, traces, and final report."""
    initialize_runtime()
    return json.dumps(get_broker_task_run(task_run_id), indent=2)


@mcp.tool()
def review_task(task_run_id: str, verdict: str, comment: str = "") -> str:
    """Attach a review to a broker task run and feed it into learning."""
    initialize_runtime()
    return json.dumps(review_task_run(task_run_id, verdict, comment), indent=2)


@mcp.tool()
def add_learning_note(note: str, project: str = "default", task_type: str = "general",
                      bundles: str = "[]", tools: str = "[]") -> str:
    """Persist a user-authored learning note into Nexus memory."""
    initialize_runtime()
    try:
        bundle_list = json.loads(bundles) if isinstance(bundles, str) else bundles
        tool_list = json.loads(tools) if isinstance(tools, str) else tools
    except Exception:
        bundle_list = []
        tool_list = []
    result = add_user_learning(
        note,
        scope="task_type",
        task_type=task_type,
        project=project,
        bundles=bundle_list or [],
        tools=tool_list or [],
        created_by="mcp-user",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def search_learnings(query: str = "", project: str = "default", limit: int = 20) -> str:
    """Search project learnings — user notes, distilled patterns, session archives."""
    initialize_runtime()
    from server.rl_feedback_loop import search_project_learnings
    return json.dumps(search_project_learnings(query=query, project=project, limit=limit), indent=2)


@mcp.tool()
def distill_project_learnings(project: str = "default", days: int = 7) -> str:
    """Distill recent project session archives into searchable learning notes."""
    initialize_runtime()
    from server.rl_feedback_loop import distill_project_learnings as _distill
    return json.dumps(_distill(project, days=days), indent=2)


@mcp.tool()
def feedback_coverage() -> str:
    """RL loop coverage stats — routing outcomes, EFC, tool session success rate."""
    initialize_runtime()
    from server.rl_feedback_loop import get_feedback_coverage
    return json.dumps(get_feedback_coverage(), indent=2)


@mcp.tool()
def kg_status() -> str:
    """Knowledge graph health — edge count, zero-edge resources, community stats (I-04)."""
    initialize_runtime()
    from server.kg_service import get_kg_status
    return json.dumps(get_kg_status(), indent=2)


@mcp.tool()
def kg_rebuild(
    dry_run: bool = False,
    restore_backup: bool = True,
    write_communities: bool = True,
    threshold: float = 0.65,
) -> str:
    """Rebuild collapsed KG — restore backup edges + semantic densification."""
    initialize_runtime()
    from server.kg_service import rebuild_kg
    return json.dumps(
        rebuild_kg(
            dry_run=dry_run,
            restore_backup=restore_backup,
            write_communities=write_communities,
            threshold=threshold,
        ),
        indent=2,
    )


@mcp.tool()
def ide_event(
    event_type: str,
    project: str = "default",
    skill_name: str = "",
    query: str = "",
    tool_session_id: str = "",
    ide_session_id: str = "",
    error: str = "",
) -> str:
    """Post an IDE event — auto task_complete on success, reflection on test_failed."""
    initialize_runtime()
    from server.rl_feedback_loop import handle_ide_event
    result = handle_ide_event(
        event_type,
        ide="claude-code",
        project=project,
        metadata={
            "project": project,
            "skill_name": skill_name,
            "query": query,
            "tool_session_id": tool_session_id,
            "ide_session_id": ide_session_id,
            "error": error,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def sync_mcp_tools(server_name: str, tools_json: str = None) -> str:
    """NEXUS v2: Dynamic MCP tool synchronization.
    
    Based on ScaleMCP CRUD pipeline.
    
    Args:
        server_name: Name of MCP server
        tools_json: JSON array of tools (optional - uses polling if not provided)
    
    Returns:
        JSON with sync results
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from mcp_sync import MCPSyncManager, MCPTool
    
    sync = MCPSyncManager()
    
    try:
        if tools_json:
            import json
            tools_data = json.loads(tools_json)
            tools = [MCPTool(**t) for t in tools_data]
            results = sync.sync_from_mcp_server(server_name, tools)
        else:
            # Just return sync status
            results = sync.get_sync_status()
        
        return json.dumps({
            'server': server_name,
            'results': results,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ')
        }, indent=2)
        
    finally:
        sync.close()


# ── v3 Tool Attention ──────────────────────────────────────────
@mcp.tool()
def tool_attention_route(task: str, project: str = "default") -> str:
    """NEXUS v3: Tool Attention routing via ISO embeddings + FAISS search.
    
    Phase 5+ pipeline: semantic search → candidate scoring → tier selection.
    Falls back to keyword matching if FAISS unavailable.
    
    Args:
        task: Task description to route
        project: Project context
    
    Returns:
        JSON with tier, model, candidate resources with scores
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    try:
        from tool_attention import route_v3
        result = route_v3(task, project)
        return json.dumps(result, indent=2)
    except Exception as e:
        result = route_for_task(
            task, project, max_results=5,
            open_session=True,
            ide=os.environ.get("NEXUS_IDE", "claude-code"),
        )
        result["fallback"] = f"Tool Attention unavailable: {e}"
        return json.dumps(result, indent=2)


@mcp.tool()
def universal_route(task: str, project: str = "default", 
                    resource_types: str = None) -> str:
    """NEXUS v3: Universal cross-type routing for ALL resource types.
    
    Searches skills + commands + agents + hooks + design_systems + docs.
    Use when you need the best resource regardless of type.
    
    Args:
        task: Task description
        project: Project context
        resource_types: Comma-separated types to include (optional, default: all)
    
    Returns:
        JSON with universal candidates across all types
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    try:
        from tool_attention import route_universal
        types = resource_types.split(',') if resource_types else None
        result = route_universal(task, project, types=types)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "fallback": "universal_route unavailable"})


@mcp.tool()
def check_preconditions(tool_name: str) -> str:
    """NEXUS v3: Check if a tool's state preconditions are met.
    
    Blocks execution when state requirements aren't satisfied.
    
    Args:
        tool_name: Name of tool to check (e.g. 'query_db', 'mcp_invoke', 'browser_action')
    
    Returns:
        JSON with allowed status and block message if applicable
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from precondition import can_invoke, GLOBAL_GATES
    allowed, message = can_invoke(tool_name)
    return json.dumps({
        "tool": tool_name,
        "allowed": allowed,
        "block_message": message,
        "state_snapshot": GLOBAL_GATES.snapshot()
    }, indent=2)


@mcp.tool()
def update_state(state_json: str) -> str:
    """NEXUS v3: Update global state gates for precondition checking.
    
    Call this after detecting environment changes (DB connected, MCP started, etc.)
    
    Args:
        state_json: JSON string of state variables (e.g. '{"has_db": true, "has_mcp": true}')
    
    Returns:
        JSON with updated state snapshot
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from precondition import GLOBAL_GATES, StateVariable
    state = json.loads(state_json)
    for k, v in state.items():
        try:
            sv = StateVariable(k)
            GLOBAL_GATES.set(sv, v)
        except ValueError:
            pass
    return json.dumps({"updated": True, "state": GLOBAL_GATES.snapshot()}, indent=2)


# ── v4+ Cluster Routing ──────────────────────────────────────────

@mcp.tool()
def cluster_route(task: str, project: str = "default", max_clusters: int = 2) -> str:
    """NEXUS v4+Cluster: Route a task through domain clusters first, then search within.

    This reduces the search space from 1097 to ~150-200 resources per query.
    Activates 1-2 domain clusters based on task intent, then runs v4 ISO
    scoring only within activated clusters.

    Cluster mapping:
      analyzer_planner → /investigate, /plan-eng-review
      architect → /autopilot (adaptive-imagining-cat)
      frontend_ui → /design-html
      backend_api → /backend-patterns
      devops_infra → /ship
      quality_security → /qa
      knowledge_wiki → /learn
      general → adaptive-imagining-cat

    Args:
        task: Task description to route
        project: Project context
        max_clusters: Maximum clusters to activate (default 2)

    Returns JSON with activated clusters, affinity scores, and candidate resources.
    """
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    try:
        from cluster_router import ClusterRouter
        router = ClusterRouter(str(DB_PATH))
        result = router.route_to_clusters(task, max_clusters=max_clusters, project=project)

        # Also get auto-trigger recommendations
        from auto_trigger import AutoTrigger
        trigger = AutoTrigger()
        signal_result = trigger.route_task(task, max_clusters=max_clusters)

        return json.dumps({
            "query": task,
            "activated_clusters": result["activated_clusters"],
            "total_resources_scanned": result["total_resources_scanned"],
            "fallback": result["fallback"],
            "latency_ms": result["latency_ms"],
            "auto_trigger": {
                "default_skills": signal_result.get("default_skills", []),
                "default_agents": signal_result.get("default_agents", []),
            },
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "fallback": "cluster_route unavailable"})


@mcp.tool()
def cluster_activate(clusters: str, project: str = "default") -> str:
    """NEXUS v4+Cluster: Manually activate specific clusters by name.

    Use when you know exactly which domain you need. Comma-separated names.

    Valid clusters: analyzer_planner, architect, frontend_ui, backend_api,
                    devops_infra, quality_security, knowledge_wiki, general

    Args:
        clusters: Comma-separated cluster names (e.g. 'backend_api,quality_security')
        project: Project context

    Returns JSON with cluster details, resource counts, and default resources.
    """
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    try:
        from auto_trigger import AutoTrigger
        trigger = AutoTrigger()
        cluster_names = [c.strip() for c in clusters.split(",")]
        activation = trigger.activate_clusters(cluster_names)

        from cluster_router import ClusterRouter
        router = ClusterRouter(str(DB_PATH))
        resource_ids = router._get_cluster_faiss().get_cluster_resource_ids(cluster_names)
        stats = router._get_cluster_faiss().get_cluster_stats()

        return json.dumps({
            "clusters": activation["clusters"],
            "total_resources": len(resource_ids),
            "resource_counts": {c: stats.get(c, 0) for c in cluster_names},
            "default_skills": activation["default_skills"],
            "default_agents": activation["default_agents"],
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "fallback": "cluster_activate unavailable"})


@mcp.tool()
def cluster_list() -> str:
    """NEXUS v4+Cluster: List all domain clusters with resource counts.

    Returns JSON array of all clusters with:
    - name, display_name, description
    - trigger keywords
    - default_skill, default_agents
    - resource_count (number of resources in each cluster)
    - has_centroid (whether centroid embedding is computed)
    - auto_activate_threshold

    Each cluster groups resources by domain across all types
    (skills, agents, commands, MCP tools, etc.).
    """
    try:
        sys.path.insert(0, str(Path.home() / 'nexus/server'))
        from cluster_config import CLUSTER_DEFINITIONS, DEFAULT_SKILL_MAP, DEFAULT_AGENT_MAP
        from auto_trigger import TRIGGER_PATTERNS

        with db_conn() as conn:
            # Pre-fetch centroid status for all clusters
            centroid_rows = conn.execute(
                "SELECT name, CASE WHEN centroid_embedding IS NOT NULL THEN 1 ELSE 0 END as has_centroid FROM cluster_definitions"
            ).fetchall()
            centroid_map = {row["name"]: bool(row["has_centroid"]) for row in centroid_rows}

            clusters = []
            for name, definition in CLUSTER_DEFINITIONS.items():
                count_row = conn.execute(
                    "SELECT COUNT(*) FROM resources WHERE cluster=? AND status='active'",
                    (name,)
                ).fetchone()
                resource_count = count_row[0] if count_row else 0

                signals = TRIGGER_PATTERNS.get(name, {}).get("signals", [])
                clusters.append({
                    "name": name,
                    "display_name": definition.get("display_name", name),
                    "description": definition.get("description", ""),
                    "trigger_keywords": signals[:10],
                    "default_skill": DEFAULT_SKILL_MAP.get(name, "adaptive-imagining-cat"),
                    "default_agents": DEFAULT_AGENT_MAP.get(name, []),
                    "resource_count": resource_count,
                    "has_centroid": centroid_map.get(name, False),
                    "auto_activate_threshold": 0.3,
                })

        return json.dumps({
            "clusters": clusters,
            "total_clusters": len(clusters),
            "total_resources": sum(c["resource_count"] for c in clusters),
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ'),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "fallback": "cluster_list unavailable"})


# ── Health check ────────────────────────────────────────────────

@mcp.tool()
def nexus_health() -> str:
    """Return a quick health check of the NEXUS system."""
    with db_conn() as conn:
        resources = conn.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type").fetchall()
        logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
        degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
        plans = conn.execute("SELECT status, COUNT(*) FROM plans GROUP BY status").fetchall()
    from server.kg_service import get_kg_status
    from server.process_supervisor import get_process_status
    kg = get_kg_status()
    proc_status = get_process_status()

    # Compute true fail-closed status (O-1 fix)
    overall_status = "healthy"
    if not proc_status.get("gate_pass"):
        overall_status = "unhealthy" if proc_status.get("required_up", 0) == 0 else "degraded"
    elif degraded > 0:
        overall_status = "degraded"

    return json.dumps({
        "status": overall_status,
        "gate_pass": proc_status.get("gate_pass", False),
        "resources": {r[0]: r[1] for r in resources},
        "routing_logs_total": logs,
        "degraded_skills": degraded,
        "plans": {p[0]: p[1] for p in plans},
        "knowledge_graph": kg,
        "processes": proc_status,
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ')
    }, indent=2)


# ── Nexus Health Orchestrator (analyzer_planner default) ──────────

sys.path.insert(0, str(Path.home() / 'nexus/server'))
from nexus_health_orchestrator import NHO as NexusHealth

@mcp.tool()
def nexus_health_orchestrate(task: str = None, auto_repair: bool = True) -> str:
    """NEXUS analyzer_planner orchestrator: Diagnose and repair all resource health issues.
    
    Uses adaptive-imagining-cat 6-phase loop:
      0. Context capture (pre-fix metrics)
      1. Cluster mapping diagnosis (all 8 clusters)
      2. Route task to best cluster
      3. Auto-repair (empty bodies, stale resources, zero tokens, low cluster confidence)
      4. Post-fix metrics capture
      5. Verification gates
      6. Audit + learnings
    
    Args:
        task: Optional task description (default: 'health check and repair')
        auto_repair: Whether to execute repairs (default: True)
    
    Returns JSON with complete orchestration record.
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from nexus_health_orchestrator import orchestrate as _orchestrate
    return _orchestrate(task, auto_repair)


@mcp.tool()
def nexus_health_check() -> str:
    """NEXUS analyzer_planner: Quick health snapshot without mutations.
    
    Returns current metrics: total resources, by status, by type,
    empty bodies, zero tokens, cluster confidence distribution.
    Does NOT repair anything — read-only.
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from nexus_health_orchestrator import health_check as _hc
    return _hc()


@mcp.tool()
def nexus_route_to_cluster(task: str) -> str:
    """NEXUS analyzer_planner: Route a task to the best cluster using adaptive-imagining-cat.
    
    Scans all 8 clusters, finds the best match via signal detection + ISO routing,
    returns the cluster name, affinity, and matched resources.
    
    Args:
        task: Task description to route
    
    Returns JSON with selected cluster, resources, and mapping.
    """
    import sys
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from nexus_health_orchestrator import route_to_best_cluster as _route
    return _route(task)


@mcp.tool()
def close_outcomes(project: str = "default", max_rows: int = 500) -> str:
    """Close the feedback loop for routing_log entries that never got outcomes.

    Scans routing_log for entries with outcome=NULL or outcome='routed' that
    have a skill_name set. These represent successful skill invocations where
    the IDE used the routed skill but no outcome was recorded.

    This is the KEY fix for the 55% → 80%+ outcome coverage gap.

    Closures are quality-weighted (quality=0.3): auto-closed rows are derived
    verdicts, not explicit human judgements, so they move Thompson beliefs and
    degradation counters less than a confident 'correct'.

    Args:
        project: Filter by project (default: 'default', use 'all' for all projects)
        max_rows: Maximum rows to close in one batch (default 500)

    Returns JSON with closed count, breakdown by previous outcome, and any errors.
    """
    initialize_runtime()
    conn = get_db()
    try:
        result = _close_routing_outcomes(conn, project=project, max_rows=max_rows)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def learning_stats() -> str:
    """Get full learning pipeline health report.
    
    Shows status of all 5 learning layers, outcome tracking accuracy,
    Thompson Sampling beliefs, self-reflection failures, cluster affinities,
    and resource coverage.
    
    Returns comprehensive JSON stats.
    """
    import json
    return json.dumps(get_learning_stats(), indent=2)


@mcp.tool()
def report_outcome(skill_name: str, outcome: str, task_summary: str = "",
                   project: str = "default", tokens_used: int = 0) -> str:
    """Report whether a skill/tool recommendation was correct or wrong.
    
    Primary learning feedback loop (replaces legacy log_outcome).
    
    Every find_skills call MUST eventually report back via this tool. Without it,
    NEXUS cannot learn:
    - Which resources succeed → Thompson Sampling gets stronger beliefs
    - Which resources fail → Self-Reflection finds alternatives + degrades them
    - Which clusters are useful → Cluster affinity weights adjust
    
    Args:
        skill_name: Name of the skill/resource that was used
        outcome: 'correct' or 'wrong'
        task_summary: What you were trying to accomplish
        project: Project context
    
    Returns learning pipeline execution summary.
    """
    result = record_outcome(skill_name, outcome, task_summary, project,
                           "mcp", tokens_used)
    return json.dumps(result, indent=2)


@mcp.tool()
def find_alternatives_on_failure(failed_skill: str, task: str, 
                                  project: str = "default") -> str:
    """When a skill fails, find better alternatives with retry logic.
    
    Combines Layers 3+4: Self-Reflection to diagnose the failure,
    then Thompson Sampling to find the best alternative.
    
    Args:
        failed_skill: Name of the skill that failed
        task: Original task description
        project: Project context
    
    Returns recommended alternative with scores.
    """
    import json
    conn = get_db()
    try:
        result = auto_invoke_retry(conn, task, failed_skill, project)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def get_cluster_affinities() -> str:
    """Get learned cluster affinity scores (0.0-1.0) based on outcome history.
    
    Higher affinity = this cluster's resources are more often correct.
    Scores update automatically after each report_outcome() call.
    
    Returns dict of cluster_name → affinity score.
    """
    import json
    from learning import get_cluster_affinity_scores
    conn = get_db()
    try:
        scores = get_cluster_affinity_scores(conn)
        return json.dumps({"cluster_affinities": scores, 
                          "interpretation": "Higher = more reliable. Auto-learned from outcomes."},
                          indent=2)
    finally:
        conn.close()


# ── Ruflo Proxy Tools ────────────────────────────────

# Ruflo tool names that ruflo_invoke refuses before dispatch (fnmatch patterns).
# Fail closed: a match here wins even if the name is otherwise valid.
RUFLO_DENIED_TOOL_PATTERNS = (
    "terminal_*",
    "browser_eval",
    "config_set",
    "system_reset",
)


def _ruflo_tool_denied(tool_name: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatchcase(tool_name, pat) for pat in RUFLO_DENIED_TOOL_PATTERNS)


@mcp.tool()
async def ruflo_invoke(tool_name: str, arguments_json: str = "{}") -> str:
    """Call a ruflo MCP tool through NEXUS proxy.

    NEXUS manages a ruflo subprocess and forwards tool calls.
    Use this to invoke any of ruflo's 261 tools (agent, swarm, memory,
    hooks, neural, etc.) without connecting ruflo as a separate MCP server.

    Each invocation is protected by a per-tool circuit breaker and a
    5-second timeout so a hung ruflo call cannot block all routing.

    Args:
        tool_name: Name of the ruflo tool (e.g. 'agent_spawn', 'memory_search', 'swarm_init')
        arguments_json: JSON string of tool arguments

    Returns:
        JSON with ruflo_tool, result text, and isError flag
    """
    import json
    from server.integrations.ruflo_async_proxy import acall_ruflo_tool
    if _ruflo_tool_denied(tool_name):
        return json.dumps({"ruflo_tool": tool_name, "isError": True,
                           "error": "tool denied by NEXUS ruflo policy"})
    arguments = json.loads(arguments_json) if arguments_json else {}
    return await acall_ruflo_tool(tool_name, arguments)


@mcp.tool()
def ruflo_list_tools() -> str:
    """List all 261 ruflo MCP tools available through the proxy.

    Returns JSON array with tool name, description, and input schema.
    """
    import json
    from ruflo_proxy import get_ruflo_tools
    tools = get_ruflo_tools()
    summary = []
    for t in tools:
        schema = t.get("inputSchema", {})
        props = list(schema.get("properties", {}).keys()) if schema else []
        summary.append({
            "name": t["name"],
            "description": t.get("description", "")[:120],
            "arguments": props,
        })
    return json.dumps({"total": len(summary), "tools": summary}, indent=2)


@mcp.tool()
def ruflo_proxy_status() -> str:
    """Check the ruflo proxy connection status.

    Returns whether the proxy is initialized, tool count, and health.
    """
    import json
    try:
        from ruflo_proxy import get_proxy
        proxy = get_proxy()
        tools = proxy.get_tools()
        return json.dumps({
            "status": "connected",
            "initialized": proxy._initialized,
            "tool_count": len(tools),
            "proxy_type": "subprocess (ruflo mcp start)",
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
        }, indent=2)


# ── Coach MCP Tools ──────────────────────────────────

@mcp.tool()
def coach_query(
    query: str = "",
    project: str = "default",
    include_scores: bool = True,
    include_antipatterns: bool = True,
    include_sessions: bool = False,
) -> str:
    """Aggregated coaching dashboard: practice scores, anti-patterns, and recent sessions.

    Args:
        query: Optional natural-language query for context
        project: Project name to scope results
        include_scores: Whether to include practice scores
        include_antipatterns: Whether to include anti-pattern detections
        include_sessions: Whether to include recent session summaries
    """
    from server.coach.mcp_handlers import handle_coach_query
    return handle_coach_query(query, project, include_scores, include_antipatterns, include_sessions)


@mcp.tool()
def coach_antipatterns(
    rule_id: str = "",
    group: str = "",
    severity: str = "",
    since_date: str = "",
) -> str:
    """Filter and return anti-pattern detections from Coach analytics.

    Args:
        rule_id: Filter by specific rule (e.g. 'lazy-prompting')
        group: Filter by practice group (prompt-quality, session-hygiene, code-review, tool-mastery, context-management)
        severity: Filter by severity (high, medium, low)
        since_date: Only show detections since this date (YYYY-MM-DD)
    """
    from server.coach.mcp_handlers import handle_coach_antipatterns
    return handle_coach_antipatterns(rule_id, group, severity, since_date)


@mcp.tool()
def coach_converse(query: str, context: str = "") -> str:
    """Conversational coaching assistant. Answers natural-language questions about AI coding practice.

    Args:
        query: Natural-language coaching question (e.g. 'how can I improve my prompt quality?')
        context: Optional session ID or project name for context
    """
    from server.coach.mcp_handlers import handle_coach_converse
    return handle_coach_converse(query, context)


@mcp.tool()
def coach_skill_finder(pattern: str) -> str:
    """Match a prompt pattern against NEXUS skill catalog.

    Args:
        pattern: A prompt pattern or description to match against (e.g. 'debugging with logs')
    """
    from server.coach.mcp_handlers import handle_coach_skill_finder
    return handle_coach_skill_finder(pattern)


@mcp.tool()
def coach_analyze_sessions(harness: str = "", since_date: str = "", limit: int = 10) -> str:
    """Deep-dive session analysis: request patterns, model usage, harness distribution.

    Args:
        harness: Filter by harness (e.g. 'Claude', 'Copilot', 'OpenCode')
        since_date: Only include sessions since this date (YYYY-MM-DD)
        limit: Maximum number of sessions to return (default 10, max 100)
    """
    from server.coach.mcp_handlers import handle_coach_analyze_sessions
    return handle_coach_analyze_sessions(harness, since_date, min(limit, 100))


from server.harness_mcp_bridge import register_harness_tools

register_harness_tools(mcp)


# ── Local harness tools (M5; additive — existing tools untouched) ──

def _local_harness_enabled() -> bool:
    # M05 contract: only the exact value "1" enables (default "1"); anything
    # else — including "0"/unset-to-other — delegates to legacy task_start.
    return os.environ.get("NEXUS_LOCAL_HARNESS", "1") == "1"


@mcp.tool()
def local_harness_open(
    task: str,
    project: str = "default",
    budget: int = 8,
    ide: str = "opencode",
    task_type: str = "general",
) -> str:
    """Open a Cordis-style local-harness session (OpenCode-first).

    ``task_type`` selects the project's per-task sites (P1.5); sites are
    harvested on open and released on local_harness_report.
    Flag off (NEXUS_LOCAL_HARNESS=0) delegates to task_start exactly.
    """
    if not _local_harness_enabled():
        return task_start(query=task, project=project, ide=ide, max_tools=budget)
    initialize_runtime()
    from server.local_harness.session import open_local_session  # lazy
    lh = open_local_session(task, project, budget, ide, task_type=task_type)
    payload = lh.inner.to_dict()
    payload["session_kind"] = "local_harness"
    payload["realm"] = lh.ctx.realm
    payload["site_status"] = dict(lh.site_status)
    payload["mcp_proxy"] = get_tool_session_broker().get_mcp_proxy_config(
        lh.inner.session_id)
    payload["instructions"] = (
        "Use only visible_tools for this task. "
        "Call local_harness_report(session_id, outcome, summary) when finished."
    )
    return json.dumps(payload, indent=2)


@mcp.tool()
def local_harness_report(
    session_id: str,
    outcome: str = "correct",
    notes: str = "",
    quality: float = 1.0,
    skill_name: str = "",
) -> str:
    """Close a local-harness session: belief_delta + LIFO dispose safety."""
    initialize_runtime()
    from server.local_harness.session import dispose_by_id  # lazy
    return json.dumps(
        dispose_by_id(session_id, outcome, notes, quality, skill_name), indent=2)


@mcp.tool()
def local_harness_reconcile(desired_json: str, project: str = "default",
                            apply: bool = True) -> str:
    """Diff a desired skill-entry tree against live resources and APPLY it (HMR).

    CONTRACT: desired_json is the COMPLETE desired active set (declarative,
    loader §5.2.1) — any active resource omitted from `entries` is ARCHIVED.
    Pass apply=false for a dry-run preview (applied=false, no mutations, no
    audit row).

    Live (Phase 5): one txn per call — add/remove/reload/rebuild/reassign/
    intercept mutate the resource surface (resources.status, cluster/sections,
    intercept meta). On failure the loader reverses applied ops LIFO, the txn
    rolls back (rolled_back=true, DB unchanged), and one row is written to
    local_harness_reconciles either way (audit counts = pre-diff on failure).
    Preview-only rollback: pass apply=false / delete apply_db.py.
    """
    from server.local_harness.apply_db import make_apply, record_reconcile
    from server.local_harness.loader import Entry, EntryTree, diff_trees, reconcile
    try:
        desired_raw = json.loads(desired_json or "{}")
    except Exception as exc:
        return json.dumps({"error": f"invalid desired_json: {exc}"})
    entries = {}
    items = desired_raw.get("entries", desired_raw) if isinstance(desired_raw, dict) else []
    if isinstance(items, dict):
        items = items.values()
    for e in items if isinstance(items, list) else []:
        if isinstance(e, dict) and e.get("id"):
            entries[e["id"]] = Entry(id=e["id"], kind=e.get("kind", "skill"),
                                     config=e.get("config", {}),
                                     disabled=bool(e.get("disabled", False)),
                                     isolate=e.get("isolate", "local"),
                                     intercept=e.get("intercept", {}) or {})
    desired = EntryTree(entries)
    try:
        conn = get_db()
    except Exception as exc:
        return json.dumps({"error": f"resources snapshot failed: {exc}"})
    try:
        try:
            current = EntryTree({
                row["name"]: Entry(id=row["name"], kind="skill")
                for row in conn.execute(
                    "SELECT name FROM resources WHERE status = 'active'")})
        except Exception as exc:
            return json.dumps({"error": f"resources snapshot failed: {exc}"})
        added, removed, maybe = diff_trees(current, desired)
        base = {"project": project, "added": sorted(added),
                "removed": sorted(removed), "maybe_changed": sorted(maybe)}
        if not apply:
            return json.dumps({**base, "applied": False,
                               "reason": "preview (apply=false)"}, indent=2)
        try:
            report = reconcile(current, desired,
                               apply=make_apply(conn, realm=f"local:{project}"))
            conn.commit()
        except Exception as exc:
            # loader already reversed applied ops LIFO; txn rollback discards
            # any partial writes → DB == prestate. Audit the failed run, then
            # report rolled_back=true.
            try:
                conn.rollback()
                record_reconcile(conn, project, added_ids=added,
                                 removed_ids=removed, changed_ids=maybe,
                                 extra={"rolled_back": True, "error": str(exc),
                                        "applied_ops": []})
                conn.commit()
            except Exception:
                print(f"[reconcile] audit row failed after rollback: {exc}",
                      file=sys.stderr, flush=True)
            return json.dumps({**base, "applied": False, "rolled_back": True,
                               "error": str(exc)}, indent=2)
        record_reconcile(conn, project, added_ids=report.added,
                         removed_ids=report.removed,
                         changed_ids=report.reloaded + report.rebuilt,
                         extra={"rolled_back": report.rolled_back,
                                "untouched": report.untouched})
        conn.commit()
        return json.dumps({**base,
                           "changed": sorted(report.reloaded + report.rebuilt),
                           "applied": not report.rolled_back,
                           "rolled_back": report.rolled_back}, indent=2)
    finally:
        conn.close()


@mcp.tool()
def local_harness_status() -> str:
    """Local-harness status: flag, sessions, redis, router mode."""
    try:
        from server.tool_session_broker import get_tool_session_broker
        active = len(get_tool_session_broker().list_active_sessions())
    except Exception:
        active = -1
    try:
        from server.router_cutover import get_router_status
        router_mode = get_router_status().get("mode")
    except Exception:
        router_mode = "unknown"
    try:
        from server.cache import redis_cache  # may not exist pre-M12... it does (Wave 0)
        redis_on = redis_cache.get_redis() is not None
    except Exception:
        redis_on = False
    return json.dumps({
        "local_harness": _local_harness_enabled(),
        "active_sessions": active,
        "redis_l4": redis_on,
        "router_mode": router_mode,
        "ide_default": "opencode",
    }, indent=2)


# ═════════════════════════════════════════════════════════════════════════════
# Browser automation over Obscura (server/browser_tool.py)
#
# Empirically grounded on 2026-11-18: obscura serve + CDP, --storage-dir is a
# persistent server-wide cookie jar, page state is per-connection (the module
# holds ONE long-lived connection). See the module docstring for measurements.
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def browser_status() -> str:
    """Obscura browser status: server running, port, profile dir, current page.

    Use this first when a task involves browsing: it reports whether the
    headless Rust browser (persistent, stealth, fixed Windows UA) is up and
    where the single long-lived page currently points.
    """
    from server import browser_tool
    return json.dumps(browser_tool.status(), indent=2)


@mcp.tool()
def browser_navigate(url: str) -> str:
    """Navigate the NEXUS Obscura browser to a URL and return page summary.

    Launches the browser automatically if it is not running (first call ~4-5 s).
    The cookie jar is persistent, so logged-in sessions (e.g. imported Gmail)
    survive server restarts. Returns final URL, title, and a text preview.

    Args:
        url: Target URL; an https:// prefix is added if missing.
    """
    from server import browser_tool
    return json.dumps(browser_tool.navigate(url), indent=2)


@mcp.tool()
def browser_read(selector: str = "", max_chars: int = 8000) -> str:
    """Read visible text from the current browser page, or from one element.

    Args:
        selector: Optional CSS selector to read a single element (e.g.
            'main', 'table.results'). Empty = whole page body text.
        max_chars: Truncation limit for returned text (default 8000).
    """
    from server import browser_tool
    return json.dumps(browser_tool.read(selector, max_chars), indent=2)


@mcp.tool()
def browser_click(selector: str) -> str:
    """Click an element on the current page by CSS selector.

    Args:
        selector: CSS selector of the element to click.
    """
    from server import browser_tool
    return json.dumps(browser_tool.click(selector), indent=2)


@mcp.tool()
def browser_type(selector: str, text: str, submit: bool = False) -> str:
    """Type text into a form field identified by CSS selector.

    Args:
        selector: CSS selector of the input/textarea.
        text: Text to type.
        submit: If true, press Enter after typing (submits most forms).
    """
    from server import browser_tool
    return json.dumps(browser_tool.type_text(selector, text, submit), indent=2)


@mcp.tool()
def browser_evaluate(expression: str) -> str:
    """Evaluate JavaScript in the current page and return the value.

    Args:
        expression: A JS expression to evaluate (e.g. 'document.title' or a
            JSON-returning IIFE).
    """
    from server import browser_tool
    return json.dumps(browser_tool.evaluate(expression), indent=2)


@mcp.tool()
def browser_screenshot(path: str = "") -> str:
    """Screenshot the current page to a PNG file.

    Args:
        path: Optional absolute file path. Default:
            D:/nexus-data/artifacts/browser/shot-<timestamp>.png
    """
    from server import browser_tool
    return json.dumps(browser_tool.screenshot(path), indent=2)


@mcp.tool()
def browser_cookies(domain: str = "") -> str:
    """List cookie NAMES in the browser session (values are never returned).

    Args:
        domain: Optional domain filter (e.g. 'google.com'). Empty = all.
    """
    from server import browser_tool
    return json.dumps(browser_tool.cookies(domain), indent=2)


@mcp.tool()
def browser_import_session(cookies_file: str) -> str:
    """Import a logged-in session from a cookie-export file (e.g. Gmail).

    THE LOGIN PATH: export cookies from your own logged-in browser with a
    cookie-editor extension (EditThisCookie JSON), then point this tool at the
    file. Cookies land in the browser's persistent jar and survive restarts.
    The export file is securely deleted after a successful import.

    Args:
        cookies_file: Path to the JSON cookie export (list of cookie objects).
    """
    from server import browser_tool
    return json.dumps(browser_tool.import_session(cookies_file), indent=2)


@mcp.tool()
def browser_stop() -> str:
    """Stop the NEXUS Obscura browser (kills the whole process tree)."""
    from server import browser_tool
    return json.dumps(browser_tool.stop_server(), indent=2)


# ═════════════════════════════════════════════════════════════════════════════
# One-trigger catalogue update (scripts/update_all.py)
#
# Swarm-pulls every workspace source repo in parallel (8 workers), re-absorbs
# the ones that moved, enriches, reindexes FTS, publishes to the vault and
# commits. Progress lands in D:/nexus-data/update_status.json after every
# stage, so nexus_update_status() can poll a live run.
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def nexus_update_all(mode: str = "fast") -> str:
    """Update ALL NEXUS resources to their latest upstream versions (one trigger).

    Swarm-pulls all ~109 source repos in parallel, re-absorbs changed ones,
    enriches tool stubs, rebuilds the FTS index, publishes to Obsidian and
    commits the vault. Returns immediately with a run id — poll progress with
    nexus_update_status().

    Args:
        mode: "fast" = pull + absorb + enrich + fts + publish + commit
              (default, minutes). "full" = also re-embed everything and
              re-cluster + rebuild graph edges (heavy CPU, much longer).
    """
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parent.parent
    script = _root / "scripts" / "update_all.py"
    if not script.is_file():
        return json.dumps({"ok": False, "error": f"not found: {script}"})
    if mode not in ("fast", "full"):
        mode = "fast"
    status_file = _Path(r"D:\nexus-data\update_status.json")
    # a previous run still alive? refuse to double-run (single writer, single vault)
    try:
        prev = json.loads(status_file.read_text(encoding="utf-8"))
        if prev.get("finished_at") is None and prev.get("mode") != "check":
            return json.dumps({"ok": False, "error": "a run is already in progress",
                              "run_id": prev.get("run_id"),
                              "hint": "poll nexus_update_status() until it finishes"})
    except (OSError, ValueError):
        pass
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_root) + (os.pathsep + env["PYTHONPATH"]
                                      if env.get("PYTHONPATH") else "")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    log = _Path(r"D:\nexus-data\logs") / f"update_all-launch-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        proc = subprocess.Popen(
            [_sys.executable, str(script), "--mode", mode],
            stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
            cwd=str(_root), env=env, creationflags=flags, close_fds=True)
    return json.dumps({
        "ok": True, "started": True, "run_id": f"manual-{time.strftime('%Y%m%d-%H%M%S')}",
        "mode": mode, "pid": proc.pid,
        "note": "running detached; poll nexus_update_status() for stage progress",
    }, indent=2)


@mcp.tool()
def nexus_update_status() -> str:
    """Report status of the last/latest update_all run (stage-by-stage).

    Returns the run id, mode, per-stage status (pull/absorb/enrich/fts/
    publish/commit), repo pull results (updated/current/failed), changed
    repos, and elapsed time. Use after nexus_update_all() to follow progress.
    """
    import time as _time
    from pathlib import Path as _Path
    status_file = _Path(r"D:\nexus-data\update_status.json")
    try:
        s = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.dumps({"ok": False,
                           "error": "no update has ever run",
                           "hint": "start one with nexus_update_all()"}, indent=2)
    stages = {k: {"status": v.get("status"),
                 "ms": v.get("ms"),
                 "progress": v.get("progress"),
                 "tail": v.get("tail")}
              for k, v in (s.get("stages") or {}).items()}
    pull = s.get("stages", {}).get("pull", {})
    out = {
        "ok": True,
        "run_id": s.get("run_id"), "mode": s.get("mode"),
        "running": s.get("finished_at") is None,
        "elapsed_s": s.get("elapsed_s") or round(
            (_time.time() - _time.mktime(_time.strptime(
                s.get("started_at", ""), "%Y-%m-%dT%H:%M:%SZ"))), 1)
        if s.get("started_at") and s.get("finished_at") is None else s.get("elapsed_s"),
        "repos_total": s.get("repos_total"),
        "pull_counts": pull.get("counts"),
        "pull_progress": pull.get("progress"),
        "changed_repos": (s.get("changed_repos") or [])[:20],
        "stages": stages,
        "summary": s.get("summary"),
        "vault_commit": (s.get("summary") or {}).get("vault_commit"),
        "finished_at": s.get("finished_at"),
    }
    return json.dumps(out, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
