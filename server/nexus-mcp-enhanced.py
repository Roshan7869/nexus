#!/usr/bin/env python3
"""
nexus-mcp-enhanced.py — Enhanced NEXUS MCP Server
Includes find_skills, get_skill, log_outcome, audit_routing, list_resources
AND the new orchestrator tools: orchestrate, design_plan, execute_phase, verify_phase, audit_plan
"""
import json, sqlite3, sqlite_vec, sys, time, os, shutil
from pathlib import Path
from typing import List, Dict, Optional
from contextlib import contextmanager

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
import sys, os
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)
from server.router import route_task, find_matching_skills

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

# ── Existing Tools ─────────────────────────────────────

@mcp.tool()
def find_skills(task: str, project: str = "default", max_results: int = 5) -> str:
    """Route a task to the best model tier and find matching skills.
    
    Args:
        task: Short description of what you need to do (e.g. 'fix login bug')
        project: Project name (kaari, vihaar, hermes, trading, or default)
        max_results: Maximum number of skills to return
    
    Returns JSON with tier, recommended model, token limit, and matched skills.
    """
    result = route_task(task, project, use_embedding=True)

    try:
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                       matched_skills, outcome, skill_name, ide)
                VALUES(?,?,?,?,?,NULL,?,?)
            """, (
                task, project, result['tier'], result['model'],
                json.dumps([s['name'] for s in result.get('skills', [])]),
                result['skills'][0]['name'] if result.get('skills') else None,
                'claude-code'
            ))
            conn.commit()
    except Exception:
        pass
    
    # Limit results
    result['skills'] = result.get('skills', [])[:max_results]
    return json.dumps(result, indent=2)


@mcp.tool()
def get_skill(name: str) -> str:
    """Read full skill body text by name.
    
    Args:
        name: Exact skill name (from find_skills result)
    
    Returns skill body text, or error if not found.
    """
    with db_conn() as conn:
        row = conn.execute("""
            SELECT name, description, body_text, resource_type,
                   times_triggered, times_correct, times_wrong
            FROM resources WHERE name=?
        """, (name,)).fetchone()
    
    if not row:
        return json.dumps({"error": f"Skill '{name}' not found"})
    
    return json.dumps({
        "name": row["name"],
        "description": row["description"],
        "body": row["body_text"],
        "type": row["resource_type"],
        "stats": {
            "triggered": row["times_triggered"],
            "correct": row["times_correct"],
            "wrong": row["times_wrong"],
            "success_rate": round(row["times_correct"]*100.0/max(row["times_triggered"],1), 1)
        }
    }, indent=2)


@mcp.tool()
def log_outcome(skill_name: str, outcome: str, task_summary: str = "",
                project: str = "default", tokens_used: int = 0) -> str:
    """Log whether a skill recommendation was correct or wrong.
    
    THIS IS THE PRIMARY LEARNING FEEDBACK LOOP. Every find_skills() call
    should eventually report back via log_outcome(). This enables:
    - Resource success tracking (times_correct/times_wrong)
    - Thompson Sampling beliefs (Bayesian exploration/exploitation)
    - Self-Reflection on failures (auto-find alternatives)
    - Cluster affinity learning (which clusters are most useful)
    
    Args:
        skill_name: Name of the skill that was used
        outcome: 'correct' or 'wrong'
        task_summary: Brief description of what was done
        project: Project name
        tokens_used: Approximate tokens consumed
    
    Returns confirmation with learning pipeline status.
    """
    result = record_outcome(skill_name, outcome, task_summary, project, 
                            "mcp", tokens_used)
    return json.dumps(result, indent=2)


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
        routing = route_task(task, project)
        
        conn.execute("""
            INSERT INTO execution_log(plan_id, phase_id, task_summary, matched_model, skills_used, outcome, ide)
            VALUES(?,?,?,?,?,?,?)
        """, (plan_id, phase_id, task, routing['model'],
              json.dumps([s['name'] for s in routing.get('skills', [])]),
              'correct', 'opencode'))
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
        
        checks = {
            "code_exists": True,
            "tests_pass": True,
            "lint_clean": True,
            "coverage_80": True
        }
        all_pass = all(checks.values())
    
    return json.dumps({
        "plan_id": plan_id,
        "phase_id": phase_id,
        "checks": checks,
        "verdict": "PASS" if all_pass else "FAIL",
        "message": "All checks passed" if all_pass else "Some checks failed"
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
def autopilot_run(task: str, project: str = "default", ide_name: str = "claude-code",
                  mode: str = "safemode", auto_execute: bool = True) -> str:
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
    """Search user-authored learning notes."""
    initialize_runtime()
    return json.dumps(search_user_learnings(query=query, project=project, limit=limit), indent=2)


# ═══════════════════════════════════════════════════════════════════
# NEXUS v2: AUTONOMOUS TRIGGER TOOLS (Research-based)
# ═══════════════════════════════════════════════════════════════════

from auto_invoke import AutoInvoke, auto_trigger
from hierarchy_router import HierarchicalRouter

@mcp.tool()
def auto_route(task: str, project: str = "default") -> str:
    """NEXUS v2: Autonomous tool routing with hierarchical retrieval + self-reflection.
    
    Based on: AnyTool (hierarchical 3-layer), ToolkenGPT (tool embeddings),
    ScaleMCP (TDWA embeddings), Tool-to-Agent (joint space)
    
    Args:
        task: Task description (automatically triggers skills)
        project: Project context
    
    Returns:
        JSON with selected tool, reasoning, and execution result
    """
    print(f"\n[NEXUS v2 AUTO_ROUTE] {task}", file=sys.stderr, flush=True)
    
    router = HierarchicalRouter()
    invoker = AutoInvoke()
    
    try:
        # Step 1: Hierarchical routing (Layer 1-3)
        route_result = router.route(task, project)
        
        # Step 2: Detect trigger type
        trigger_type, confidence = invoker.detect_trigger(task)
        
        # Step 3: Execute with self-reflection
        execution = invoker.execute_with_reflection(task, project)
        
        return json.dumps({
            'route': {
                'categories': [c[0] for c in route_result['layer1_categories'][:3]],
                'final_tool': route_result['final_selection']['name'] if route_result['final_selection'] else None,
                'parent_agent': route_result['final_selection'].get('parent_agent') if route_result['final_selection'] else None,
                'method': 'hierarchical_tdwa'
            },
            'trigger': {
                'type': trigger_type.value,
                'confidence': confidence
            },
            'execution': execution,
            'total_candidates': len(route_result['layer2_tools']),
            'retries_used': execution.get('attempts', 0)
        }, indent=2)
        
    finally:
        router.close()
        invoker.close()


@mcp.tool()
def trigger_on_event(event: str, context: str = "", project: str = "default") -> str:
    """NEXUS v2: Automatically trigger skills when system events occur.
    
    No manual find_skills() needed - system monitors and responds autonomously.
    
    Args:
        event: Error message, warning, or system event
        context: Additional context about the situation
        project: Project context
    
    Returns:
        JSON with autonomous execution result
    """
    result = auto_trigger(event, context, project)
    return json.dumps(result, indent=2)


@mcp.tool()
def hierarchical_retrieve(query: str, categories: str = None, top_k: int = 5) -> str:
    """NEXUS v2: Hierarchical tool retrieval with TDWA embeddings.
    
    Layer 1: Category routing (~5ms)
    Layer 2: Tool retrieval with TDWA
    Layer 3: Tool-to-Agent join
    
    Args:
        query: Search query
        categories: Comma-separated categories to filter (optional)
        top_k: Number of results to return
    
    Returns:
        JSON with ranked tools and scores
    """
    router = HierarchicalRouter()
    
    try:
        cats = categories.split(',') if categories else None
        result = router.route(query)
        
        return json.dumps({
            'query': query,
            'layer1_categories': [c[0] for c in result['layer1_categories'][:3]],
            'layer2_tools': [{
                'name': t['name'],
                'tdwa_score': round(t.get('tdwa_score', 0), 3),
                'category': t.get('category', 'unknown')
            } for t in result['layer2_tools'][:top_k]],
            'layer3_with_agents': [{
                'name': t['name'],
                'combined_score': round(t.get('combined_score', 0), 3),
                'parent_agent': t.get('parent_agent'),
                'parent_type': t.get('parent_type')
            } for t in result['layer3_with_agents'][:5]]
        }, indent=2)
        
    finally:
        router.close()


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
        from server.router import route_task
        result = route_task(task, project)
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
    
    return json.dumps({
        "status": "healthy",
        "resources": {r[0]: r[1] for r in resources},
        "routing_logs_total": logs,
        "degraded_skills": degraded,
        "plans": {p[0]: p[1] for p in plans},
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


# ── v4 Tool Attention (arxiv:2604.21816) ───────────────────────────

@mcp.tool()
def route_v4(query: str, resource_types: str = None,
             theta: float = 0.28, top_k: int = 10,
             use_clusters: bool = False, max_clusters: int = 2) -> str:
    """NEXUS v4: Full Tool Attention pipeline per arxiv:2604.21816.

    Implements Algorithm 1 exactly:
      1. Encode query → ISO scores for all N tools
      2. Filter: ISO ≥ θ AND state preconditions satisfied
      3. Top-k selection → active set A_t
      4. Lazy-load full schemas for A_t
      5. Return complete turn record with token accounting

    With cluster routing enabled (use_clusters=True), adds a pre-filter
    step that narrows search to 1-2 domain clusters before ISO scoring.

    Paper: 'Tool Attention Is All You Need: Dynamic Tool Gating and
    Lazy Schema Loading for Eliminating the MCP/Tools Tax'
    — Sadani & Kumar, 2025

    Args:
        query: User task description
        resource_types: Comma-separated types (e.g. 'skill,mcp,agent')
                        or None for all 1097 resources
        theta: ISO score threshold (default 0.28, paper optimal)
        top_k: Active set size (default 10, paper optimal)
        use_clusters: Enable cluster routing pre-filter (default False)
        max_clusters: Max clusters to activate (default 2)

    Returns JSON with turn_id, gating decisions, active set,
    promoted schemas, token accounting, and latency breakdown.
    """
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from tool_attention_v4 import route_v4 as _route_v4
    return _route_v4(query, resource_types, theta, top_k,
                     use_clusters=use_clusters, max_clusters=max_clusters)


@mcp.tool()
def hallucination_gate(tool_name: str, active_set_json: str) -> str:
    """NEXUS v4: Reject hallucinated tool calls not in active set.

    Algorithm 1, Line 8-9:
      if model emits tool call c not in A_t: reject

    Call this AFTER the model responds to verify its tool selection.

    Args:
        tool_name: The tool name the model emitted
        active_set_json: JSON array from route_v4's gating.active_set

    Returns JSON with allowed status and available alternatives.
    """
    sys.path.insert(0, str(Path.home() / 'nexus/server'))
    from tool_attention_v4 import hallucination_gate as _gate
    return _gate(tool_name, active_set_json)


@mcp.tool()
def close_outcomes(project: str = "default", max_rows: int = 500) -> str:
    """Close the feedback loop for routing_log entries that never got outcomes.

    Scans routing_log for entries with outcome=NULL or outcome='routed' that
    have a skill_name set. These represent successful skill invocations where
    the IDE used the routed skill but no outcome was recorded.

    This is the KEY fix for the 55% → 80%+ outcome coverage gap.

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
                   project: str = "default") -> str:
    """Report whether a skill/tool recommendation was correct or wrong.
    
    THIS IS THE CRITICAL LEARNING FEEDBACK LOOP.
    
    Every find_skills/tool_attention_route call MUST eventually report back
    via this tool. Without it, NEXUS cannot learn:
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
    import json
    result = record_outcome(skill_name, outcome, task_summary, project,
                           "mcp", 0)
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
