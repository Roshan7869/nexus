#!/usr/bin/env python3
"""~/nexus/server/orchestrator.py — NEXUS Orchestrator (adaptive-imagining-cat workflow)"""
import json, sqlite3, sqlite_vec, os, sys, time, subprocess, shutil
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

# ── DB — consolidated from server/db.py (single source of truth)
from server.db import get_db_path

DB_PATH = get_db_path()
MAX_CONTEXT_TOKENS = 8192
CHECKPOINT_DIR = Path('/tmp/nexus-checkpoints')

# ── Dataclasses ─────────────────────────────────────────
@dataclass
class Phase:
    id: str
    type: str              # research | implement | test | verify | deploy | audit
    name: str
    description: str
    inputs: List[str]
    outputs: List[str]
    guardrails: List[str]
    resources: Dict[str, List[str]]
    parallel: bool
    estimated_tokens: int
    status: str = 'pending'  # pending | running | done | failed
    checkpoint: bool = True

@dataclass
class Plan:
    goal: str
    project: str
    phases: List[Phase]
    status: str = 'planning'
    total_estimated_tokens: int = 0

# ── DB ─────────────────────────────────────────────────
def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn

# ── Phase 0: Context Preservation ────────────────────────
def _load_skill_content(skill_name: str) -> str:
    """Read a skill's body text from the DB."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT body_text FROM resources WHERE name=? AND resource_type='skill'",
            (skill_name,),
        ).fetchone()
        return row["body_text"] if row else ""
    finally:
        conn.close()

def _read_claude_md() -> str:
    """Read~/CLAUDE.md for routing rules."""
    path = Path.home() / 'CLAUDE.md'
    if not path.exists():
        return ""
    return path.read_text(encoding='utf-8', errors='ignore')[:4000]

def _scan_environment() -> Dict:
    """Fast environment scan (git, files, etc.) — optimized for speed."""
    cwd = Path.cwd()
    state = {
        "cwd": str(cwd),
        "git_branch": None,
        "git_status": None,
        "recent_files": [],
        "file_count": -1,  # -1 means not counted (fast path)
    }
    
    # Fast git check using porcelain (avoids pager)
    try:
        git_dir = subprocess.run(['git', 'rev-parse', '--show-toplevel'], 
                                 capture_output=True, text=True, timeout=2)
        if git_dir.returncode == 0:
            top = git_dir.stdout.strip()
            # Fast branch
            br = subprocess.run(['git', '-C', top, 'symbolic-ref', '--short', 'HEAD'], 
                                  capture_output=True, text=True, timeout=1)
            state["git_branch"] = br.stdout.strip() if br.returncode == 0 else "detached"
            # Fast status (porcelain, no pager)
            st = subprocess.run(['git', '-C', top, 'status', '--short', '--no-renames'], 
                               capture_output=True, text=True, timeout=2)
            state["git_status"] = st.stdout.strip()[:200] if st.returncode == 0 else ""  # truncate
    except Exception:
        pass
    
    return state

def _discover_resources(task: str, project: str = "default") -> List[Dict]:
    """Query NEXUS for relevant skills/agents/commands."""
    from router import route_task
    result = route_task(task, project, ide=os.environ.get("NEXUS_IDE", "http"))
    return result.get("skills", [])

# ── Phase 1: State Analysis ──────────────────────────────
def phase_1_state_analysis(task: str, project: str) -> Dict:
    """Analyze current state with cluster routing."""
    print("[Phase 1] State Analysis...", flush=True)
    env = _scan_environment()
    skills = _discover_resources(task, project)
    claude_md = _read_claude_md()

    # Load adaptive-imagining-cat skill for context
    cat_skill = _load_skill_content("adaptive-imagining-cat")

    # Cluster routing: activate relevant domain clusters
    cluster_routing = None
    try:
        from cluster_router import ClusterRouter
        cr = ClusterRouter(str(DB_PATH))
        cluster_routing = cr.route_to_clusters(task, max_clusters=2, project=project)
    except Exception as e:
        print(f"  → Cluster routing unavailable: {e}", flush=True)

    # Auto-trigger: detect which clusters should activate
    auto_trigger_result = None
    try:
        from auto_trigger import AutoTrigger
        trigger = AutoTrigger()
        auto_trigger_result = trigger.route_task(task, max_clusters=2)
    except Exception as e:
        print(f"  → Auto-trigger unavailable: {e}", flush=True)

    analysis = {
        "task": task,
        "project": project,
        "environment": env,
        "relevant_skills": skills[:10],
        "claude_md_preview": claude_md[:500] if claude_md else "",
        "adaptive_imagining_cat_present": bool(cat_skill),
        "cluster_routing": cluster_routing,
        "auto_trigger": auto_trigger_result,
    }

    if cluster_routing:
        clusters = ", ".join(
            f"{c['name']}({c['affinity']:.2f})"
            for c in cluster_routing.get("activated_clusters", [])
        )
        print(f"  → Clusters: {clusters} | {cluster_routing.get('total_resources_scanned', 0)} resources", flush=True)
    print(f"  → Found {len(skills)} relevant skills, env: {env['cwd']}", flush=True)
    return analysis

# ── Phase 2: Typed Plan Creation ──────────────────────────
def phase_2_plan_creation(analysis: Dict, task: str) -> Plan:
    """Create a typed DAG plan."""
    print("[Phase 2] Typed Plan Creation...", flush=True)
    
    # Simple heuristics to build a plan structure
    # In a real system, this would use an LLM to generate the plan.
    # For now, we map common task keywords to phases.
    phases = []
    task_lower = task.lower()
    
    # Research phase
    if any(k in task_lower for k in ['research', 'investigate', 'debug', 'analyze']):
        phases.append(Phase(
            id="phase-1-research",
            type="research",
            name="Research and Analysis",
            description=f"Investigate: {task}",
            inputs=[],
            outputs=["research-notes.md", "diagnosis.md"],
            guardrails=["Search vault for existing solutions first", "Check for known bugs"],
            resources={"skills": ["search-first", "iterative-retrieval"], "agents": ["investigate"]},
            parallel=False,
            estimated_tokens=5000,
        ))
    
    # Architecture/Design phase
    if any(k in task_lower for k in ['design', 'architecture', 'system design', 'adr']):
        phases.append(Phase(
            id="phase-2-design",
            type="research",  # or design
            name="Design and Architecture",
            description="Create design documents and architecture decisions",
            inputs=["phase-1-research"] if phases else [],
            outputs=["DESIGN.md", "ADR-001.md"],
            guardrails=["Use existing design systems", "Follow project conventions"],
            resources={"skills": ["design-html", "design-review"], "agents": ["architect"]},
            parallel=False,
            estimated_tokens=3000,
        ))
    
    # Implementation phase
    phases.append(Phase(
        id="phase-3-implement",
        type="implement",
        name="Implementation",
        description=f"Build: {task}",
        inputs=[p.id for p in phases],
        outputs=["code", "tests"],
        guardrails=["TDD: test first", "Max 400 lines per file", "Immutable data patterns"],
        resources={"skills": ["tdd-workflow", "careful"], "agents": ["tdd-guide", "code-reviewer"]},
        parallel=False,
        estimated_tokens=15000,
    ))
    
    # Testing phase
    phases.append(Phase(
        id="phase-3.5-test",
        type="test",
        name="Testing and QA",
        description="Run tests, coverage, QA",
        inputs=["phase-3-implement"],
        outputs=["test-results", "coverage-report"],
        guardrails=["80% minimum coverage", "All critical paths tested"],
        resources={"skills": ["qa", "e2e-testing"], "agents": ["qa-testers"]},
        parallel=False,
        estimated_tokens=5000,
    ))
    
    # Deployment phase
    phases.append(Phase(
        id="phase-4-deploy",
        type="deploy",
        name="Deployment",
        description="Deploy with canary, rollback plan",
        inputs=["phase-3.5-test"],
        outputs=["deployed-url", "logs"],
        guardrails=["Canary deployment first", "Rollback plan ready"],
        resources={"skills": ["ship", "canary"], "agents": ["deploy-bots"]},
        parallel=False,
        estimated_tokens=3000,
    ))
    
    # Verification phase
    phases.append(Phase(
        id="phase-5-verify",
        type="verify",
        name="Block-Level Verification",
        description="Verify each phase output against plan",
        inputs=[p.id for p in phases],
        outputs=["verification-report.md"],
        guardrails=["Each phase output must match planned outputs", "No unchecked exceptions"],
        resources={"skills": ["verification-loop", "iterative-retrieval"], "agents": ["code-reviewer"]},
        parallel=False,
        estimated_tokens=5000,
    ))
    
    # Audit phase
    phases.append(Phase(
        id="phase-6-audit",
        type="audit",
        name="Execution Audit",
        description="Compare plan vs actual, generate learnings",
        inputs=["phase-5-verify"],
        outputs=["audit-report.md", "performance-matrix"],
        guardrails=["Impact analysis required", "Token usage logged"],
        resources={"skills": ["skill-stocktake"], "agents": ["auditor"]},
        parallel=False,
        estimated_tokens=3000,
    ))
    
    total_tokens = sum(p.estimated_tokens for p in phases)
    
    plan = Plan(
        goal=task,
        project=analysis.get("project", "default"),
        phases=phases,
        status="planning",
        total_estimated_tokens=total_tokens,
    )
    print(f"  → Created {len(phases)} phases, ~{total_tokens} tokens", flush=True)
    return plan

# ── Phase 3: Autopilot Execution ────────────────────────
def phase_3_execute(plan: Plan, start_index: int = 0) -> Dict:
    """Execute plan phases."""
    print("[Phase 3] Autopilot Execution...", flush=True)
    results = []
    
    for i, phase in enumerate(plan.phases):
        if i < start_index:
            continue
            
        print(f"  ▶ [{i+1}/{len(plan.phases)}] {phase.id}: {phase.name}", flush=True)
        phase.status = 'running'
        
        # Route the phase's core task to get model + skills
        from router import route_task
        task_desc = f"{phase.name}: {phase.description}"
        routing = route_task(task_desc, plan.project, ide=os.environ.get("NEXUS_IDE", "http"))
        
        # In a full autopilot mode, we'd actually dispatch this to the IDE.
        # For now, we log it and return the routing info.
        result = {
            "phase": asdict(phase),
            "routing": routing,
            "status": "dispatched",
            "output_files": phase.outputs,
        }
        results.append(result)
        phase.status = 'done'
        
        # Write checkpoint
        _write_checkpoint(plan, i+1)
    
    return {"phases_executed": len(results), "results": results}

def _write_checkpoint(plan: Plan, current_phase: int):
    """Write a session checkpoint."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "goal": plan.goal,
        "project": plan.project,
        "current_phase": current_phase,
        "phases_completed": [p.id for p in plan.phases[:current_phase] if p.status == 'done'],
        "phases_remaining": [p.id for p in plan.phases[current_phase:] if p.status == 'pending'],
        "status": "active",
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    with open(CHECKPOINT_DIR / f"plan-{plan.project}.json", 'w') as f:
        json.dump(checkpoint, f, indent=2)

# ── Phase 4: Verification ────────────────────────────────
def phase_4_verification(plan: Plan, execution_results: Dict) -> Dict:
    """Verify outputs."""
    print("[Phase 4] Block-Level Verification...", flush=True)
    checks = []
    for phase in plan.phases:
        # In a real system, we'd check actual file existence, test results, etc.
        check = {
            "block": phase.id,
            "verdict": True,  # Placeholder
            "explanation": "Automated check passed (placeholder)"
        }
        checks.append(check)
    return {"checks": checks}

# ── Phase 5: Audit Report ──────────────────────────────
def phase_5_audit(plan: Plan, execution_results: Dict, verification: Dict) -> Dict:
    """Generate audit report."""
    print("[Phase 5] Execution Audit...", flush=True)
    report = {
        "goal": plan.goal,
        "plan_type": "adaptive",
        "phases_total": len(plan.phases),
        "phases_completed": sum(1 for p in plan.phases if p.status == 'done'),
        "estimated_tokens": plan.total_estimated_tokens,
        "verification": verification,
    }
    return report

# ── Main Orchestrator Entry Point ────────────────────────
def orchestrate(task: str, project: str = "default", auto_execute: bool = False) -> Dict:
    """Run the full adaptive-imagining-cat workflow."""
    print("═══════════════════════════════════════════════════════════════", flush=True)
    print(f"  NEXUS Orchestrator  |  Task: {task}", flush=True)
    print("═══════════════════════════════════════════════════════════════", flush=True)
    
    # Phase 0: Context (implicit in Phase 1)
    # Phase 1
    analysis = phase_1_state_analysis(task, project)
    
    # Phase 2
    plan = phase_2_plan_creation(analysis, task)
    
    result = {
        "plan": asdict(plan),
        "approved": False,
        "execution": None,
        "verification": None,
        "audit": None,
    }
    
    # User approval gate
    if not auto_execute:
        print("\n▶ Plan ready for approval. Type 'go' to execute. (Not implemented in headless mode)", flush=True)
        return result
    
    # Phase 3
    execution = phase_3_execute(plan)
    result["execution"] = execution
    result["approved"] = True
    
    # Phase 4
    verification = phase_4_verification(plan, execution)
    result["verification"] = verification
    
    # Phase 5
    audit = phase_5_audit(plan, execution, verification)
    result["audit"] = audit
    
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: orchestrator.py '<task description>' [project_name] [--auto]")
        sys.exit(1)
    task = sys.argv[1]
    project = sys.argv[2] if len(sys.argv) > 2 else "default"
    auto = "--auto" in sys.argv
    output = orchestrate(task, project, auto)
    print(json.dumps(output, indent=2))
