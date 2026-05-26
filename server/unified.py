#!/usr/bin/env python3
"""
NEXUS Unified Server — single entry point for LLM Gateway + MCP Orchestration

Architecture:
  Port 8000 (FastAPI):
    /v1/chat/completions     → LLM proxy (Ollama Cloud / NVIDIA NIM)
    /v1/models              → list available models
    /nexus/route            → unified routing (v4 pipeline)
    /nexus/event            → IDE feedback endpoint
    /nexus/beliefs          → Thompson Sampling beliefs
    /nexus/reflection       → self-reflection stats
    /nexus/health           → full system health
    /health                 → liveness probe
    /approaches             → reasoning approaches

  MCP Server (stdio):
    All MCP tools from nexus-mcp-enhanced.py

This unifies System A (MCP server with 1,097 resources) and
System B (LLM Gateway with Thompson Sampling) into a single process.
"""

import os, sys, json, time, logging, asyncio, threading
from pathlib import Path

# ── Add paths ────────────────────────────────────────────────────────────
NEXUS_ROOT = Path.home() / 'nexus'
sys.path.insert(0, str(NEXUS_ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ── Feature Flags — imported from centralized config ────────────────────
from core.config import get_learning_config
_LEARN_CFG = get_learning_config()
THOMPSON_ROUTING = _LEARN_CFG.is_thompson_routing()
CLUSTER_ROUTING = _LEARN_CFG.is_cluster_routing()
REFLECTION_ENABLED = _LEARN_CFG.is_reflection_enabled()
MCP_SYNC_ENABLED = _LEARN_CFG.is_mcp_sync()

# ── Embedding Model State ───────────────────────────────────────────────
EMBEDDING_LOADED = False
EMBEDDING_LOAD_TIME = 0.0

def preload_embeddings():
    global EMBEDDING_LOADED, EMBEDDING_LOAD_TIME
    start = time.time()
    try:
        from server.tool_attention_v4 import TA_V4
        _ = TA_V4.intent_router
        EMBEDDING_LOADED = True
        EMBEDDING_LOAD_TIME = time.time() - start
        logger.info(f"Embedding model loaded in {EMBEDDING_LOAD_TIME:.1f}s")
    except Exception as e:
        EMBEDDING_LOAD_TIME = time.time() - start
        logger.warning(f"Embedding model pre-warm failed: {e}")
        EMBEDDING_LOADED = False

# ── Load Config ──────────────────────────────────────────────────────────
from core.config import NexusConfig

config_path = NEXUS_ROOT / 'nexus.yaml'
config = NexusConfig.from_yaml(str(config_path)) if config_path.exists() else NexusConfig()

# ── Initialize Subsystems ────────────────────────────────────────────────
learner = None
event_collector = None
metrics_store = None

if THOMPSON_ROUTING:
    try:
        from core.learner import NexusLearner, IDEEventCollector
        learner = NexusLearner()
        event_collector = IDEEventCollector(learner)
        logger.info("Thompson Sampling learner initialized")
    except Exception as e:
        logger.warning(f"Learner init failed: {e}")

try:
    from core.metrics import MetricsStore
    metrics_store = MetricsStore()
except Exception:
    pass

try:
    from server.router import route_task, find_matching_skills
except Exception as e:
    logger.warning(f"Router import failed: {e}")
    route_task = None
    find_matching_skills = None

# ── Cached MCP module (lazy-loaded, kept in memory) ──
_mcp_module = None

# ── FastAPI App ──────────────────────────────────────────────────────────
app = FastAPI(
    title="NEXUS Unified Server",
    description="Unified LLM Gateway + MCP Orchestration + Self-Learning",
    version="4.0.0",
)


# ── Models ───────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    model: str
    messages: List[Dict]
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False
    approach: Optional[str] = None
    task_type: Optional[str] = None


class IDEEventRequest(BaseModel):
    ide: str
    event_type: str
    model: str
    provider: str = "ollama_cloud"
    task_type: str = "general"
    reward: Optional[float] = None
    metadata: Dict = {}


class ReportOutcomeRequest(BaseModel):
    """Request model for reporting routing outcomes."""
    skill_name: str
    outcome: str  # "correct" or "wrong"
    task_summary: str = ""
    project: str = "default"
    ide: str = "unknown"


class RouteRequest(BaseModel):
    task: str
    project: str = "default"
    max_results: int = 5


class AutopilotRunRequest(BaseModel):
    task: str
    project: str = "default"
    ide_name: str = "unknown"
    mode: str = "safemode"
    cwd: str = ""
    repo_context: Dict[str, Any] = {}
    auto_execute: bool = True


class TaskReviewRequest(BaseModel):
    verdict: str
    comment: str = ""


class LearningNoteRequest(BaseModel):
    note: str
    scope: str = "task_type"
    task_type: str = "general"
    project: str = "default"
    bundles: List[str] = []
    tools: List[str] = []
    created_by: str = "user"


# ═══════════════════════════════════════════════════════════════════════
# LLM GATEWAY ENDPOINTS (System B)
# ═══════════════════════════════════════════════════════════════════════

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    """OpenAI-compatible chat completions endpoint."""
    start = time.time()

    # Classify task for Thompson Sampling
    task_type = req.task_type or "general"
    if not req.task_type and req.messages:
        last_msg = req.messages[-1].get("content", "")
        if last_msg and route_task:
            try:
                route_result = route_task(last_msg[:200], "default")
                task_type = route_result.get("task_type", "general")
            except Exception:
                pass

    # Resolve model via Thompson Sampling if enabled
    model = req.model
    if THOMPSON_ROUTING and learner:
        try:
            if learner.can_route(task_type):
                ts_result = learner.thompson_sample(
                    [{"provider": "ollama_cloud", "model": m}
                     for m in ["deepseek-v4-flash", "kimi-k2", "glm-5.1",
                                "qwen3-coder:480b", "devstral-small-2:24b"]],
                    task_type
                )
                if ts_result:
                    model = ts_result["model"]
                    logger.info(f"Thompson selected: {model} for {task_type}")
        except Exception:
            pass

    # Build provider map and dispatch to chat_completions (FIX: was .complete())
    # NOTE: Moved OUTSIDE THOMPSON_ROUTING block - should always run
    try:
        from providers.registry import build_provider_map
        provider_map = build_provider_map(config)
        provider_name = _model_to_provider(model, provider_map)
        provider = provider_map.get(provider_name)
        if not provider:
            raise HTTPException(400, f"No provider for model {model}")

        result = await provider.chat_completions(  # FIX: call chat_completions() not complete()
            model=model,
            messages=req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=req.stream,
            approach=req.approach,
        )

        latency_ms = (time.time() - start) * 1000

        # Record metrics + implicit signal
        if metrics_store:
            try:
                from core.metrics import RequestMetrics
                from uuid import uuid4
                usage = result.get("usage", {})
                metrics_store.log(RequestMetrics(
                    request_id=str(uuid4()),
                    model=model,
                    provider=provider_name,
                    task_type=task_type,
                    latency_ms=latency_ms,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    timestamp=time.time(),
                    success=True,
                ))
            except Exception:
                pass

        if THOMPSON_ROUTING and learner:
            try:
                learner.record_implicit_signal(
                    provider_name, model, task_type,
                    latency_ms, result.get("usage", {}).get("total_tokens", 0), True
                )
            except Exception:
                pass

        if req.stream:
            return StreamingResponse(result, media_type="text/event-stream")
        return JSONResponse(result)

    except HTTPException:
        raise
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        provider_name = "unknown"
        if THOMPSON_ROUTING and learner and learner.can_route(task_type):
            try:
                learner.record_implicit_signal(provider_name, model, task_type, latency_ms, 0, False)
            except Exception:
                pass

        # ── Phase 6: Reflection on LLM failure ──
        if REFLECTION_ENABLED:
            try:
                from server.reflection_loop import reflect_and_retry
                retry = reflect_and_retry(
                    failed_tool=model, error=str(e), query=str(req.messages[-1].get("content", "")),
                    candidates=[{"name": m, "score": 0.5, "resource_type": "model"}
                                for m in ["deepseek-v4-flash", "kimi-k2", "glm-5.1"]]
                )
                if retry.success:
                    return JSONResponse(retry.final_result)
            except Exception:
                pass

        raise HTTPException(500, f"LLM proxy error: {e}")


def _model_to_provider(model: str, provider_map: dict) -> str:
    """Route model name to provider using config model lists."""
    # First, check config for explicit model→provider mapping
    try:
        all_models = config.get_all_models()
        for prov_name, model_name, _role in all_models:
            if model_name == model or model_name.endswith(":" + model.split(":")[-1]) if ":" in model else model_name == model:
                return prov_name
    except Exception:
        pass

    # Fallback: string matching
    if "nvidia" in model or model.startswith("meta/") or model.startswith("nvidia/"):
        return "nvidia_nim"
    return "ollama_cloud"


@app.get("/v1/models")
async def list_models():
    """List all available models across providers."""
    models = []
    try:
        from providers.registry import build_provider_map
        provider_map = build_provider_map(config)
        for name, provider in provider_map.items():
            try:
                provider_models = await provider.list_models()
                for m in provider_models:
                    models.append({
                        "id": m if "/" not in m else m.split("/")[-1],
                        "object": "model",
                        "provider": name,
                        "owned_by": name,
                    })
            except Exception:
                pass
    except Exception:
        # Static fallback
        models = [
            {"id": "deepseek-v4-flash", "provider": "ollama_cloud"},
            {"id": "kimi-k2", "provider": "ollama_cloud"},
            {"id": "glm-5.1", "provider": "ollama_cloud"},
            {"id": "qwen3-coder:480b", "provider": "ollama_cloud"},
            {"id": "devstral-small-2:24b", "provider": "ollama_cloud"},
            {"id": "gemini-3-flash-preview", "provider": "ollama_cloud"},
        ]
    return JSONResponse({"object": "list", "data": models})


# ═══════════════════════════════════════════════════════════════════════
# NEXUS ORCHESTRATION ENDPOINTS (System A → B bridge)
# ═══════════════════════════════════════════════════════════════════════

@app.post("/nexus/route")
async def nexus_route(req: RouteRequest):
    """Unified v4 routing: auto_trigger → cluster → ISO → Thompson."""
    if not route_task:
        raise HTTPException(500, "Router not available")
    result = route_task(req.task, req.project, use_embedding=True)
    result["skills"] = result.get("skills", [])[:req.max_results]
    return JSONResponse(result)


@app.post("/nexus/search")
async def nexus_search(request: Request):
    """Search NEXUS resources by query. Uses keyword + fuzzy matching."""
    body = await request.json()
    query = body.get("query", body.get("task", ""))
    project = body.get("project", "default")
    max_results = body.get("max_results", 10)

    if not query:
        raise HTTPException(400, "Missing 'query' or 'task' field")

    # Direct keyword match — fast, no heavy imports
    if find_matching_skills:
        skills = find_matching_skills(query, project)
        return JSONResponse({"query": query, "method": "keyword_match", "results": skills[:max_results]})

    # If router not loaded, do a simple DB search
    import sqlite3
    from pathlib import Path as P
    db = P.home() / "nexus/db/nexus.db"
    if not db.exists():
        raise HTTPException(500, "Database not found")

    conn = sqlite3.connect(str(db), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        words = query.lower().split()
        results = []
        rows = conn.execute(
            "SELECT id, name, description, resource_type, cluster, times_triggered, times_correct FROM resources WHERE status='active' ORDER BY times_correct DESC LIMIT 200"
        ).fetchall()
        for row in rows:
            text = f"{row['name']} {row['description'] or ''}".lower()
            if any(w in text for w in words if len(w) >= 3) or any(w in text.split() for w in words):
                results.append({
                    "id": row["id"], "name": row["name"], "description": row["description"] or "",
                    "type": row["resource_type"], "cluster": row["cluster"] or "unassigned",
                    "triggered": row["times_triggered"] or 0, "successes": row["times_correct"] or 0,
                })
        return JSONResponse({"query": query, "method": "db_scan", "results": results[:max_results]})
    finally:
        conn.close()


@app.get("/nexus/stats")
async def nexus_stats():
    """Overview of NEXUS resource stats."""
    stats = {
        "version": "4.0.0-unified",
        "routing": {
            "thompson": THOMPSON_ROUTING,
            "cluster": CLUSTER_ROUTING,
            "reflection": REFLECTION_ENABLED,
            "mcp_sync": MCP_SYNC_ENABLED,
        },
    }
    try:
        from core.config import NexusConfig
        all_models = config.get_all_models()
        stats["providers"] = len(config.providers)
        stats["models"] = len(all_models)
        stats["model_details"] = [{"provider": p, "name": m, "role": r} for p, m, r in all_models]
    except Exception:
        stats["providers"] = "error"
    return JSONResponse(stats)


@app.get("/nexus/resources")
async def nexus_resources(type: str = None, cluster: str = None, limit: int = 50):
    """List NEXUS resources (skills, agents, tools, etc). Optional filters: type, cluster."""
    import sqlite3
    from pathlib import Path as P
    db = P.home() / "nexus/db/nexus.db"
    if not db.exists():
        raise HTTPException(500, "Database not found")

    conn = sqlite3.connect(str(db), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT id, name, description, resource_type, cluster, status, times_triggered, times_correct FROM resources WHERE 1=1"
        params = []
        if type:
            query += " AND resource_type = ?"
            params.append(type)
        if cluster:
            query += " AND cluster = ?"
            params.append(cluster)

        # Only active resources by default
        query += " AND status = 'active' ORDER BY times_correct DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        resources = [
            {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"] or "",
                "type": r["resource_type"],
                "cluster": r["cluster"] or "unassigned",
                "status": r["status"],
                "triggered": r["times_triggered"] or 0,
                "successes": r["times_correct"] or 0,
            }
            for r in rows
        ]
        # Summary counts
        counts = dict(conn.execute(
            "SELECT resource_type, COUNT(*) FROM resources WHERE status='active' GROUP BY resource_type"
        ).fetchall())

        return JSONResponse({
            "total": len(resources),
            "filters": {"type": type, "cluster": cluster},
            "counts_by_type": counts,
            "resources": resources,
        })
    finally:
        conn.close()


@app.post("/nexus/autopilot/run")
async def nexus_autopilot_run(req: AutopilotRunRequest):
    """Adaptive-imagining-cat broker entrypoint for TUI agents."""
    from server.broker_runtime import create_task_run
    result = create_task_run(
        req.task,
        ide_name=req.ide_name,
        mode=req.mode,
        project=req.project,
        cwd=req.cwd,
        repo_context=req.repo_context,
        auto_execute=req.auto_execute,
    )
    return JSONResponse(result)


@app.get("/nexus/task-runs/{task_run_id}")
async def nexus_task_run(task_run_id: str):
    from server.broker_runtime import get_task_run
    result = get_task_run(task_run_id)
    if result.get("status") == "error":
        raise HTTPException(404, result["message"])
    return JSONResponse(result)


@app.post("/nexus/task-runs/{task_run_id}/review")
async def nexus_task_run_review(task_run_id: str, req: TaskReviewRequest):
    from server.broker_runtime import review_task_run
    result = review_task_run(task_run_id, req.verdict, req.comment)
    if result.get("status") == "error":
        raise HTTPException(400, result["message"])
    return JSONResponse(result)


@app.post("/nexus/learnings")
async def nexus_add_learning(req: LearningNoteRequest):
    from server.broker_runtime import add_user_learning
    result = add_user_learning(
        req.note,
        scope=req.scope,
        task_type=req.task_type,
        project=req.project,
        bundles=req.bundles,
        tools=req.tools,
        created_by=req.created_by,
    )
    return JSONResponse(result)


@app.get("/nexus/learnings")
async def nexus_get_learnings(query: str = "", project: str = "default", limit: int = 20):
    from server.broker_runtime import search_user_learnings
    return JSONResponse(search_user_learnings(query=query, project=project, limit=limit))


@app.post("/nexus/event")
async def nexus_event(req: IDEEventRequest):
    """Record IDE feedback event for Thompson Sampling."""
    if not THOMPSON_ROUTING or not learner or not event_collector:
        return JSONResponse({"status": "disabled", "thompson_routing": False})

    try:
        from core.ide_events import create_event
        event = create_event(
            ide=req.ide,
            model=req.model,
            provider=req.provider,
            task_type=req.task_type,
            event_type=req.event_type,
            reward=req.reward or 0.0,
            metadata=req.metadata,
        )
        result = event_collector.receive_event(event)
        return JSONResponse({"status": "recorded", "model": req.model, "reward": event.reward, **result})
    except Exception as e:
        raise HTTPException(500, f"Event recording failed: {e}")


@app.post("/nexus/report-outcome")
async def nexus_report_outcome(req: ReportOutcomeRequest):
    """
    Report the outcome of a routing decision — closes the feedback loop.

    This is the PRIMARY feedback entry point for HTTP clients (IDEs, scripts).
    Calls into learning.py's record_outcome() which triggers:
      Layer 1: Outcome tracking (update resource counters)
      Layer 2: Thompson Sampling belief update (if enabled)
      Layer 3: Self-reflection on failure (if enabled)
      Layer 5: Cluster affinity update (if enabled)
    """
    from server.learning import record_outcome
    result = record_outcome(
        skill_name=req.skill_name,
        outcome=req.outcome,
        task_summary=req.task_summary,
        project=req.project,
        ide=req.ide,
    )
    return JSONResponse(result)


@app.get("/nexus/beliefs")
async def nexus_beliefs(task_type: str = None):
    """View current Thompson Sampling beliefs."""
    if not learner:
        return JSONResponse({"status": "learner_not_initialized"})

    try:
        stats = learner.get_stats()
        return JSONResponse(stats)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/nexus/reflection")
async def nexus_reflection():
    """View self-reflection statistics."""
    try:
        from server.reflection_loop import ReflectionLoop
        loop = ReflectionLoop()
        stats = loop.get_failure_stats()
        loop.close()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e), "enabled": REFLECTION_ENABLED})


@app.get("/nexus/learning-stats")
async def nexus_learning_stats():
    """Full learning loop statistics."""
    stats = {"thompson_routing": THOMPSON_ROUTING, "cluster_routing": CLUSTER_ROUTING,
             "reflection_enabled": REFLECTION_ENABLED, "mcp_sync_enabled": MCP_SYNC_ENABLED}

    if learner:
        try:
            stats["thompson"] = learner.get_stats()
        except Exception:
            pass

    try:
        from server.reflection_loop import ReflectionLoop
        loop = ReflectionLoop()
        stats["reflection"] = loop.get_failure_stats()
        loop.close()
    except Exception:
        pass

    try:
        from server.event_bridge import get_bridge
        bridge = get_bridge()
        stats["event_bridge"] = bridge.get_learning_stats()
    except Exception:
        pass

    return JSONResponse(stats)


@app.get("/nexus/health")
async def nexus_health():
    """Full system health combining MCP + LLM + Learning."""
    import sqlite3
    db_path = NEXUS_ROOT / 'db/nexus.db'

    health = {
        "status": "healthy",
        "version": "4.0.0-unified",
        "features": {
            "thompson_routing": THOMPSON_ROUTING,
            "cluster_routing": CLUSTER_ROUTING,
            "reflection": REFLECTION_ENABLED,
            "mcp_sync": MCP_SYNC_ENABLED,
        },
        "subsystems": {},
    }

    # DB health — comprehensive routing metrics
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")

        total_active = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
        triggered = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active' AND times_triggered > 0").fetchone()[0]
        resources_by_type = conn.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type").fetchall()
        total_logs = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
        fallbacks = conn.execute("SELECT COUNT(*) FROM routing_log WHERE matched_tier = 1").fetchone()[0]
        with_outcome = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IS NOT NULL").fetchone()[0]
        null_outcome = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IS NULL").fetchone()[0]
        correct = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='correct'").fetchone()[0]
        wrong = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='wrong'").fetchone()[0]
        degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
        clusters = conn.execute("SELECT COUNT(DISTINCT cluster_name) FROM cluster_membership").fetchone()[0]
        reflections = conn.execute("SELECT COUNT(*) FROM reflection_fixes").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM ide_events").fetchone()[0]
        beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]

        coverage = triggered / max(total_active, 1) * 100
        fallback_rate = fallbacks / max(total_logs, 1) * 100
        close_rate = with_outcome / max(total_logs, 1) * 100
        accuracy = correct / max(with_outcome, 1) * 100

        # Per-cluster trigger stats
        cluster_stats = conn.execute(
            "SELECT r.cluster, COUNT(DISTINCT r.id) as total, "
            "       SUM(CASE WHEN r.times_triggered > 0 THEN 1 ELSE 0 END) as triggered, "
            "       ROUND(AVG(r.cluster_confidence), 3) as avg_conf "
            "FROM resources r WHERE r.status='active' AND r.cluster IS NOT NULL "
            "GROUP BY r.cluster ORDER BY total DESC"
        ).fetchall()

        conn.close()
        health["subsystems"]["database"] = {
            "resources": dict(resources_by_type),
            "total_active": total_active,
            "triggered": triggered,
            "coverage_pct": round(coverage, 1),
            "routing_logs": {
                "total": total_logs,
                "with_outcome": with_outcome,
                "pending_feedback": null_outcome,
                "correct": correct,
                "wrong": wrong,
                "accuracy_pct": round(accuracy, 1),
            },
            "fallback_rate_pct": round(fallback_rate, 1),
            "close_rate_pct": round(close_rate, 1),
            "degraded": degraded,
            "clusters": clusters,
            "cluster_coverage": {
                r["cluster"]: {
                    "total": r["total"], "triggered": r["triggered"],
                    "pct": round(r["triggered"] / max(r["total"], 1) * 100, 1),
                    "avg_conf": r["avg_conf"],
                } for r in cluster_stats
            },
            "reflection_fixes": reflections,
            "ide_events": events,
            "model_beliefs": beliefs,
        }
    except Exception as e:
        health["subsystems"]["database"] = {"error": str(e)}

    # Learner health
    if learner:
        try:
            health["subsystems"]["learner"] = learner.get_stats()
        except Exception:
            health["subsystems"]["learner"] = {"error": "stats unavailable"}

    # Metrics health
    if metrics_store:
        try:
            health["subsystems"]["metrics"] = {"enabled": True}
        except Exception:
            pass

    # Embedding model status
    if EMBEDDING_LOADED:
        health["subsystems"]["embedding"] = {"status": "cached", "load_time_s": round(EMBEDDING_LOAD_TIME, 1)}
    elif EMBEDDING_LOAD_TIME > 0:
        health["subsystems"]["embedding"] = {"status": "failed", "load_time_s": round(EMBEDDING_LOAD_TIME, 1)}
    else:
        health["subsystems"]["embedding"] = {"status": "loading"}

    return JSONResponse(health)


# ── Simple Endpoints ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": "4.0.0-unified"})


@app.get("/approaches")
async def approaches():
    """List available reasoning approaches."""
    try:
        from approaches import list_approaches
        return JSONResponse({"approaches": list_approaches()})
    except Exception:
        return JSONResponse({
            "approaches": ["none", "cot_reflection", "moa", "mcts", "bon"],
            "active": "none"
        })


# ═══════════════════════════════════════════════════════════════════════
# MCP BRIDGE (expose MCP tools via HTTP for IDEs that can't use stdio)
# ═══════════════════════════════════════════════════════════════════════

@app.post("/mcp/{tool_name}")
async def mcp_proxy(tool_name: str, request: Request):
    """HTTP proxy for MCP tools. Bridges HTTP → MCP stdio."""
    body = await request.json()

    try:
        # Argument name mapping: HTTP keys → MCP function args
        arg_map = {
            "find_skills": {"query": "task", "q": "task"},
            "cluster_route": {"query": "task", "q": "task"},
            "auto_route": {"query": "task", "q": "task"},
            "route_v4": {"query": "query", "q": "query"},
            "get_skill": {"name": "name", "skill_name": "name"},
            "log_outcome": {"skill": "skill_name", "outcome": "outcome"},
        }

        # MCP function → Python arg names
        func_map = {
            "find_skills": "find_skills",
            "get_skill": "get_skill",
            "log_outcome": "log_outcome",
            "audit_routing": "audit_routing",
            "auto_route": "auto_route",
            "cluster_route": "cluster_route",
            "cluster_list": "cluster_list",
            "route_v4": "route_v4",
            "nexus_health": "nexus_health",
        }

        func_name = func_map.get(tool_name)
        if not func_name:
            raise HTTPException(404, f"MCP tool '{tool_name}' not found. Available: {list(func_map.keys())}")

        # Cached lazy import of heavy MCP module
        global _mcp_module
        if _mcp_module is None:
            import importlib, pathlib
            mcp_path = pathlib.Path(__file__).parent / "nexus-mcp-enhanced.py"
            spec = importlib.util.spec_from_file_location("nexus_mcp_enhanced", mcp_path)
            _mcp_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_mcp_module)
            logger.info("MCP module loaded and cached")

        func = getattr(_mcp_module, func_name)

        # Remap arguments for the function
        remaps = arg_map.get(tool_name, {})
        call_args = {}
        for k, v in body.items():
            call_args[remaps.get(k, k)] = v

        result = func(**call_args)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                pass
        return JSONResponse(result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MCP proxy error for {tool_name}: {e}")
        raise HTTPException(500, f"MCP proxy error: {e}")


# ═══════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    logger.info(f"NEXUS v4.0 Unified Server starting")
    logger.info(f"  Thompson Routing: {THOMPSON_ROUTING}")
    logger.info(f"  Cluster Routing: {CLUSTER_ROUTING}")
    logger.info(f"  Reflection:      {REFLECTION_ENABLED}")
    logger.info(f"  MCP Sync:        {MCP_SYNC_ENABLED}")
    try:
        from server.broker_runtime import initialize_runtime
        initialize_runtime()
        logger.info("  Broker runtime initialized")
    except Exception as e:
        logger.warning(f"Broker runtime init failed: {e}")

    # Pre-warm MCP module in background so first request isn't slow
    async def warm_mcp():
        global _mcp_module
        try:
            import importlib.util as _ilu, pathlib as _pl
            mcp_path = _pl.Path(__file__).parent / "nexus-mcp-enhanced.py"
            spec = _ilu.spec_from_file_location("nexus_mcp_enhanced", mcp_path)
            _mcp_module = _ilu.module_from_spec(spec)
            spec.loader.exec_module(_mcp_module)
            logger.info("MCP module pre-warmed and cached")
        except Exception as e:
            logger.warning(f"MCP module pre-warm failed (will load on first request): {e}")

    asyncio.create_task(asyncio.to_thread(warm_mcp))

    # Pre-load embedding model in background so first route request isn't slow
    t = threading.Thread(target=preload_embeddings, daemon=True)
    t.start()

    if MCP_SYNC_ENABLED:
        try:
            from server.nexus_daemon import run_daemon
            asyncio.create_task(asyncio.to_thread(run_daemon, 120, False))
            logger.info("  MCP Sync daemon started")
        except Exception as e:
            logger.warning(f"MCP Sync daemon failed: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("NEXUS_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
