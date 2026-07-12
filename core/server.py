#!/usr/bin/env python3
"""
Nexus Core Server - OpenAI-compatible LLM Gateway
Replaces and extends optillm.py with multi-provider, routing, and IDE connector support.

Routes: /v1/chat/completions, /v1/models, /health, /metrics
"""
import os, sys, json, time, uuid, logging, argparse, secrets
from pathlib import Path

# Add nexus to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from core.config import NexusConfig, ProviderConfig, RoutingConfig, IDEConnection, ApproachConfig
from core.metrics import MetricsStore, RequestMetrics
from core.learner import NexusLearner, IDEEvent
from core.ide_events import IDEEventCollector
from providers.registry import get_provider, build_provider_map

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nexus")

app = FastAPI(title="NEXUS LLM Gateway", version="0.1.0")

# Global state
config: Optional[NexusConfig] = None
providers: Dict[str, Any] = {}
metrics_store = MetricsStore()
learner: Optional[NexusLearner] = None
event_collector: Optional[IDEEventCollector] = None
task_classifier = None  # Initialized lazily from NexusLearner.classify_task


# ─── Request Models ──────────────────────────────────────────────────────────

class Message(BaseModel):
    role: str
    content: str | List[Dict]

class ChatRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    n: int = 1
    extra_body: Optional[Dict[str, Any]] = None


# ─── Helper Functions ────────────────────────────────────────────────────────

def normalize_messages(messages: List[Dict]) -> List[Dict]:
    """Ensure all content is string, not list"""
    normalized = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        normalized.append({"role": msg["role"], "content": content})
    return normalized


def classify_task(messages: List[Dict]) -> str:
    """Heuristic task classification based on message content"""
    full_text = " ".join(
        str(m.get("content", "")) for m in messages
    ).lower()

    if any(k in full_text for k in ["def ", "class ", "import ", "function ", "=>", "async"]):
        return "coding"
    if any(k in full_text for k in ["prove", "theorem", "calculate", "solve", "logic", "reason"]):
        return "reasoning"
    if any(k in full_text for k in ["search", "browse", "fetch", "scrape", "find", "agent"]):
        return "agentic"
    return "general"


def resolve_model(model_name: str, task_type: str = "general") -> tuple[str, str, str]:
    """
    Map logical model name to (provider, model, role).
    Priority:
      1. Thompson Sampling route (if learner enabled)
      2. Explicit provider/model prefix
      3. Configured models
      4. First available provider (default)
    """
    # Check if Thompson Sampling routing is enabled
    if learner and learner.config.routing.enabled and config:
        available = [(p, m.name) for p, pconfig in config.providers.items() for m in pconfig.models]
        route = learner.get_route(task_type, available)
        if route:
            logger.info(f"[TS-Route] {task_type} -> {route['provider']}/{route['model']} "
                       f"(score={route['score']:.3f}, conf={route.get('confidence', 0):.2f})")
            return route["provider"], route["model"], task_type

    # Check explicit provider/model prefix
    if "/" in model_name:
        for pname in providers:
            if model_name.startswith(f"{pname}/"):
                actual_model = model_name[len(pname)+1:]
                return pname, actual_model, "general"

    # Check configured models
    for pname, pconfig in (config.providers.items() if config else {}):
        for model in pconfig.models:
            if model.name == model_name:
                return pname, model.name, model.role

    # Fallback: first available provider
    if providers:
        first_provider = list(providers.keys())[0]
        return first_provider, model_name, "general"

    raise HTTPException(status_code=400, detail=f"No provider available for model: {model_name}")


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest, http_request: Request):
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    # Auth check
    if config and config.optillm_api_key:
        auth = http_request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = auth.split("Bearer ", 1)[1].strip()
        if not secrets.compare_digest(token, config.optillm_api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        # Classify task type for routing (lazy init)
        messages_raw = [m.model_dump() for m in request.messages]
        last_msg = messages_raw[-1].get("content", "") if messages_raw else ""
        if learner and hasattr(learner, 'classify_task'):
            task_type, task_confidence = learner.classify_task(last_msg[:200])
        else:
            task_type, task_confidence = "general", 0.5

        provider_name, model_name, role = resolve_model(request.model, task_type)
        provider = providers.get(provider_name)
        if not provider:
            raise HTTPException(status_code=503, detail=f"Provider '{provider_name}' not available")

        # Normalize messages
        messages = normalize_messages([m.model_dump() for m in request.messages])

        # Build kwargs
        kwargs = {}
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop:
            kwargs["stop"] = request.stop

        logger.info(f"[{request_id}] {provider_name}/{model_name} | task={role} | stream={request.stream}")

        response = await provider.chat_completions(model=model_name, messages=messages, stream=request.stream, **kwargs)

        latency_ms = (time.time() - start_time) * 1000

        # Log metrics with classified task type
        m = RequestMetrics(
            request_id=request_id,
            model=model_name,
            provider=provider_name,
            task_type=task_type,
            latency_ms=latency_ms,
            prompt_tokens=response.get("usage", {}).get("prompt_tokens", 0),
            completion_tokens=response.get("usage", {}).get("completion_tokens", 0),
            total_tokens=response.get("usage", {}).get("total_tokens", 0),
            timestamp=time.time(),
            success=True
        )
        metrics_store.log(m)

        if request.stream:
            return StreamingResponse(
                _stream_response(response, model_name, request_id),
                media_type="text/event-stream"
            )

        return JSONResponse(content=response)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Error: {e}")
        # task_type may not be defined if error occurred before classification
        fallback_task = task_type if 'task_type' in dir() or 'task_type' in locals() else "general"
        fallback_model = model_name if 'model_name' in locals() else request.model
        fallback_provider = provider_name if 'provider_name' in locals() else "unknown"
        m = RequestMetrics(
            request_id=request_id, model=fallback_model, provider=fallback_provider,
            task_type=fallback_task, latency_ms=(time.time()-start_time)*1000,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            timestamp=time.time(), success=False, error=str(e)
        )
        metrics_store.log(m)
        raise HTTPException(status_code=500, detail=str(e))


async def _stream_response(response: Dict, model: str, request_id: str):
    """Convert OpenAI response to SSE stream format"""
    import json
    content = ""
    if response.get("choices"):
        content = response["choices"][0].get("message", {}).get("content", "")

    response_id = f"chatcmpl-{request_id}"
    created = int(time.time())

    yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'delta': {'role': 'assistant', 'content': content}, 'index': 0, 'finish_reason': 'stop'}]})}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/v1/models")
async def list_models():
    all_models = []
    for name, provider in providers.items():
        try:
            models = await provider.list_models()
            for m in models:
                all_models.append({
                    "id": m,
                    "object": "model",
                    "created": 0,
                    "owned_by": name
                })
        except Exception as e:
            logger.warning(f"Failed to list models from {name}: {e}")

    return JSONResponse({
        "object": "list",
        "data": all_models
    })


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "version": "0.1.0",
        "providers": list(providers.keys()),
        "routing_enabled": config.routing.enabled if config else False
    })


@app.get("/metrics/{provider}/{model}")
async def get_metrics(provider: str, model: str, last_n: int = 100):
    return JSONResponse(metrics_store.get_stats(provider, model, last_n))


@app.get("/metrics/routing")
async def get_routing_data(min_samples: int = 50):
    """Get data for routing decisions"""
    return JSONResponse(metrics_store.get_routing_decision_data(min_samples))


@app.get("/config")
async def get_config():
    """Return current config (without secrets)"""
    if not config:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return JSONResponse({
        "host": config.host,
        "port": config.port,
        "providers": list(config.providers.keys()),
        "models": config.get_all_models(),
        "routing_enabled": config.routing.enabled,
        "ide_connections": {k: {"enabled": v.enabled, "api_base": v.api_base} for k, v in config.ide_connections.items()}
    })


# ─── IDE Event Routes ─────────────────────────────────────────────────────────

class IDEEventRequest(BaseModel):
    event_type: str
    model: str
    provider: str
    task_type: str
    task_description: str = ""
    reward: float = 0.0
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None
    ide: str = "unknown"

@app.post("/nexus/event")
async def post_ide_event(event: IDEEventRequest):
    """Receive an IDE feedback event (accept/reject, test pass/fail, etc.)"""
    if not learner:
        raise HTTPException(status_code=503, detail="Learner not enabled (routing must be enabled)")

    from core.ide_events import create_event, classify_event_reward
    reward = event.reward if event.reward != 0.0 else classify_event_reward(event.event_type, event.metadata)

    ide_event = create_event(
        ide=event.ide,
        model=event.model,
        provider=event.provider,
        task_type=event.task_type,
        event_type=event.event_type,
        reward=reward,
        task_description=event.task_description,
        confidence=event.confidence,
        metadata=event.metadata or {}
    )

    result = event_collector.receive_event(ide_event)
    return JSONResponse(result)


@app.get("/nexus/events")
async def get_recent_events(limit: int = 50):
    """Get recent IDE events"""
    if not event_collector:
        return JSONResponse({"events": []})
    return JSONResponse({"events": event_collector.get_recent_events(limit)})


@app.get("/nexus/learner/stats")
async def get_learner_stats():
    """Get learning statistics"""
    if not learner:
        raise HTTPException(status_code=503, detail="Learner not enabled")
    return JSONResponse(learner.get_stats())


@app.get("/nexus/beliefs")
async def get_model_beliefs():
    """Get current model beliefs per task type"""
    if not learner:
        raise HTTPException(status_code=503, detail="Learner not enabled")
    stats = learner.get_learning_stats()
    return JSONResponse(stats)


# ─── Configuration Loader ──────────────────────────────────────────────────────

def load_config(config_path: str):
    global config, providers, learner, event_collector
    config = NexusConfig.from_yaml(config_path)
    providers = build_provider_map(config)
    logger.info(f"NEXUS loaded. Providers: {list(providers.keys())}")
    logger.info(f"Routing: {'ENABLED' if config.routing.enabled else 'DISABLED'}")
    for pname, pconfig in config.providers.items():
        logger.info(f"  {pname}: {[m.name for m in pconfig.models]}")

    # Initialize learner if routing is enabled
    if config.routing.enabled:
        learner = NexusLearner(config, metrics_store)
        event_collector = IDEEventCollector(learner)
        logger.info("NexusLearner: ENABLED (self-learning from IDE events + benchmarks)")
    else:
        logger.info("NexusLearner: DISABLED (set routing.enabled: true to enable)")


# ─── Startup ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NEXUS LLM Gateway")
    parser.add_argument("--config", default="nexus.yaml", help="Path to nexus.yaml config")
    parser.add_argument("--host", help="Override host")
    parser.add_argument("--port", type=int, help="Override port")
    parser.add_argument("--api-key", help="Set API key for auth")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        logger.error(f"Config not found: {config_path}")
        logger.info(f"Create one at: {config_path}")
        sys.exit(1)

    load_config(str(config_path))

    host = args.host or config.host
    port = args.port or config.port
    if args.api_key:
        config.optillm_api_key = args.api_key

    import uvicorn
    logger.info(f"Starting NEXUS on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=config.log_level)


if __name__ == "__main__":
    main()