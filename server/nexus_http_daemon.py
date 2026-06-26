#!/usr/bin/env python3
"""
NEXUS Persistent HTTP Daemon — eliminates 14s cold start by keeping
the embedding model and routing pipeline loaded in memory.

Endoints:
  GET  /health              — pipeline state + embedding model status
  POST /route               — route_task with optional embedding
  POST /route_v4             — full v4 Tool Attention pipeline
  POST /cluster_route        — cluster-first routing
  POST /cluster_activate     — manually activate clusters
  GET  /cluster_list         — list all domain clusters
  POST /find_skills          — find matching skills
  POST /report_outcome       — report skill outcome for learning
  GET  /learning_stats       — learning pipeline health report
  GET  /health_orchestrate   — full health orchestration
  GET  /health_check         — quick health snapshot

Based on: AUDIT-3 Phase 2a — Persistent Daemon + MCP Integration
Audit-4: All blocking sync calls now wrapped in run_in_executor to unblock event loop.
"""

import os
import sys
import json
import time
import sqlite3
import logging
import signal
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

# ── Paths ───────────────────────────────────────────────
SERVER_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SERVER_DIR.parent
DB_PATH = Path.home() / "nexus/db/nexus.db"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(SERVER_DIR))

# ── Logging ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [nexus-daemon] %(levelname)s: %(message)s",
)
logger = logging.getLogger("nexus-daemon")

# ── Global state ─────────────────────────────────────────
START_TIME = time.time()
EMBEDDING_LOADED = False
EMBEDDING_LOAD_TIME = 0.0
REQUEST_COUNT = 0
DB_AVAILABLE = False

# ── Thread pool for running sync functions off the event loop ──
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="nexus-worker")

async def _run_sync(func, *args, **kwargs):
    """Run a synchronous function in the thread pool without blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, lambda: func(*args, **kwargs))


# ── Pre-load embedding model ( singleton ) ──────────────
def preload_embeddings():
    """Load SentenceTransformer model once at startup — pays the 14s cold start once."""
    global EMBEDDING_LOADED, EMBEDDING_LOAD_TIME
    start = time.time()
    try:
        from tool_attention_v4 import TA_V4
        # Access the singleton to trigger lazy load
        _ = TA_V4.intent_router
        EMBEDDING_LOADED = True
        EMBEDDING_LOAD_TIME = time.time() - start
        logger.info(f"Embedding model loaded in {EMBEDDING_LOAD_TIME:.1f}s")
    except Exception as e:
        EMBEDDING_LOAD_TIME = time.time() - start
        logger.error(f"Embedding model failed: {e}")
        EMBEDDING_LOADED = False

def check_db():
    """Check if SQLite database is accessible."""
    global DB_AVAILABLE
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        DB_AVAILABLE = True
    except Exception:
        DB_AVAILABLE = False

def get_db():
    """Get a DB connection with WAL mode and vec extension."""
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


# ── FastAPI app ─────────────────────────────────────────
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="NEXUS Resource Manager",
    description="Persistent HTTP daemon for NEXUS v4 routing + learning pipeline",
    version="4.1.0",
)


@app.on_event("startup")
async def startup():
    """Pre-load heavy resources on daemon startup."""
    logger.info("NEXUS HTTP Daemon starting up...")
    check_db()
    # Pre-load embedding model in background thread so uvicorn starts fast
    t = threading.Thread(target=preload_embeddings, daemon=True)
    t.start()
    logger.info("Embedding model pre-load initiated in background")


# ── Health ──────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check: pipeline state + embedding model status."""
    uptime = time.time() - START_TIME
    return {
        "status": "ok" if DB_AVAILABLE and EMBEDDING_LOADED else "degraded",
        "uptime_seconds": round(uptime, 1),
        "embedding_model": "cached" if EMBEDDING_LOADED else "loading" if EMBEDDING_LOAD_TIME == 0 else "failed",
        "embedding_load_time_s": round(EMBEDDING_LOAD_TIME, 2),
        "db_available": DB_AVAILABLE,
        "total_requests": REQUEST_COUNT,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@app.get("/stats")
async def stats():
    """Detailed system stats: DB state, resource counts, routing logs."""
    if not DB_AVAILABLE:
        raise HTTPException(503, "Database not available")

    def _blocking():
        conn = get_db()
        try:
            resources = conn.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type").fetchall()
            logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
            degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
            plans = conn.execute("SELECT status, COUNT(*) FROM plans GROUP BY status").fetchall()
            return {
                "status": "healthy",
                "resources": {r[0]: r[1] for r in resources},
                "routing_logs_total": logs,
                "degraded_skills": degraded,
                "plans": {p[0]: p[1] for p in plans},
                "embedding_model": "cached" if EMBEDDING_LOADED else "failed",
                "total_requests": REQUEST_COUNT,
                "uptime_seconds": round(time.time() - START_TIME, 1),
            }
        finally:
            conn.close()

    try:
        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Stats error: {e}")


# ── Routing ─────────────────────────────────────────────

@app.post("/route")
async def route_task_endpoint(task: str, project: str = "default", use_embedding: bool = True):
    """Route a task through the v3 pipeline."""
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    start = time.time()
    try:
        from server.router import route_task as _route
        result = await _run_sync(_route, task, project, use_embedding=use_embedding)
        result["latency_ms"] = round((time.time() - start) * 1000, 1)
        return result
    except Exception as e:
        raise HTTPException(500, f"Routing error: {e}")

@app.post("/route_v4")
async def route_v4_endpoint(
    query: str,
    resource_types: Optional[str] = None,
    theta: float = 0.28,
    top_k: int = 10,
    use_clusters: bool = True,
    max_clusters: int = 2,
):
    """NEXUS v4: Full Tool Attention pipeline."""
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    start = time.time()
    try:
        from tool_attention_v4 import route_v4 as _route_v4

        def _blocking_v4():
            result_json = _route_v4(query, resource_types, theta, top_k,
                                    use_clusters=use_clusters,
                                    max_clusters=max_clusters)
            return json.loads(result_json) if isinstance(result_json, str) else result_json

        result = await _run_sync(_blocking_v4)
        result["daemon_latency_ms"] = round((time.time() - start) * 1000, 1)
        return result
    except Exception as e:
        raise HTTPException(500, f"v4 routing error: {e}")

@app.post("/cluster_route")
async def cluster_route_endpoint(task: str, project: str = "default", max_clusters: int = 2):
    """Route through domain clusters first, then search within."""
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    try:
        def _blocking():
            from cluster_router import ClusterRouter
            router = ClusterRouter(str(DB_PATH))
            result = router.route_to_clusters(task, max_clusters=max_clusters, project=project)

            from auto_trigger import AutoTrigger
            trigger = AutoTrigger()
            signal_result = trigger.route_task(task, max_clusters=max_clusters)

            return {
                "query": task,
                "activated_clusters": result.get("activated_clusters", []),
                "total_resources_scanned": result.get("total_resources_scanned", 0),
                "fallback": result.get("fallback", False),
                "latency_ms": result.get("latency_ms", 0),
                "auto_trigger": {
                    "default_skills": signal_result.get("default_skills", []),
                    "default_agents": signal_result.get("default_agents", []),
                },
            }

        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Cluster route error: {e}")


@app.post("/cluster_activate")
async def cluster_activate_endpoint(clusters: str, project: str = "default"):
    """Manually activate specific clusters by name."""
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    try:
        def _blocking():
            from auto_trigger import AutoTrigger
            from cluster_router import ClusterRouter
            trigger = AutoTrigger()
            cluster_names = [c.strip() for c in clusters.split(",")]
            activation = trigger.activate_clusters(cluster_names)

            router = ClusterRouter(str(DB_PATH))
            resource_ids = router._get_cluster_faiss().get_cluster_resource_ids(cluster_names)
            stats = router._get_cluster_faiss().get_cluster_stats()

            return {
                "clusters": activation.get("clusters", {}),
                "total_resources": len(resource_ids),
                "resource_counts": {c: stats.get(c, 0) for c in cluster_names},
                "default_skills": activation.get("default_skills", []),
                "default_agents": activation.get("default_agents", []),
            }

        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Cluster activate error: {e}")


@app.get("/cluster_list")
async def cluster_list_endpoint():
    """List all domain clusters with resource counts."""
    if not DB_AVAILABLE:
        raise HTTPException(503, "Database not available")

    def _blocking():
        from cluster_config import CLUSTER_DEFINITIONS, DEFAULT_SKILL_MAP, DEFAULT_AGENT_MAP
        from auto_trigger import TRIGGER_PATTERNS

        conn = get_db()
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
                "has_centroid": False,
                "auto_activate_threshold": 0.3,
            })
        conn.close()

        return {
            "clusters": clusters,
            "total_clusters": len(clusters),
            "total_resources": sum(c["resource_count"] for c in clusters),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    try:
        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Cluster list error: {e}")


# ── Skills ───────────────────────────────────────────────

@app.post("/find_skills")
async def find_skills_endpoint(task: str, project: str = "default", max_results: int = 5):
    """Route a task to the best model tier and find matching skills."""
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    start = time.time()
    try:
        def _blocking():
            from server.router import route_task as _route
            result = _route(task, project, use_embedding=True)

            # Log to routing_log
            if DB_AVAILABLE:
                try:
                    conn = get_db()
                    conn.execute("""
                        INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                               matched_skills, outcome, skill_name, ide)
                        VALUES(?,?,?,?,?,NULL,?,?)
                    """, (
                        task, project, result.get('tier', ''), result.get('model', ''),
                        json.dumps([s['name'] for s in result.get('skills', [])]),
                        result.get('skills', [{}])[0].get('name') if result.get('skills') else None,
                        'unknown'
                    ))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

            result["skills"] = result.get("skills", [])[:max_results]
            result["latency_ms"] = round((time.time() - start) * 1000, 1)
            return result

        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Find skills error: {e}")


# ── Learning ────────────────────────────────────────────

@app.post("/report_outcome")
async def report_outcome_endpoint(
    skill_name: str,
    outcome: str,
    task_summary: str = "",
    project: str = "default",
):
    """Report whether a skill/tool recommendation was correct or wrong."""
    global REQUEST_COUNT
    REQUEST_COUNT += 1
    try:
        from learning import record_outcome
        result = await _run_sync(record_outcome, skill_name, outcome, task_summary, project, "http", 0)

        # Bridge to OutcomeLog (server.rl.outcome_log) so the RL feedback table
        # is populated. Without this, /report_outcome updates routing_log + resource
        # counters but never inserts into outcome_log — the Thompson Sampling and
        # cluster_affinity layers only read from outcome_log. Bug fix 2026-06-26.
        try:
            from server.rl.outcome_log import OutcomeLog
            from pathlib import Path as _Path
            from server import efc as _efc
            db_path = _Path(__file__).parent.parent / "db" / "nexus.db"

            def _bridge():
                with OutcomeLog(str(db_path)) as log:
                    # Resolve resource_id from skill_name
                    row = log.conn.execute(
                        "SELECT id, cluster FROM resources WHERE name = ?",
                        (skill_name,),
                    ).fetchone()
                    if not row:
                        return None
                    resource_id, cluster = row

                    # Compute EFC factors (arXiv:2605.29682)
                    repeat_count = _efc.compute_repeat_count_1h(
                        log.conn, task_summary or "", skill_name
                    )
                    first_use = _efc.is_first_use_on_task(
                        log.conn, task_summary or "", skill_name
                    )
                    action_type = _efc.classify_action_type(
                        task_summary or "", []
                    )
                    factors = _efc.compute_efc(
                        outcome=outcome,
                        action_type=action_type,
                        repeat_count_1h=repeat_count,
                        is_first_use_on_task=first_use,
                    )

                    return log.record(
                        resource_id=resource_id,
                        cluster_id=cluster,
                        task_intent=task_summary or "",
                        outcome=outcome,
                        duration_ms=0,
                        alternative_resource_ids=[],
                        notes=f"bridged from /report_outcome on 2026-06-26 (EFC={factors['efc']:.3f}, NRS-EFC={factors['nrs_efc']:.3f})",
                        session_id="http-bridge",
                    ), log.conn.execute(
                        """UPDATE outcome_log
                           SET action_type = ?, repeat_count_1h = ?,
                               efc_informativeness = ?, efc_validity = ?,
                               efc_redundancy = ?, efc_memory = ?, efc_score = ?
                           WHERE id = last_insert_rowid()""",
                        (
                            action_type, repeat_count,
                            factors["I"], factors["V"], factors["R"], factors["M"],
                            factors["efc"],
                        ),
                    )
            bridge_result = await _run_sync(_bridge)
        except Exception as bridge_err:
            # Don't fail the whole request if bridge fails; just include in result
            if isinstance(result, dict):
                result["outcome_log_bridge_error"] = str(bridge_err)

        return result
    except Exception as e:
        raise HTTPException(500, f"Report outcome error: {e}")

@app.get("/learning_stats")
async def learning_stats_endpoint():
    """Full learning pipeline health report."""
    try:
        from learning import get_learning_stats
        return await _run_sync(get_learning_stats)
    except Exception as e:
        raise HTTPException(500, f"Learning stats error: {e}")

@app.get("/cluster_affinities")
async def cluster_affinities_endpoint():
    """Get learned cluster affinity scores."""
    if not DB_AVAILABLE:
        raise HTTPException(503, "Database not available")

    def _blocking():
        from learning import get_cluster_affinity_scores
        conn = get_db()
        try:
            scores = get_cluster_affinity_scores(conn)
            return {
                "cluster_affinities": scores,
                "interpretation": "Higher = more reliable. Auto-learned from outcomes.",
            }
        finally:
            conn.close()

    try:
        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Affinity error: {e}")


# ── Health Orchestration ────────────────────────────────

@app.get("/health_orchestrate")
async def health_orchestrate_endpoint(task: str = "health check and repair", auto_repair: bool = True):
    """Diagnose and repair all resource health issues."""
    try:
        from nexus_health_orchestrator import orchestrate as _orchestrate

        def _blocking():
            result = _orchestrate(task, auto_repair)
            if isinstance(result, str):
                return json.loads(result)
            return result

        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Orchestrate error: {e}")


@app.get("/health_check")
async def health_check_endpoint():
    """Quick health snapshot without mutations."""
    try:
        from nexus_health_orchestrator import health_check as _hc

        def _blocking():
            result = _hc()
            if isinstance(result, str):
                return json.loads(result)
            return result

        return await _run_sync(_blocking)
    except Exception as e:
        raise HTTPException(500, f"Health check error: {e}")


# ── Graceful shutdown ────────────────────────────────────

def shutdown_handler(signum, frame):
    logger.info("Shutdown signal received, exiting...")
    _EXECUTOR.shutdown(wait=False)
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# ── Main ────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS Persistent HTTP Daemon")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--workers", type=int, default=1, help="Uvicorn workers (default: 1, use run_in_executor for concurrency)")
    args = parser.parse_args()

    logger.info(f"Starting NEXUS HTTP Daemon on {args.host}:{args.port} (workers={args.workers})")
    logger.info(f"DB path: {DB_PATH}")
    logger.info(f"Server dir: {SERVER_DIR}")

    # Workers > 1 requires import string and creates N independent processes
    # (N model loads, N DB connections). Workers=1 + run_in_executor = 1 model, concurrent requests.
    uvicorn.run(
        "nexus_http_daemon:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_level="info",
        access_log=True,
    )