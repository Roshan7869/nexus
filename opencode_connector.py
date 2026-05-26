#!/usr/bin/env python3
"""
nexus/opencode_connector.py
NEXUS v3 OpenCode Integration — Auto-connector for session startup

Usage in OpenCode:
    import nexus_connector as nexus
    nexus.find_skills("debug memory leak")
    nexus.route_v3("deploy django app")
    nexus.health()
"""

import sys
import os
from pathlib import Path

# --- Auto-detect and inject paths ---
NEXUS_HOME = Path.home() / "nexus"
VENV_SITE = NEXUS_HOME / "venv/lib/python3.12/site-packages"

# Inject venv site-packages (for faiss, torch, transformers, etc.)
if str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

# Inject nexus root only. Adding nexus/server directly collides with
# core/server.py and breaks package imports in some execution contexts.
if str(NEXUS_HOME) not in sys.path:
    sys.path.insert(0, str(NEXUS_HOME))

# --- Lazy imports (fail gracefully if not available) ---
try:
    from server.router import route_task
except ImportError as e:
    route_task = None
    _router_error = e

try:
    from server.tool_attention import route_v3
except ImportError as e:
    route_v3 = None
    _tool_attention_error = e

try:
    import faiss
    FAISS_VERSION = faiss.__version__
except ImportError:
    faiss = None
    FAISS_VERSION = None

import sqlite3
import json
try:
    from server.broker_runtime import (
        create_task_run,
        get_task_run,
        review_task_run,
        add_user_learning,
        search_user_learnings,
    )
except ImportError:
    create_task_run = None
    get_task_run = None
    review_task_run = None
    add_user_learning = None
    search_user_learnings = None

# --- Constants ---
DB_PATH = NEXUS_HOME / "db/nexus.db"

# --- Public API ---

def _ensure_paths():
    """Ensure Python paths are set (idempotent)."""
    if str(VENV_SITE) not in sys.path:
        sys.path.insert(0, str(VENV_SITE))
    if str(NEXUS_HOME) not in sys.path:
        sys.path.insert(0, str(NEXUS_HOME))

def find_skills(task: str, project: str = "default", max_results: int = 5) -> dict:
    """Find matching skills for a task using v2 routing."""
    _ensure_paths()
    if route_task is None:
        raise ImportError(f"router module not importable: {_router_error}")
    result = route_task(task, project, use_embedding=True)
    result['skills'] = result.get('skills', [])[:max_results]
    return result

def autopilot(task: str, project: str = "default", mode: str = "safemode",
              ide_name: str = "opencode", auto_execute: bool = True) -> dict:
    """Run a task through the adaptive-imagining-cat broker/runtime."""
    _ensure_paths()
    if create_task_run is None:
        raise ImportError("broker_runtime module not importable")
    return create_task_run(
        task,
        ide_name=ide_name,
        mode=mode,
        project=project,
        cwd=str(Path.cwd()),
        repo_context={"connector": "opencode"},
        auto_execute=auto_execute,
    )

def task_run(task_run_id: str) -> dict:
    _ensure_paths()
    if get_task_run is None:
        raise ImportError("broker_runtime module not importable")
    return get_task_run(task_run_id)

def review_run(task_run_id: str, verdict: str, comment: str = "") -> dict:
    _ensure_paths()
    if review_task_run is None:
        raise ImportError("broker_runtime module not importable")
    return review_task_run(task_run_id, verdict, comment)

def add_learning(note: str, project: str = "default", task_type: str = "general",
                 bundles: list | None = None, tools: list | None = None) -> dict:
    _ensure_paths()
    if add_user_learning is None:
        raise ImportError("broker_runtime module not importable")
    return add_user_learning(
        note,
        scope="task_type",
        task_type=task_type,
        project=project,
        bundles=bundles or [],
        tools=tools or [],
        created_by="opencode-user",
    )

def search_learnings(query: str = "", project: str = "default") -> dict:
    _ensure_paths()
    if search_user_learnings is None:
        raise ImportError("broker_runtime module not importable")
    return search_user_learnings(query=query, project=project)

def find_skills_v3(task: str, project: str = "default") -> dict:
    """Find matching skills using v3 Tool Attention (ISO + FAISS)."""
    _ensure_paths()
    if route_v3 is None:
        raise ImportError(f"tool_attention module not importable: {_tool_attention_error}")
    return route_v3(task, project)

def health() -> dict:
    """Return NEXUS health status."""
    _ensure_paths()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    resources = conn.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type").fetchall()
    logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
    conn.close()
    return {
        "status": "healthy",
        "version": "v3",
        "db_path": str(DB_PATH),
        "resources": {r[0]: r[1] for r in resources},
        "routing_logs_total": logs,
        "degraded_skills": degraded,
        "faiss_available": FAISS_VERSION is not None,
        "faiss_version": FAISS_VERSION,
    }

def list_resources(resource_type: str = "all") -> list:
    """List indexed resources by type."""
    _ensure_paths()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if resource_type == "all":
        rows = conn.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type").fetchall()
        conn.close()
        return [{"type": r[0], "count": r[1]} for r in rows]
    else:
        rows = conn.execute("SELECT name, description, tier, status FROM resources WHERE resource_type=? LIMIT 50", (resource_type,)).fetchall()
        conn.close()
        return [{"name": r[0], "description": r[1], "tier": r[2], "status": r[3]} for r in rows]

def verify() -> dict:
    """Verify all NEXUS components are working."""
    results = {
        "db_connected": False,
        "router_importable": False,
        "tool_attention_importable": False,
        "faiss_available": False,
        "mcp_daemon_running": False,
        "routing_works": False,
    }
    
    # DB
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.execute("SELECT COUNT(*) FROM resources")
        results["db_connected"] = c.fetchone()[0] > 0
        conn.close()
    except Exception as e:
        results["db_error"] = str(e)
    
    # Router
    try:
        from server.router import route_task
        results["router_importable"] = True
    except Exception as e:
        results["router_error"] = str(e)
    
    # Tool Attention
    try:
        from server.tool_attention import route_v3
        results["tool_attention_importable"] = True
    except Exception as e:
        results["tool_attention_error"] = str(e)
    
    # FAISS
    try:
        import faiss
        results["faiss_available"] = True
        results["faiss_version"] = faiss.__version__
    except Exception as e:
        results["faiss_error"] = str(e)
    
    # MCP Daemon
    import subprocess
    pid = subprocess.run(["pgrep", "-f", "mcp_sync.py --daemon"], capture_output=True, text=True)
    results["mcp_daemon_running"] = bool(pid.stdout.strip())
    
    # Routing test
    try:
        from server.router import route_task
        r = route_task("test connection", "default", use_embedding=True)
        results["routing_works"] = r["tier"] >= 0
    except Exception as e:
        results["routing_error"] = str(e)
    
    results["all_ok"] = all([
        results["db_connected"],
        results["router_importable"],
        results["tool_attention_importable"],
        results["faiss_available"],
        results["mcp_daemon_running"],
        results["routing_works"],
    ])
    
    return results

# --- Convenience aliases ---
connect = verify  # alias for verify()

if __name__ == "__main__":
    import pprint
    print("NEXUS v3 OpenCode Connector")
    print("Verification Results:")
    pprint.pprint(verify())
