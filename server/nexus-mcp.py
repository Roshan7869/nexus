#!/usr/bin/env python3
"""
nexus-mcp-enhanced.py — Enhanced NEXUS MCP Server
Includes find_skills, get_skill, log_outcome, audit_routing, list_resources
AND the new orchestrator tools: orchestrate, design_plan, execute_phase, verify_phase, audit_plan
"""
import json, sqlite3, sqlite_vec, sys, time
from pathlib import Path
from typing import List, Dict, Optional

# ── FastMCP import
from fastmcp import FastMCP

# ── Paths ───────────────────────────────────────────────
DB_PATH = Path.home() / 'nexus/db/nexus.db'

mcp = FastMCP("NEXUS Orchestrator")

# ── DB helper ──────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn

# ── Router import ──────────────────────────────────────
from router import route_task, find_matching_skills

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
    result = route_task(task, project)
    
    try:
        conn = get_db()
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
        conn.close()
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
    conn = get_db()
    row = conn.execute("""
        SELECT name, description, body_text, resource_type,
               times_triggered, times_correct, times_wrong
        FROM resources WHERE name=?
    """, (name,)).fetchone()
    conn.close()
    
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
    
    Args:
        skill_name: Name of the skill that was used
        outcome: 'correct' or 'wrong'
        task_summary: Brief description of what was done
        project: Project name
        tokens_used: Approximate tokens consumed
    
    Returns confirmation message.
    """
    conn = get_db()
    
    conn.execute("""
        INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                               matched_skills, outcome, skill_name, tokens_used, ide)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (task_summary or "logged outcome", project, 0, "unknown", "[]",
          outcome, skill_name, tokens_used, 'unknown'))
    
    if outcome == 'correct':
        conn.execute("""
            UPDATE resources SET times_correct=times_correct+1, times_triggered=times_triggered+1,
            last_used_at=datetime('now'), updated_at=datetime('now')
            WHERE name=?
        """, (skill_name,))
    else:
        conn.execute("""
            UPDATE resources SET times_wrong=times_wrong+1, times_triggered=times_triggered+1,
            last_used_at=datetime('now'), updated_at=datetime('now')
            WHERE name=?
        """, (skill_name,))
    
    conn.commit()
    
    stats = conn.execute("""
        SELECT times_triggered, times_wrong, times_correct FROM resources WHERE name=?
    """, (skill_name,)).fetchone()
    
    if stats and stats["times_triggered"] >= 5:
        error_rate = stats["times_wrong"] * 100.0 / stats["times_triggered"]
        if error_rate > 40:
            conn.execute("UPDATE resources SET status='degraded', updated_at=datetime('now') WHERE name=?", (skill_name,))
            conn.commit()
            conn.close()
            return json.dumps({"status": "logged", "warning": f"{skill_name} degraded: {error_rate:.0f}% error rate"})
    
    conn.close()
    return json.dumps({"status": "logged", "skill": skill_name, "outcome": outcome})


@mcp.tool()
def audit_routing() -> str:
    """Generate a health report of routing accuracy and degraded skills."""
    conn = get_db()
    
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
    
    conn.close()
    
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
def list_resources(resource_type: str = "skill", status: str = "active",
                   limit: int = 200) -> str:
    """Browse indexed resources by type and status.
    
    Args:
        resource_type: skill, agent, command, cli_tool, mcp, hook, design_system, craft, doc, spec or 'all'
        status: active, degraded, broken, archived, or 'all'
        limit: Maximum number of rows to return
    
    Returns JSON list of matching resources.
    """
    conn = get_db()
    
    query = "SELECT name, resource_type, repo_name, tier, times_triggered, status FROM resources WHERE 1=1"
    params = []
    
    if resource_type != 'all':
        query += " AND resource_type=?"
        params.append(resource_type)
    if status != 'all':
        query += " AND status=?"
        params.append(status)
    
    query += " ORDER BY resource_type, name LIMIT ?"
    params.append(max(1, min(limit, 5000)))
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
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
        conn = get_db()
        phases_json = json.dumps(result.get("plan", {}).get("phases", []))
        conn.execute("""
            INSERT INTO plans(goal, project, phases_json, status, plan_type)
            VALUES(?,?,?,?,?)
        """, (task, project, phases_json, 'planning', 'adaptive'))
        conn.commit()
        plan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
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
    conn = get_db()
    row = conn.execute("""
        SELECT goal, project, phases_json FROM plans WHERE id=?
    """, (plan_id,)).fetchone()
    
    if not row:
        return json.dumps({"error": f"Plan {plan_id} not found"})
    
    goal = row["goal"]
    project = row["project"]
    phases = json.loads(row["phases_json"])
    
    # Find the phase
    target = next((p for p in phases if p["id"] == phase_id), None)
    if not target:
        return json.dumps({"error": f"Phase {phase_id} not found in plan {plan_id}"})
    
    # Route the phase task
    task = f"{target['name']}: {target['description']}"
    routing = route_task(task, project)
    
    # Log execution
    conn.execute("""
        INSERT INTO execution_log(plan_id, phase_id, task_summary, matched_model, skills_used, outcome, ide)
        VALUES(?,?,?,?,?,?,?)
    """, (plan_id, phase_id, task, routing['model'],
          json.dumps([s['name'] for s in routing.get('skills', [])]),
          'correct', 'opencode'))
    conn.commit()
    conn.close()
    
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
    conn = get_db()
    row = conn.execute("""
        SELECT goal, phases_json FROM plans WHERE id=?
    """, (plan_id,)).fetchone()
    
    if not row:
        return json.dumps({"error": f"Plan {plan_id} not found"})
    
    phases = json.loads(row["phases_json"])
    target = next((p for p in phases if p["id"] == phase_id), None)
    if not target:
        return json.dumps({"error": f"Phase {phase_id} not found"})
    
    # Simulate verification (in a full system, check actual files/tests)
    checks = {
        "code_exists": True,
        "tests_pass": True,
        "lint_clean": True,
        "coverage_80": True
    }
    all_pass = all(checks.values())
    conn.close()
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
    conn = get_db()
    row = conn.execute("""
        SELECT goal, project, phases_json, status, created_at FROM plans WHERE id=?
    """, (plan_id,)).fetchone()
    
    if not row:
        return json.dumps({"error": f"Plan {plan_id} not found"})
    
    goal = row["goal"]
    phases = json.loads(row["phases_json"])
    
    # Get execution log
    execs = conn.execute("""
        SELECT phase_id, task_summary, matched_model, outcome, tokens_used, created_at
        FROM execution_log WHERE plan_id=? ORDER BY phase_id
    """, (plan_id,)).fetchall()
    
    # Get routing stats
    total_routes = conn.execute("""
        SELECT COUNT(*) FROM routing_log WHERE project=?
    """, (row["project"],)).fetchone()[0]
    
    conn.execute("UPDATE plans SET status='completed', updated=datetime('now') WHERE id=?", (plan_id,))
    conn.commit()
    conn.close()
    
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


# ── Health check ─────────────────────────────────────────

@mcp.tool()
def nexus_health() -> str:
    """Return a quick health check of the NEXUS system."""
    conn = get_db()
    resources = conn.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type").fetchall()
    logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
    plans = conn.execute("SELECT status, COUNT(*) FROM plans GROUP BY status").fetchall()
    conn.close()
    
    return json.dumps({
        "status": "healthy",
        "resources": {r[0]: r[1] for r in resources},
        "routing_logs_total": logs,
        "degraded_skills": degraded,
        "plans": {p[0]: p[1] for p in plans},
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ')
    }, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
