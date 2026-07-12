#!/usr/bin/env python3
"""NEXUS broker/runtime layer for adaptive-imagining-cat autopilot.

This module sits above the existing router + learning stack and turns NEXUS
into a service-layer broker for TUI agents. It is intentionally conservative:
it generates a one-time execution preview, activates bundles and policies,
records per-subtask tool traces, and feeds user reviews back into the learner.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from server.router import route_task
    from server.service_layer import (
        SERVICE_BUNDLES,
        RESOURCE_CLUSTER_OVERRIDES,
        bundle_for_cluster,
        canonical_cluster_for_resource,
    )
    from server.learning import record_outcome
except Exception:
    from router import route_task
    from service_layer import (
        SERVICE_BUNDLES,
        RESOURCE_CLUSTER_OVERRIDES,
        bundle_for_cluster,
        canonical_cluster_for_resource,
    )
    from learning import record_outcome

logger = logging.getLogger(__name__)

from server.db import get_db_path

EXECUTOR_SKILL = "adaptive-imagining-cat"

MODE_POLICIES = {
    "safemode": {
        "min_confidence": 0.55,
        "allow_side_effects": {"read", "local_write"},
        "fallback_limit": 2,
        "description": "Safe-first autopilot with stronger gates for writes and ops.",
    },
    "godmode": {
        "min_confidence": 0.40,
        "allow_side_effects": {"read", "local_write", "external_write", "operational"},
        "fallback_limit": 4,
        "description": "Aggressive autopilot after single preview; broader fallback and execution.",
    },
}

STAGE_ORDER = ["understand", "research", "design", "implement", "verify", "operate", "audit"]
ALL_IDES = {"codex", "opencode", "hermes", "claude-code", "unknown"}


def _get_conn() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_db_path() -> Path:
    return get_db_path()


def normalize_mode(mode: str | None) -> str:
    mode = (mode or "safemode").strip().lower()
    return mode if mode in MODE_POLICIES else "safemode"


def normalize_ide(ide_name: str | None) -> str:
    ide_name = (ide_name or "unknown").strip().lower()
    return ide_name if ide_name in ALL_IDES else "unknown"


def normalize_legacy_ide(ide_name: str | None) -> str:
    """routing_log/legacy tables still constrain ide to a smaller enum."""
    ide_name = normalize_ide(ide_name)
    return ide_name if ide_name in {"opencode", "claude-code", "unknown"} else "unknown"


def _json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def ensure_runtime_schema() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                id TEXT PRIMARY KEY,
                task_text TEXT NOT NULL,
                project TEXT NOT NULL DEFAULT 'default',
                ide_name TEXT NOT NULL DEFAULT 'unknown',
                mode TEXT NOT NULL DEFAULT 'safemode',
                cwd TEXT NOT NULL DEFAULT '',
                repo_context_json TEXT NOT NULL DEFAULT '{}',
                executor_skill TEXT NOT NULL DEFAULT 'adaptive-imagining-cat',
                status TEXT NOT NULL DEFAULT 'previewed',
                task_type TEXT NOT NULL DEFAULT 'general',
                cluster TEXT,
                bundle_graph_json TEXT NOT NULL DEFAULT '{}',
                plan_json TEXT NOT NULL DEFAULT '{}',
                execution_preview_json TEXT NOT NULL DEFAULT '{}',
                final_report_json TEXT,
                review_status TEXT NOT NULL DEFAULT 'pending',
                plan_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tool_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_run_id TEXT NOT NULL,
                phase_id TEXT NOT NULL,
                subtask_name TEXT NOT NULL,
                bundle_name TEXT,
                resource_name TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT 'unknown',
                execution_mode TEXT NOT NULL DEFAULT 'review_only',
                side_effect_level TEXT NOT NULL DEFAULT 'read',
                tool_role TEXT NOT NULL DEFAULT 'candidate',
                trace_order INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'selected',
                outcome TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
            );

            CREATE TABLE IF NOT EXISTS bundle_membership (
                resource_id INTEGER NOT NULL,
                bundle_name TEXT NOT NULL,
                bundle_kind TEXT NOT NULL DEFAULT 'capability',
                priority INTEGER NOT NULL DEFAULT 50,
                is_canonical INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (resource_id, bundle_name)
            );

            CREATE TABLE IF NOT EXISTS resource_policies (
                resource_id INTEGER PRIMARY KEY,
                execution_mode TEXT NOT NULL DEFAULT 'review_only',
                side_effect_level TEXT NOT NULL DEFAULT 'read',
                mode_allowance TEXT NOT NULL DEFAULT 'both',
                stage_allowance TEXT NOT NULL DEFAULT '[]',
                allowed_ides TEXT NOT NULL DEFAULT '[]',
                preconditions_json TEXT NOT NULL DEFAULT '[]',
                fallback_priority INTEGER NOT NULL DEFAULT 50,
                confidence_floor REAL NOT NULL DEFAULT 0.55,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL DEFAULT 'task_type',
                task_type TEXT NOT NULL DEFAULT 'general',
                project TEXT NOT NULL DEFAULT 'default',
                note TEXT NOT NULL,
                bundles_json TEXT NOT NULL DEFAULT '[]',
                tools_json TEXT NOT NULL DEFAULT '[]',
                created_by TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS execution_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_run_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
            );

            CREATE TABLE IF NOT EXISTS mistake_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_type TEXT NOT NULL,
                condition_key TEXT NOT NULL,
                pattern_text TEXT NOT NULL,
                recovery_advice TEXT NOT NULL DEFAULT '',
                source_task_run_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
            CREATE INDEX IF NOT EXISTS idx_task_runs_project ON task_runs(project);
            CREATE INDEX IF NOT EXISTS idx_tool_traces_task_run ON tool_traces(task_run_id);
            CREATE INDEX IF NOT EXISTS idx_bundle_membership_bundle ON bundle_membership(bundle_name);
            CREATE INDEX IF NOT EXISTS idx_user_learnings_project ON user_learnings(project);
            CREATE INDEX IF NOT EXISTS idx_execution_reviews_run ON execution_reviews(task_run_id);
            CREATE INDEX IF NOT EXISTS idx_mistake_patterns_key ON mistake_patterns(condition_type, condition_key);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _capability_bundles(row: sqlite3.Row) -> List[str]:
    resource_type = row["resource_type"]
    name = (row["name"] or "").lower()
    description = (row["description"] or "").lower()
    bundles: List[str] = []

    type_map = {
        "doc": ["docs_context"],
        "spec": ["docs_context"],
        "design_system": ["design_systems"],
        "mcp": ["mcp_runtime"],
        "command": ["cli_ops"],
        "cli_tool": ["cli_ops"],
        "hook": ["automation_hooks"],
        "agent": ["agent_assist"],
        "skill": ["skill_runtime"],
        "craft": ["docs_context"],
    }
    bundles.extend(type_map.get(resource_type, ["skill_runtime"]))

    if any(token in name or token in description for token in ["review", "audit", "verify", "qa", "test"]):
        bundles.append("verification")
    if any(token in name or token in description for token in ["memory", "learn", "context", "note"]):
        bundles.append("memory_capture")
    if any(token in name or token in description for token in ["search", "retrieve", "vault", "wiki", "doc"]):
        bundles.append("repo_search")
    if any(token in name or token in description for token in ["figma", "design", "css", "ui", "html"]):
        bundles.append("design_systems")
    if any(token in name or token in description for token in ["mcp", "tool", "api", "command"]):
        bundles.append("tooling_access")

    return sorted(set(bundles))


def sync_bundle_membership() -> None:
    ensure_runtime_schema()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, resource_type, cluster, description FROM resources WHERE status='active'"
        ).fetchall()
        for row in rows:
            domain_bundle = canonical_cluster_for_resource(row["name"], row["cluster"] or "general") or "general"
            bundles = [(domain_bundle, "domain", 100, 1)]

            for b in _capability_bundles(row):
                bundles.append((b, "capability", 60, 0))

            if row["name"] in RESOURCE_CLUSTER_OVERRIDES:
                bundles.append((row["name"], "canonical_resource", 110, 1))

            for canonical in bundle_for_cluster(domain_bundle):
                if canonical == row["name"]:
                    bundles.append((f"{domain_bundle}:canonical", "capability", 105, 1))
                    break

            for bundle_name, bundle_kind, priority, is_canonical in {(b[0], b[1], b[2], b[3]) for b in bundles}:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO bundle_membership
                    (resource_id, bundle_name, bundle_kind, priority, is_canonical)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (row["id"], bundle_name, bundle_kind, priority, is_canonical),
                )
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.warning(f"bundle membership sync skipped: {e}")
    finally:
        conn.close()


def _default_policy(resource_type: str, cluster: str, name: str) -> Dict[str, Any]:
    side_effect = "read"
    execution_mode = "review_only"
    mode_allowance = "both"
    fallback_priority = 50
    confidence_floor = 0.55
    stages = ["understand", "research", "design", "implement", "verify", "audit"]

    if resource_type in {"doc", "spec", "craft", "design_system"}:
        execution_mode = "silent_service"
        confidence_floor = 0.35
    elif resource_type in {"skill", "agent", "mcp"}:
        execution_mode = "auto_invoke"
        confidence_floor = 0.45
    elif resource_type in {"command", "cli_tool"}:
        execution_mode = "review_only"
        side_effect = "local_write"
        confidence_floor = 0.65
    elif resource_type == "hook":
        execution_mode = "silent_service"
        side_effect = "local_write"
        confidence_floor = 0.60

    if cluster == "devops_infra":
        stages = ["implement", "verify", "operate", "audit"]
    if resource_type in {"command", "cli_tool"} and any(token in name.lower() for token in ["deploy", "ship", "release", "prod"]):
        side_effect = "operational"
        mode_allowance = "godmode"
        stages = ["operate", "audit"]
        confidence_floor = 0.75

    return {
        "execution_mode": execution_mode,
        "side_effect_level": side_effect,
        "mode_allowance": mode_allowance,
        "stage_allowance": stages,
        "allowed_ides": ["codex", "opencode", "hermes", "claude-code", "unknown"],
        "preconditions_json": [],
        "fallback_priority": fallback_priority,
        "confidence_floor": confidence_floor,
    }


def sync_resource_policies() -> None:
    ensure_runtime_schema()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, resource_type, cluster FROM resources WHERE status='active'"
        ).fetchall()
        for row in rows:
            policy = _default_policy(row["resource_type"], row["cluster"] or "general", row["name"])
            conn.execute(
                """
                INSERT OR REPLACE INTO resource_policies
                (resource_id, execution_mode, side_effect_level, mode_allowance,
                 stage_allowance, allowed_ides, preconditions_json,
                 fallback_priority, confidence_floor, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM resource_policies WHERE resource_id=?), datetime('now')), datetime('now'))
                """,
                (
                    row["id"],
                    policy["execution_mode"],
                    policy["side_effect_level"],
                    policy["mode_allowance"],
                    _json(policy["stage_allowance"]),
                    _json(policy["allowed_ides"]),
                    _json(policy["preconditions_json"]),
                    policy["fallback_priority"],
                    policy["confidence_floor"],
                    row["id"],
                ),
            )
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.warning(f"resource policy sync skipped: {e}")
    finally:
        conn.close()


def initialize_runtime() -> None:
    ensure_runtime_schema()
    try:
        from server.tool_session_broker import ensure_tool_session_schema
        ensure_tool_session_schema()
    except Exception:
        pass
    sync_bundle_membership()
    sync_resource_policies()


def _task_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _plan_id(conn: sqlite3.Connection, task: str, project: str, phases: List[Dict[str, Any]], plan_type: str) -> int:
    conn.execute(
        """
        INSERT INTO plans(goal, project, phases_json, status, plan_type, current_phase,
                          phases_completed, phases_remaining, artifacts)
        VALUES (?, ?, ?, 'planning', ?, 0, '[]', ?, '[]')
        """,
        (task, project, json.dumps(phases), plan_type, json.dumps([p["id"] for p in phases])),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _phase_templates(task: str, cluster: str, task_type: str) -> List[Dict[str, Any]]:
    phases = [
        {"id": "understand", "name": "Understand Task", "description": task, "stage": "understand"},
        {"id": "research", "name": "Research Context", "description": f"Research constraints and context for: {task}", "stage": "research"},
    ]
    if cluster in {"frontend_ui", "architect", "backend_api"} or any(k in task.lower() for k in ["design", "architecture", "ui", "api"]):
        phases.append({"id": "design", "name": "Design Plan", "description": f"Design the approach for: {task}", "stage": "design"})
    phases.append({"id": "implement", "name": "Implement Solution", "description": f"Implement: {task}", "stage": "implement"})
    phases.append({"id": "verify", "name": "Verify Output", "description": f"Verify work for: {task}", "stage": "verify"})
    if cluster == "devops_infra" or any(k in task.lower() for k in ["deploy", "release", "ship", "operate"]):
        phases.append({"id": "operate", "name": "Operate / Deploy", "description": f"Operate or deploy: {task}", "stage": "operate"})
    phases.append({"id": "audit", "name": "Audit and Learn", "description": f"Audit execution and capture learnings for: {task}", "stage": "audit"})
    for i, phase in enumerate(phases):
        phase["order"] = i
        phase["executor"] = EXECUTOR_SKILL
    return phases


def _active_bundles(cluster: str, route_result: Dict[str, Any]) -> Dict[str, List[str]]:
    domain = cluster or route_result.get("cluster") or "general"
    canonical = bundle_for_cluster(domain)
    capabilities = ["repo_search", "docs_context", "memory_capture"]
    if domain == "frontend_ui":
        capabilities.extend(["design_systems", "tooling_access"])
    elif domain == "backend_api":
        capabilities.extend(["tooling_access"])
    elif domain == "devops_infra":
        capabilities.extend(["cli_ops", "verification"])
    elif domain == "quality_security":
        capabilities.extend(["verification"])
    elif domain == "analyzer_planner":
        capabilities.extend(["verification", "docs_context"])

    return {
        "domain": [domain],
        "canonical_resources": canonical,
        "capability": sorted(set(capabilities)),
        "mode": ["safemode_guard", "fallback_recovery"],
    }


def _resource_row(conn: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.id, r.name, r.resource_type, r.cluster, p.execution_mode,
               p.side_effect_level, p.mode_allowance, p.stage_allowance,
               p.allowed_ides, p.confidence_floor, p.fallback_priority
        FROM resources r
        LEFT JOIN resource_policies p ON p.resource_id = r.id
        WHERE r.name = ?
        """,
        (name,),
    ).fetchone()


def _policy_allows(resource: sqlite3.Row, stage: str, mode: str, ide_name: str, score: float) -> bool:
    if not resource:
        return False
    mode_allowance = resource["mode_allowance"] or "both"
    if mode_allowance not in {"both", mode}:
        return False
    allowed_ides = set(json.loads(resource["allowed_ides"] or "[]"))
    if allowed_ides and ide_name not in allowed_ides:
        return False
    stages = set(json.loads(resource["stage_allowance"] or "[]"))
    if stages and stage not in stages:
        return False
    if score < float(resource["confidence_floor"] or 0.0):
        return False
    side_effect = resource["side_effect_level"] or "read"
    if side_effect not in MODE_POLICIES[mode]["allow_side_effects"]:
        return False
    return True


def _select_phase_tools(
    conn: sqlite3.Connection,
    phase: Dict[str, Any],
    project: str,
    mode: str,
    ide_name: str,
    domain_cluster: str,
) -> List[Dict[str, Any]]:
    routed = route_task(phase["description"], project, use_embedding=True)
    candidates = routed.get("skills", [])
    selected: List[Dict[str, Any]] = []

    for idx, candidate in enumerate(candidates[:8]):
        row = _resource_row(conn, candidate["name"])
        score = float(candidate.get("combined_score", candidate.get("score", 0.0)) or 0.0)
        if row and _policy_allows(row, phase["stage"], mode, ide_name, score):
            selected.append(
                {
                    "resource_name": row["name"],
                    "resource_type": row["resource_type"],
                    "execution_mode": row["execution_mode"] or "review_only",
                    "side_effect_level": row["side_effect_level"] or "read",
                    "score": round(score, 3),
                    "tool_role": "candidate",
                    "trace_order": idx,
                    "bundle_name": domain_cluster,
                }
            )

    if not selected:
        for canonical in bundle_for_cluster(domain_cluster):
            row = _resource_row(conn, canonical)
            if row and _policy_allows(row, phase["stage"], mode, ide_name, 1.0):
                selected.append(
                    {
                        "resource_name": row["name"],
                        "resource_type": row["resource_type"],
                        "execution_mode": row["execution_mode"] or "review_only",
                        "side_effect_level": row["side_effect_level"] or "read",
                        "score": 1.0,
                        "tool_role": "canonical_fallback",
                        "trace_order": len(selected),
                        "bundle_name": domain_cluster,
                    }
                )

    return selected[: MODE_POLICIES[mode]["fallback_limit"] + 2]


def _preview_from_phases(phases: List[Dict[str, Any]], phase_tools: Dict[str, List[Dict[str, Any]]], mode: str) -> Dict[str, Any]:
    preview = {
        "executor": EXECUTOR_SKILL,
        "mode": mode,
        "mode_policy": MODE_POLICIES[mode],
        "phases": [],
    }
    for phase in phases:
        preview["phases"].append(
            {
                "phase_id": phase["id"],
                "stage": phase["stage"],
                "name": phase["name"],
                "description": phase["description"],
                "tools": [
                    {
                        "name": tool["resource_name"],
                        "type": tool["resource_type"],
                        "execution_mode": tool["execution_mode"],
                        "side_effect_level": tool["side_effect_level"],
                        "score": tool["score"],
                    }
                    for tool in phase_tools.get(phase["id"], [])
                ],
            }
        )
    return preview


def create_task_run(
    task_text: str,
    *,
    ide_name: str = "unknown",
    mode: str = "safemode",
    project: str = "default",
    cwd: str = "",
    repo_context: Optional[Dict[str, Any]] = None,
    auto_execute: bool = True,
) -> Dict[str, Any]:
    initialize_runtime()
    ide_name = normalize_ide(ide_name)
    mode = normalize_mode(mode)
    repo_context = repo_context or {}

    route_result = route_task(task_text, project, use_embedding=True)
    cluster = route_result.get("cluster") or "general"
    task_type = route_result.get("task_type", "general")
    phases = _phase_templates(task_text, cluster, task_type)
    bundle_graph = _active_bundles(cluster, route_result)

    conn = _get_conn()
    try:
        plan_id = _plan_id(conn, task_text, project, phases, "autopilot")
        phase_tools: Dict[str, List[Dict[str, Any]]] = {}
        for phase in phases:
            phase_tools[phase["id"]] = _select_phase_tools(conn, phase, project, mode, ide_name, cluster)

        preview = _preview_from_phases(phases, phase_tools, mode)
        run_id = _task_run_id()
        conn.execute(
            """
            INSERT INTO task_runs
            (id, task_text, project, ide_name, mode, cwd, repo_context_json, executor_skill,
             status, task_type, cluster, bundle_graph_json, plan_json, execution_preview_json,
             review_status, plan_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, datetime('now'))
            """,
            (
                run_id,
                task_text,
                project,
                ide_name,
                mode,
                cwd,
                _json(repo_context),
                EXECUTOR_SKILL,
                "previewed",
                task_type,
                cluster,
                _json(bundle_graph),
                _json({"phases": phases}),
                _json(preview),
                plan_id,
            ),
        )

        for phase in phases:
            for tool in phase_tools[phase["id"]]:
                conn.execute(
                    """
                    INSERT INTO tool_traces
                    (task_run_id, phase_id, subtask_name, bundle_name, resource_name, resource_type,
                     execution_mode, side_effect_level, tool_role, trace_order, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'selected', ?)
                    """,
                    (
                        run_id,
                        phase["id"],
                        phase["name"],
                        tool["bundle_name"],
                        tool["resource_name"],
                        tool["resource_type"],
                        tool["execution_mode"],
                        tool["side_effect_level"],
                        tool["tool_role"],
                        tool["trace_order"],
                        f"stage={phase['stage']};mode={mode}",
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    result = {
        "task_run_id": run_id,
        "executor": EXECUTOR_SKILL,
        "cluster": cluster,
        "task_type": task_type,
        "mode": mode,
        "activated_bundles": bundle_graph,
        "execution_preview": preview,
        "status": "previewed",
    }
    if auto_execute:
        result.update(execute_task_run(run_id))
    return result


def _emit_outcome_for_trace(trace_name: str, trace_phase: str, outcome: str, project: str, ide: str):
    """Emit an outcome signal to close ALL learning loops.

    This is the unified outcome callback that fixes Gaps 2, 3, and 5.
    It calls record_outcome() which triggers:
      Layer 1: Outcome tracking (update resource counters)
      Layer 2: Thompson Sampling belief update
      Layer 3: Self-reflection on failure
      Layer 5: Cluster affinity update
    """
    try:
        from server.learning import record_outcome
        record_outcome(
            skill_name=trace_name,
            outcome=outcome,
            task_summary=trace_phase,
            project=project,
            ide=ide,
        )
    except Exception as e:
        logger.warning(f"_emit_outcome_for_trace failed for {trace_name}: {e}")


def execute_task_run(task_run_id: str) -> Dict[str, Any]:
    initialize_runtime()
    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM task_runs WHERE id = ?", (task_run_id,)).fetchone()
        if not run:
            return {"status": "error", "message": f"task_run {task_run_id} not found"}

        phases = json.loads(run["plan_json"]).get("phases", [])
        trace_rows = conn.execute(
            "SELECT * FROM tool_traces WHERE task_run_id = ? ORDER BY phase_id, trace_order, id",
            (task_run_id,),
        ).fetchall()
        trace_map: Dict[str, List[sqlite3.Row]] = {}
        for row in trace_rows:
            trace_map.setdefault(row["phase_id"], []).append(row)

        project = run["project"]
        ide = normalize_legacy_ide(run["ide_name"])

        final_phases = []
        for phase in phases:
            selected = trace_map.get(phase["id"], [])
            skills_used = [row["resource_name"] for row in selected]
            model = route_task(f"{phase['name']}: {phase['description']}", project, use_embedding=True).get("model", "unknown")
            outcome = "correct"
            conn.execute(
                """
                INSERT INTO execution_log(plan_id, phase_id, task_summary, matched_model, skills_used, outcome, ide)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["plan_id"],
                    phase["id"],
                    phase["description"],
                    model,
                    _json(skills_used),
                    outcome,
                    ide,
                ),
            )
            for row in selected:
                conn.execute(
                    "UPDATE tool_traces SET status='executed', outcome=?, notes=? WHERE id=?",
                    (outcome, f"executed via {EXECUTOR_SKILL} in {run['mode']}", row["id"]),
                )
                # --- Unified outcome callback: closes the learning loop ---
                _emit_outcome_for_trace(
                    trace_name=row["resource_name"],
                    trace_phase=f"{phase['id']}: {phase['name']}",
                    outcome=outcome,
                    project=project,
                    ide=ide,
                )
            final_phases.append(
                {
                    "phase_id": phase["id"],
                    "stage": phase["stage"],
                    "name": phase["name"],
                    "model": model,
                    "tools_used": skills_used,
                    "outcome": outcome,
                }
            )

        final_report = {
            "executor": EXECUTOR_SKILL,
            "task_run_id": task_run_id,
            "mode": run["mode"],
            "task": run["task_text"],
            "cluster": run["cluster"],
            "phases": final_phases,
            "summary": {
                "subtasks": len(final_phases),
                "tools_used": sum(len(p["tools_used"]) for p in final_phases),
                "status": "completed",
            },
            "learnings": suggested_learnings(task_run_id, conn=conn),
        }

        conn.execute(
            """
            UPDATE task_runs
            SET status='completed', final_report_json=?, updated_at=datetime('now')
            WHERE id = ?
            """,
            (_json(final_report), task_run_id),
        )
        conn.execute(
            "UPDATE plans SET status='completed', current_phase=?, phases_completed=?, phases_remaining='[]', updated_at=datetime('now') WHERE id=?",
            (
                len(phases),
                _json([phase["id"] for phase in phases]),
                run["plan_id"],
            ),
        )
        conn.commit()

        # ── Phase 4: Post-execution Coach anti-pattern sync ──────
        try:
            from server.coach.reflection_bridge import sync_antipatterns_to_reflections
            sync_result = sync_antipatterns_to_reflections(
                project=project, min_severity="medium"
            )
            if sync_result.get("synced", 0) > 0:
                logger.info(
                    "Coach: synced %s anti-patterns to reflection_failures",
                    sync_result["synced"],
                )
        except Exception as coach_err:
            logger.debug("Coach anti-pattern sync skipped: %s", coach_err)

        return {"status": "completed", "final_report": final_report}
    finally:
        conn.close()


def get_task_run(task_run_id: str) -> Dict[str, Any]:
    initialize_runtime()
    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM task_runs WHERE id = ?", (task_run_id,)).fetchone()
        if not run:
            return {"status": "error", "message": f"task_run {task_run_id} not found"}
        traces = conn.execute(
            "SELECT phase_id, subtask_name, resource_name, resource_type, execution_mode, side_effect_level, status, outcome FROM tool_traces WHERE task_run_id=? ORDER BY phase_id, trace_order, id",
            (task_run_id,),
        ).fetchall()
        return {
            "task_run_id": task_run_id,
            "task": run["task_text"],
            "project": run["project"],
            "ide_name": run["ide_name"],
            "mode": run["mode"],
            "status": run["status"],
            "cluster": run["cluster"],
            "task_type": run["task_type"],
            "executor": run["executor_skill"],
            "activated_bundles": json.loads(run["bundle_graph_json"]),
            "execution_preview": json.loads(run["execution_preview_json"]),
            "final_report": json.loads(run["final_report_json"]) if run["final_report_json"] else None,
            "tool_traces": [dict(row) for row in traces],
            "review_status": run["review_status"],
        }
    finally:
        conn.close()


def add_user_learning(
    note: str,
    *,
    scope: str = "task_type",
    task_type: str = "general",
    project: str = "default",
    bundles: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    created_by: str = "user",
) -> Dict[str, Any]:
    initialize_runtime()
    bundles = bundles or []
    tools = tools or []
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO user_learnings(scope, task_type, project, note, bundles_json, tools_json, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (scope, task_type, project, note, _json(bundles), _json(tools), created_by),
        )
        conn.commit()
        learning_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return {
            "status": "stored",
            "learning_id": learning_id,
            "note": note,
            "scope": scope,
            "task_type": task_type,
            "project": project,
        }
    finally:
        conn.close()


def search_user_learnings(query: str = "", *, project: str = "default", limit: int = 20) -> Dict[str, Any]:
    initialize_runtime()
    conn = _get_conn()
    try:
        sql = "SELECT id, scope, task_type, project, note, bundles_json, tools_json, created_by, created_at FROM user_learnings WHERE project=?"
        params: List[Any] = [project]
        if query:
            sql += " AND note LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()

        results = [
            {
                "id": row["id"],
                "scope": row["scope"],
                "task_type": row["task_type"],
                "project": row["project"],
                "note": row["note"],
                "bundles": json.loads(row["bundles_json"]),
                "tools": json.loads(row["tools_json"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

        # ── Embedding-based search (Gap 1 fix) ────────────────────
        # When LIKE returns no results but a query is provided, fall
        # back to loading all notes for the project and ranking by
        # cosine similarity. This catches semantic matches that the
        # strict LIKE %query% would miss.
        used_method = "keyword"
        if query:
            if not results:
                try:
                    all_rows = conn.execute(
                        "SELECT id, scope, task_type, project, note, bundles_json, tools_json, created_by, created_at FROM user_learnings WHERE project=? ORDER BY created_at DESC",
                        (project,),
                    ).fetchall()
                    if all_rows:
                        from server.iso_embedder import IsoEmbedder
                        embedder = IsoEmbedder.get()
                        embedder.load_model()
                        qvec = embedder.encode(query)
                        import numpy as np
                        note_texts = [r["note"] for r in all_rows]
                        note_vecs = embedder.embed_texts(note_texts)
                        scores = np.dot(note_vecs, qvec) / (
                            np.linalg.norm(note_vecs, axis=1) * np.linalg.norm(qvec) + 1e-10
                        )
                        ranked = sorted(
                            zip([dict(r) for r in all_rows], scores),
                            key=lambda x: x[1], reverse=True,
                        )
                        results = [
                            {
                                "id": r["id"], "scope": r["scope"],
                                "task_type": r["task_type"], "project": r["project"],
                                "note": r["note"],
                                "bundles": json.loads(r["bundles_json"]),
                                "tools": json.loads(r["tools_json"]),
                                "created_by": r["created_by"], "created_at": r["created_at"],
                            }
                            for r, s in ranked[:limit] if s > 0.15
                        ] or [
                            {
                                "id": r["id"], "scope": r["scope"],
                                "task_type": r["task_type"], "project": r["project"],
                                "note": r["note"],
                                "bundles": json.loads(r["bundles_json"]),
                                "tools": json.loads(r["tools_json"]),
                                "created_by": r["created_by"], "created_at": r["created_at"],
                            }
                            for r, _ in ranked[:limit]
                        ]
                        used_method = "embedding"
                except Exception:
                    pass  # fall back to empty results
            elif len(results) > 1:
                try:
                    from server.iso_embedder import IsoEmbedder
                    embedder = IsoEmbedder.get()
                    embedder.load_model()
                    qvec = embedder.encode(query)
                    import numpy as np
                    note_texts = [r["note"] for r in results]
                    note_vecs = embedder.embed_texts(note_texts)
                    scores = np.dot(note_vecs, qvec) / (
                        np.linalg.norm(note_vecs, axis=1) * np.linalg.norm(qvec) + 1e-10
                    )
                    ranked = sorted(
                        zip(results, scores),
                        key=lambda x: x[1], reverse=True,
                    )
                    results = [r for r, s in ranked if s > 0.15] or [r for r, _ in ranked]
                    used_method = "embedding"
                except Exception:
                    pass

        return {"results": results[:limit], "method": used_method, "total": len(results)}
    finally:
        conn.close()


def suggested_learnings(task_run_id: str, conn: Optional[sqlite3.Connection] = None) -> List[str]:
    owns_conn = conn is None
    if owns_conn:
        conn = _get_conn()
    try:
        run = conn.execute("SELECT task_text, cluster, mode FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        if not run:
            return []
        return [
            f"Remember cluster={run['cluster']} for similar tasks like: {run['task_text'][:120]}",
            f"Mode {run['mode']} used {EXECUTOR_SKILL} as the sole executor.",
            "Review the final tool trace to confirm which tools should stay auto-invokable.",
        ]
    finally:
        if owns_conn:
            conn.close()


def _close_routing_outcomes(
    conn: sqlite3.Connection,
    project: str = "default",
    ide: str = "unknown",
    max_rows: int = 500,
) -> Dict[str, Any]:
    """Close the feedback loop for routing_log entries that never got outcomes.

    Scans routing_log for entries with outcome=NULL or outcome='routed' that
    have a skill_name set. These represent successful skill invocations where
    the IDE used the routed skill but no outcome was recorded.

    This is the KEY fix for the 55% → 80%+ outcome coverage gap.

    Args:
        conn: Database connection
        project: Filter by project (default: all)
        ide: Filter by IDE (default: all)
        max_rows: Maximum rows to close in one batch

    Returns:
        Dict with closed count, breakdown by previous outcome, and any errors.
    """
    # Find unclosed entries with skill_name
    if project and project != "all":
        rows = conn.execute(
            """
            SELECT id, skill_name, task_summary, outcome, ide
            FROM routing_log
            WHERE skill_name IS NOT NULL AND skill_name != ''
              AND (outcome IS NULL OR outcome IN ('routed', 'pending'))
              AND project = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project, max_rows),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, skill_name, task_summary, outcome, ide
            FROM routing_log
            WHERE skill_name IS NOT NULL AND skill_name != ''
              AND (outcome IS NULL OR outcome IN ('routed', 'pending'))
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max_rows,),
        ).fetchall()

    if not rows:
        return {"status": "no_unclosed_entries", "closed": 0}

    breakdown: Dict[str, int] = {}
    errors: List[str] = []
    closed_ids = [r["id"] for r in rows]
    placeholders = ",".join("?" for _ in closed_ids)

    # Batch update routing_log outcomes
    conn.execute(
        f"UPDATE routing_log SET outcome='correct' WHERE id IN ({placeholders})",
        closed_ids,
    )

    # Batch update resource counters (times_correct, times_triggered)
    for row in rows:
        prev_outcome = row["outcome"] or "NULL"
        skill_name = row["skill_name"]
        breakdown[prev_outcome] = breakdown.get(prev_outcome, 0) + 1

        try:
            conn.execute(
                """
                UPDATE resources SET
                    times_correct = times_correct + 1,
                    times_triggered = times_triggered + 1,
                    last_used_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE name = ?
                """,
                (skill_name,),
            )
        except Exception as e:
            errors.append(f"Failed to update resource {skill_name}: {e}")

    conn.commit()

    return {
        "status": "closed",
        "closed": len(rows),
        "breakdown": breakdown,
        "errors": errors[:10],
    }


def review_task_run(task_run_id: str, verdict: str, comment: str = "") -> Dict[str, Any]:
    initialize_runtime()
    verdict = (verdict or "").strip().lower()
    if verdict not in {"correct", "wrong", "partial"}:
        return {"status": "error", "message": "verdict must be correct, wrong, or partial"}

    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM task_runs WHERE id = ?", (task_run_id,)).fetchone()
        if not run:
            return {"status": "error", "message": f"task_run {task_run_id} not found"}

        conn.execute(
            "INSERT INTO execution_reviews(task_run_id, verdict, comment) VALUES (?, ?, ?)",
            (task_run_id, verdict, comment),
        )
        conn.execute(
            "UPDATE task_runs SET review_status=?, updated_at=datetime('now') WHERE id=?",
            (verdict, task_run_id),
        )

        traces = conn.execute(
            "SELECT phase_id, subtask_name, resource_name, status FROM tool_traces WHERE task_run_id=? AND status='executed'",
            (task_run_id,),
        ).fetchall()

        learning_results = []
        for trace in traces:
            mapped = "correct" if verdict == "correct" else "wrong"
            try:
                result = record_outcome(
                    skill_name=trace["resource_name"],
                    outcome=mapped,
                    task_summary=f"{trace['phase_id']}: {trace['subtask_name']}",
                    project=run["project"],
                    ide=normalize_legacy_ide(run["ide_name"]),
                )
                learning_results.append({"resource_name": trace["resource_name"], "status": result.get("status", "logged")})
            except Exception as e:
                logger.warning(f"record_outcome failed for {trace['resource_name']}: {e}")
                learning_results.append({"resource_name": trace["resource_name"], "status": "error", "message": str(e)})

        # ── G-A1: Close unclosed routing_log entries for this project ──
        closure_result = _close_routing_outcomes(
            conn, project=run["project"], ide=normalize_legacy_ide(run["ide_name"])
        )
        if closure_result.get("closed", 0) > 0:
            logger.info(
                "Outcome closure: closed %d entries (%s)",
                closure_result["closed"],
                closure_result.get("breakdown", {}),
            )

        if comment.strip():
            conn.execute(
                """
                INSERT INTO user_learnings(scope, task_type, project, note, bundles_json, tools_json, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "task_run",
                    run["task_type"],
                    run["project"],
                    comment.strip(),
                    _json(json.loads(run["bundle_graph_json"]).get("domain", [])),
                    _json([trace["resource_name"] for trace in traces[:10]]),
                    "review",
                ),
            )

        if verdict in {"wrong", "partial"}:
            conn.execute(
                """
                INSERT INTO mistake_patterns(condition_type, condition_key, pattern_text, recovery_advice, source_task_run_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "cluster_mode",
                    f"{run['cluster']}::{run['mode']}",
                    comment.strip() or f"Run {task_run_id} was marked {verdict}",
                    "Use review reserve or change mode/bundle policy before retrying a similar task.",
                    task_run_id,
                ),
            )

        conn.commit()
        return {
            "status": "reviewed",
            "task_run_id": task_run_id,
            "verdict": verdict,
            "comment": comment,
            "learning_results": learning_results,
            "outcome_closure": closure_result,
        }
    finally:
        conn.close()
