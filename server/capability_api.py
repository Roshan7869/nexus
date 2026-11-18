"""Nexus Shared Capability HTTP API Facade (/capabilities/v1).

Exposes canonical catalog discovery, advisory recommendations, generation-leased
tool invocation, native action receipts, and verified reward recording.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.capability_runtime import CapabilityError
from server.capability_catalog import CatalogError

logger = logging.getLogger("capability_api")

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="capability-api")


async def _run_sync(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, lambda: func(*args, **kwargs))


# ── Pydantic Request Models ──────────────────────────────────────────

class OpenSessionRequest(BaseModel):
    session_id: Optional[str] = None
    harness_id: str = "codex"
    project: str = "default"
    context: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = 3600


class CloseSessionRequest(BaseModel):
    outcome: str = "closed"
    notes: str = ""


class RecommendRequest(BaseModel):
    session_id: str
    query: str
    k: int = 8
    seed: Optional[int] = None
    project: Optional[str] = None
    niche: Optional[str] = None
    niche_version: Optional[str] = None


class InvokeRequest(BaseModel):
    capability_id: str
    version: str = "v1"
    session_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    approval: Optional[dict[str, Any]] = None


class NativeReceiptRequest(BaseModel):
    envelope: dict[str, Any]
    state: str
    result: Any = None
    error: Optional[dict[str, Any]] = None


class RecordOutcomeRequest(BaseModel):
    invocation_id: str
    verdict: str  # "correct" or "wrong"
    evidence: Optional[dict[str, Any]] = None
    evidence_ref: Optional[str] = "verified_receipt"
    verified_by: Optional[str] = None


# ── Authentication Helper ───────────────────────────────────────────

def resolve_principal(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_principal_id: Optional[str] = Header(None),
    x_harness_id: Optional[str] = Header(None),
    x_projects: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Resolve transport identity from request state, headers, or bearer token."""
    if hasattr(request.state, "principal") and request.state.principal:
        return request.state.principal

    # Explicit headers (used by internal tests / trusted harnesses)
    if x_principal_id:
        projects = [p.strip() for p in (x_projects or "*").split(",") if p.strip()]
        is_verifier = "verifier" in x_principal_id.lower() or x_principal_id in ("trusted", "evaluator")
        return {
            "id": x_principal_id,
            "harness": x_harness_id or "codex",
            "projects": projects,
            "verifier": is_verifier,
        }

    # Default loopback principal
    return {
        "id": "loopback",
        "harness": "codex",
        "projects": ["*"],
        "verifier": False,
    }


def map_error(exc: Exception) -> HTTPException:
    """Map capability exceptions to standard HTTP error codes."""
    if isinstance(exc, CapabilityError):
        code_map = {
            "INVALID_ARGUMENT": 422,
            "UNAUTHORIZED": 401,
            "FORBIDDEN": 403,
            "NOT_FOUND": 404,
            "CONFLICT": 409,
            "VERSION_CHANGED": 409,
            "EXPIRED": 410,
            "UNAVAILABLE": 503,
            "PROVIDER_TIMEOUT": 504,
        }
        status = code_map.get(exc.code, 400)
        return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, CatalogError):
        return HTTPException(status_code=400, detail={"code": "CATALOG_ERROR", "message": str(exc)})
    logger.exception("Unexpected capability API error: %s", exc)
    return HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(exc)})


# ── Service Container & Factory ─────────────────────────────────────

class CapabilityServiceGraph:
    """Container holding injected or lazily initialized service singletons."""

    def __init__(self, catalog=None, runtime=None, recommender=None, events=None, adapters=None):
        self.catalog = catalog
        self.runtime = runtime
        self.recommender = recommender
        self.events = events
        self.adapters = adapters

    def is_ready(self) -> bool:
        return bool(self.catalog and self.runtime and self.recommender and self.events)


_GLOBAL_GRAPH: Optional[CapabilityServiceGraph] = None


def set_global_service_graph(graph: CapabilityServiceGraph):
    global _GLOBAL_GRAPH
    _GLOBAL_GRAPH = graph


def get_global_service_graph() -> CapabilityServiceGraph:
    global _GLOBAL_GRAPH
    if _GLOBAL_GRAPH is None:
        try:
            from server.db import get_db_path
            from server.capability_catalog import CapabilityCatalog
            from server.capability_runtime import CapabilityRuntime
            from server.capability_recommender import CapabilityRecommender
            from server.capability_events import CapabilityEvents
            from server.capability_adapters import CapabilityAdapters

            db_path = get_db_path()
            catalog = CapabilityCatalog(db_path)
            runtime = CapabilityRuntime(db_path, catalog)
            recommender = CapabilityRecommender(db_path, catalog)
            events = CapabilityEvents(db_path)
            adapters = CapabilityAdapters(db_path, secret=b"nexus-capability-signing-secret")
            _GLOBAL_GRAPH = CapabilityServiceGraph(catalog, runtime, recommender, events, adapters)
        except Exception as e:
            logger.warning("Could not lazily initialize global capability service graph: %s", e)
            _GLOBAL_GRAPH = CapabilityServiceGraph()
    return _GLOBAL_GRAPH


# ── Router Creation ──────────────────────────────────────────────────

def create_capability_router(graph: Optional[CapabilityServiceGraph] = None) -> APIRouter:
    router = APIRouter(prefix="/capabilities/v1", tags=["capabilities"])

    def get_services() -> CapabilityServiceGraph:
        return graph or get_global_service_graph()

    @router.get("/health")
    async def health(services: CapabilityServiceGraph = Depends(get_services)):
        catalog_count = 0
        catalog_revision = 0
        catalog_ready = False
        if services.catalog and getattr(services.catalog, "db_path", None):
            try:
                from server.capability_catalog import connect_db
                with connect_db(services.catalog.db_path) as conn:
                    row_c = conn.execute("SELECT count(*) FROM capability_catalog").fetchone()
                    catalog_count = row_c[0] if row_c else 0
                    row_r = conn.execute("SELECT max(revision) FROM catalog_revisions").fetchone()
                    catalog_revision = (row_r[0] or 0) if row_r else 0
                catalog_ready = catalog_revision > 0 and catalog_count > 0
            except Exception as e:
                logger.warning("Could not check catalog health: %s", e)
                catalog_ready = False

        ready = services.is_ready() and catalog_ready
        status = "ready" if ready else ("degraded" if services.is_ready() else "unavailable")
        payload = {
            "status": status,
            "api_version": "v1",
            "catalog_ready": catalog_ready,
            "catalog_count": catalog_count,
            "catalog_revision": catalog_revision,
            "runtime_ready": services.runtime is not None,
            "recommender_ready": services.recommender is not None,
            "events_ready": services.events is not None,
            "adapters_ready": services.adapters is not None,
        }
        if status == "unavailable":
            return JSONResponse(status_code=503, content=payload)
        return payload

    @router.post("/sessions")
    async def open_session(
        body: OpenSessionRequest,
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.events:
            raise HTTPException(503, "Events service unavailable")
        session_id = body.session_id or f"sess_{uuid.uuid4().hex[:16]}"
        context = {
            "project": body.project,
            "harness": body.harness_id,
            **body.context,
        }
        try:
            res = await _run_sync(
                services.events.open_session,
                principal,
                context=context,
                session_id=session_id,
                ttl_seconds=body.ttl_seconds,
            )
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "session": res,
            }
        except Exception as e:
            raise map_error(e)

    @router.post("/sessions/{session_id}/close")
    async def close_session(
        session_id: str,
        body: CloseSessionRequest,
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.events:
            raise HTTPException(503, "Events service unavailable")
        try:
            res = await _run_sync(
                services.events.close_session,
                session_id,
                body.outcome,
                principal=principal,
                notes=body.notes,
            )
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "closed": res,
            }
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except Exception as e:
            raise map_error(e)

    @router.get("/catalog")
    async def list_catalog(
        cursor: Optional[str] = Query(None),
        limit: int = Query(20, ge=1, le=100),
        query: Optional[str] = Query(None),
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.catalog:
            raise HTTPException(503, "Catalog service unavailable")
        try:
            res = await _run_sync(services.catalog.list_capabilities, principal, cursor, limit, query)
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "catalog_revision": res.get("catalog_revision"),
                "page": res,
            }
        except Exception as e:
            raise map_error(e)

    @router.get("/catalog/{capability_id:path}")
    async def get_capability(
        capability_id: str,
        version: Optional[str] = Query(None),
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.catalog:
            raise HTTPException(503, "Catalog service unavailable")
        try:
            desc = await _run_sync(services.catalog.get_capability, capability_id, version)
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "descriptor": desc,
            }
        except Exception as e:
            raise map_error(e)

    @router.get("/skills/{capability_id:path}/content")
    async def read_skill(
        capability_id: str,
        version: str = Query("v1"),
        path: Optional[str] = Query(None),
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.catalog:
            raise HTTPException(503, "Catalog service unavailable")
        try:
            content = await _run_sync(services.catalog.read_skill, capability_id, version, path)
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "skill": content,
            }
        except Exception as e:
            raise map_error(e)

    @router.post("/recommend")
    async def recommend(
        body: RecommendRequest,
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.recommender:
            raise HTTPException(503, "Recommender service unavailable")
        try:
            res = await _run_sync(
                services.recommender.recommend,
                body.session_id,
                body.query,
                k=body.k,
                seed=body.seed,
                principal=principal,
            )
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "catalog_revision": res.get("catalog_revision"),
                "recommendation": res,
            }
        except Exception as e:
            raise map_error(e)

    @router.post("/invoke")
    async def invoke(
        body: InvokeRequest,
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.runtime or not services.catalog:
            raise HTTPException(503, "Runtime service unavailable")

        req = body.model_dump()
        try:
            desc = await _run_sync(services.catalog.get_capability, body.capability_id, body.version)
            if desc.get("kind") == "native_action":
                if not services.adapters:
                    raise HTTPException(503, "Native adapters service unavailable")
                inv_req = {**req, "principal": principal}
                envelope = await _run_sync(services.adapters.prepare_native_action, inv_req)
                return {
                    "request_id": str(uuid.uuid4()),
                    "api_version": "v1",
                    "native_envelope": envelope,
                }
            res = await _run_sync(services.runtime.invoke, req, principal)
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "receipt": res,
            }
        except Exception as e:
            raise map_error(e)

    @router.post("/native-receipts")
    async def native_receipt(
        body: NativeReceiptRequest,
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.adapters:
            raise HTTPException(503, "Native adapters service unavailable")
        envelope_id = body.envelope.get("envelope_id")
        if not envelope_id:
            raise HTTPException(422, "envelope_id required in envelope")
        try:
            res = await _run_sync(services.adapters.accept_native_receipt, envelope_id, body.model_dump(), principal)
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "receipt": res,
            }
        except Exception as e:
            raise map_error(e)

    @router.post("/outcomes")
    async def record_outcome(
        body: RecordOutcomeRequest,
        principal: dict = Depends(resolve_principal),
        services: CapabilityServiceGraph = Depends(get_services),
    ):
        if not services.events:
            raise HTTPException(503, "Events service unavailable")
        if not principal.get("verifier"):
            raise HTTPException(403, "Only trusted verifier principal can record outcomes")

        evidence = body.evidence
        if evidence is None:
            # Construct evidence by binding to the invocation row
            from server.capability_catalog import connect_db
            with connect_db(services.events.db_path) as db:
                row = db.execute("SELECT * FROM capability_invocations WHERE invocation_id=?", (body.invocation_id,)).fetchone()
                if row is None:
                    raise HTTPException(404, "Invocation not found")
                evidence = {
                    "invocation_id": row["invocation_id"],
                    "capability_id": row["capability_id"],
                    "version": row["version"],
                    "evidence_ref": body.evidence_ref or "verified_eval",
                    "verified_by": body.verified_by or principal.get("id"),
                }

        try:
            res = await _run_sync(
                services.events.record_verified_outcome,
                body.invocation_id,
                body.verdict,
                evidence,
                principal,
            )
            return {
                "request_id": str(uuid.uuid4()),
                "api_version": "v1",
                "recorded": res,
            }
        except Exception as e:
            raise map_error(e)

    return router


# Module-level default router
router = create_capability_router()
