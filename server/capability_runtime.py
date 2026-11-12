"""Explicitly owned, injected capability providers; no configured provider startup."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from server.capability_catalog import connect_db, initialize_schema
from server.local_harness.context import NexusContext


class CapabilityError(ValueError):
    def __init__(self, code, message, status_code=422):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CapabilityError("INVALID_ARGUMENT", "Expected finite JSON values") from exc


def arguments_hash(arguments):
    return hashlib.sha256(canonical(arguments).encode()).hexdigest()


def epoch(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=timezone.utc).timestamp()
    if not math.isfinite(result):
        raise CapabilityError("INVALID_ARGUMENT", "Invalid expiry")
    return result


def authorize(db, request, principal, descriptor, policy_context=None):
    """Transport principal wins over request identity; use shared policy when supplied."""
    if not isinstance(principal, dict) or not principal.get("id") or not principal.get("harness"):
        raise CapabilityError("UNAUTHORIZED", "Authenticated principal required", 401)
    session = db.execute("SELECT * FROM capability_sessions WHERE session_id=?",
                         (request.get("session_id"),)).fetchone()
    if session is None:
        raise CapabilityError("NOT_FOUND", "Session not found", 404)
    session = dict(session)
    if (session["principal_id"], session["harness_id"]) != (principal["id"], principal["harness"]):
        raise CapabilityError("FORBIDDEN", "Session belongs to another principal or harness", 403)
    if session["status"] != "active" or epoch(session["expires_at"]) <= time.time():
        raise CapabilityError("EXPIRED", "Session is closed or expired", 410)
    if "*" not in principal.get("projects", []) and session["project"] not in principal.get("projects", []):
        raise CapabilityError("FORBIDDEN", "Project is outside principal scope", 403)
    # Loaded lazily: policy is another worker's injected-db implementation.
    from server.capability_policy import authorize_invocation
    if policy_context is not None:
        session["context"] = {**json.loads(session["context_json"] or "{}"), **policy_context}
    decision = authorize_invocation(principal, session, descriptor,
                                    request["arguments"], request.get("approval"))
    allowed = decision.get("allowed", False) if isinstance(decision, dict) else decision.allowed
    if not allowed:
        reason = decision.get("reason", "FORBIDDEN") if isinstance(decision, dict) else "FORBIDDEN"
        if reason == "INVALID_ARGUMENTS":
            raise CapabilityError("INVALID_ARGUMENT", "Arguments do not match the registered schema")
        if reason in ("DEPENDENCY_UNAVAILABLE", "UNAVAILABLE", "SOURCE_REMOVED"):
            raise CapabilityError("UNAVAILABLE", "Capability dependency is unavailable", 503)
        raise CapabilityError("FORBIDDEN", "Execution policy denied invocation", 403)
    return session


def validate_request(request):
    for key in ("capability_id", "version", "session_id", "idempotency_key"):
        if not isinstance(request.get(key), str) or not request[key]:
            raise CapabilityError("INVALID_ARGUMENT", f"{key} must be a nonempty string")
    if not isinstance(request.get("arguments"), dict):
        raise CapabilityError("INVALID_ARGUMENT", "arguments must be an object")
    if len(canonical(request).encode()) > 1024 * 1024:
        raise CapabilityError("INVALID_ARGUMENT", "Invocation exceeds 1 MiB")


def append_event(db, invocation_id, session_id, kind, payload):
    event_id = str(uuid.uuid4())
    db.execute("INSERT INTO capability_events "
               "(event_id,invocation_id,session_id,type,payload_json,created_at) VALUES (?,?,?,?,?,?)",
               (event_id, invocation_id, session_id, kind, canonical(payload), time.time()))
    return event_id


def receipt(row):
    data = dict(row)
    result = json.loads(data.pop("result_ref") or "null")
    data.update(result=None, error=None)
    if isinstance(result, dict) and "state" in result:
        data.update(result)
    return data


def existing_invocation(db, request, principal):
    row = db.execute("SELECT * FROM capability_invocations WHERE principal_id=? AND idempotency_key=?",
                     (principal["id"], request["idempotency_key"])).fetchone()
    if row is not None:
        for key, value in {"capability_id": request["capability_id"], "version": request["version"],
                           "session_id": request["session_id"],
                           "args_hash": arguments_hash(request["arguments"])}.items():
            if row[key] != value:
                raise CapabilityError("CONFLICT", "Idempotency key has different bindings", 409)
    return row


def insert_invocation(db, request, principal, invocation_id, generation, state):
    db.execute("INSERT INTO capability_invocations (invocation_id,principal_id,session_id,"
               "capability_id,version,provider_generation,idempotency_key,args_hash,state,decision_id) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", (invocation_id, principal["id"], request["session_id"],
               request["capability_id"], request["version"], generation, request["idempotency_key"],
               arguments_hash(request["arguments"]), state, request.get("decision_id")))


@dataclass
class Lease:
    provider_id: str
    generation: int
    context: NexusContext
    pins: list = field(default_factory=list)
    released: bool = False


class CapabilityRuntime:
    """One daemon-owned instance per database; all handlers are explicitly injected.

    Handler contract: callable(arguments, invocation_context), or an object exposing
    invoke(arguments, invocation_context). Optional initialize(context) and close()
    hooks are owned through NexusContext. descriptor supports dependencies (provider
    IDs), timeout_seconds, max_concurrency and dispose (a callable).
    """

    def __init__(self, db_path, catalog):
        self.db_path, self.catalog = db_path, catalog
        self._lock = threading.RLock()
        self._generations, self._active = {}, {}
        self._closed = False
        initialize_schema(db_path)

    def register_provider(self, provider_id, handler, descriptor=None):
        descriptor = dict(descriptor or {})
        if not callable(handler) and not callable(getattr(handler, "invoke", None)):
            raise CapabilityError("INVALID_ARGUMENT", "Provider handler must be callable")
        dependencies = descriptor.get("dependencies", [])
        timeout = float(descriptor.get("timeout_seconds", 30))
        capacity = int(descriptor.get("max_concurrency", 8))
        if timeout <= 0 or not math.isfinite(timeout) or capacity < 1:
            raise CapabilityError("INVALID_ARGUMENT", "Invalid provider limits")
        with self._lock, connect_db(self.db_path) as db:
            if self._closed:
                raise CapabilityError("UNAVAILABLE", "Runtime is drained", 503)
            def visit(node, path):
                if node in path:
                    raise CapabilityError("INVALID_ARGUMENT", "Provider dependency cycle")
                deps = dependencies if node == provider_id else self._generations[
                    (node, self._active[node])]["descriptor"].get("dependencies", [])
                for dep in deps:
                    if dep != provider_id and dep not in self._active:
                        raise CapabilityError("UNAVAILABLE", "Provider dependency unavailable", 503)
                    visit(dep, path | {node})
            visit(provider_id, set())
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT generation,state FROM provider_generations WHERE provider_id=?",
                              (provider_id,)).fetchall()
            if any(r["state"] in ("active", "loading", "draining") and
                   (provider_id, r["generation"]) not in self._generations for r in rows):
                raise CapabilityError("CONFLICT", "Provider owned elsewhere; recover before registering", 409)
            generation = max((int(r["generation"]) for r in rows), default=0) + 1
            stored = {k: v for k, v in descriptor.items() if k != "dispose"}
            db.execute("INSERT INTO provider_generations "
                       "(provider_id,generation,state,descriptor_json,lease_count) VALUES (?,?,?,?,?)",
                       (provider_id, generation, "loading", canonical(stored), 0))
            db.commit()
            ctx = NexusContext()
            try:
                disposer = descriptor.get("dispose") or getattr(handler, "close", None)
                if disposer:
                    ctx.effect(lambda: disposer)
                if callable(getattr(handler, "initialize", None)):
                    handler.initialize(ctx)
                old = self._active.get(provider_id)
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE provider_generations SET state='active' WHERE provider_id=? AND generation=?",
                           (provider_id, generation))
                if old is not None:
                    db.execute("UPDATE provider_generations SET state='draining' WHERE provider_id=? AND generation=?",
                               (provider_id, old))
                db.commit()
            except BaseException:
                db.rollback()
                ctx.dispose()
                db.execute("UPDATE provider_generations SET state='failed' WHERE provider_id=? AND generation=?",
                           (provider_id, generation))
                db.commit()
                raise
            self._generations[(provider_id, generation)] = {
                "handler": handler, "context": ctx, "descriptor": descriptor,
                "slots": threading.BoundedSemaphore(capacity), "timeout": timeout}
            self._active[provider_id] = generation
            if old is not None:
                self._dispose_drained(db, (provider_id, old))
            return {"provider_id": provider_id, "generation": generation, "state": "active"}

    def _dispose_drained(self, db, pin):
        row = db.execute("SELECT state,lease_count FROM provider_generations WHERE provider_id=? AND generation=?",
                         pin).fetchone()
        if row and row["state"] == "draining" and row["lease_count"] == 0:
            self._generations[pin]["context"].dispose()
            db.execute("UPDATE provider_generations SET state='inactive' WHERE provider_id=? AND generation=?", pin)
            db.commit()

    def _acquire(self, db, provider_id):
        pins = []
        def pin(node):
            generation = self._active.get(node)
            if generation is None or self._closed:
                raise CapabilityError("UNAVAILABLE", "Provider is not registered", 503)
            item = (node, generation)
            if item in pins:
                return
            record = self._generations[item]
            row = db.execute("SELECT state FROM provider_generations WHERE provider_id=? AND generation=?", item).fetchone()
            if row is None or row["state"] != "active":
                raise CapabilityError("UNAVAILABLE", "Provider is not active", 503)
            for dep in record["descriptor"].get("dependencies", []):
                pin(dep)
            pins.append(item)
        pin(provider_id)
        for item in pins:
            db.execute("UPDATE provider_generations SET lease_count=lease_count+1 WHERE provider_id=? AND generation=?", item)
        return Lease(provider_id, self._active[provider_id], NexusContext(), pins)

    def acquire(self, capability_id, version, session):
        descriptor = self.catalog.get_capability(capability_id, version)
        with self._lock, connect_db(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            lease = self._acquire(db, descriptor["provider_id"])
            lease.context.state.update(capability_id=capability_id, version=version, session=session)
            db.commit()
            return lease

    def release(self, lease):
        with self._lock, connect_db(self.db_path) as db:
            if lease.released:
                return
            db.execute("BEGIN IMMEDIATE")
            for pin in reversed(lease.pins):
                db.execute("UPDATE provider_generations SET lease_count=lease_count-1 "
                           "WHERE provider_id=? AND generation=? AND lease_count>0", pin)
            db.commit()
            lease.released = True
            lease.context.dispose()
            for pin in reversed(lease.pins):
                self._dispose_drained(db, pin)

    def invoke(self, request, principal):
        validate_request(request)
        request = json.loads(canonical(request))
        descriptor = self.catalog.get_capability(request["capability_id"], request["version"])
        current = self.catalog.get_capability(request["capability_id"])
        descriptor = {**descriptor, **{key: current[key] for key in
                      ("availability", "reason", "execution_policy", "visibility") if key in current}}
        with self._lock, connect_db(self.db_path) as db:
            snapshot = {key: "active" for key in self._active}
            authorize(db, request, principal, descriptor, {"provider_snapshot": snapshot})
            db.execute("BEGIN IMMEDIATE")
            previous = existing_invocation(db, request, principal)
            if previous is not None:
                return receipt(previous)
            if descriptor["kind"] != "tool":
                raise CapabilityError("INVALID_ARGUMENT", "Only shared tools execute in the runtime")
            invocation_id = str(uuid.uuid4())
            lease = self._acquire(db, descriptor["provider_id"])
            lease.context.state.update(invocation_id=invocation_id, capability_id=request["capability_id"],
                                       version=request["version"], method=descriptor.get("method"),
                                       session_id=request["session_id"])
            provider = self._generations[(lease.provider_id, lease.generation)]
            if not provider["slots"].acquire(blocking=False):
                raise CapabilityError("UNAVAILABLE", "Provider concurrency limit reached", 503)
            try:
                insert_invocation(db, request, principal, invocation_id, lease.generation, "planned")
                db.execute("UPDATE capability_invocations SET state='running' WHERE invocation_id=?", (invocation_id,))
                append_event(db, invocation_id, request["session_id"], "invocation_started", {})
                db.commit()
            except BaseException:
                provider["slots"].release()
                raise
        done = threading.Event()
        def execute():
            try:
                fn = provider["handler"]
                fn = fn if callable(fn) else fn.invoke
                result = fn(request["arguments"], lease.context)
                canonical(result)
                self._finish(invocation_id, "succeeded", result=result)
            except BaseException as exc:
                unknown = isinstance(exc, (ConnectionError, TimeoutError, OSError))
                self._finish(invocation_id, "unknown" if unknown else "failed", error={
                    "code": "UNKNOWN_OUTCOME" if unknown else "PROVIDER_FAILED",
                    "message": "Provider acknowledgement lost" if unknown else "Provider execution failed"})
            finally:
                self.release(lease)
                provider["slots"].release()
                done.set()
        threading.Thread(target=execute, daemon=True, name="capability-invocation").start()
        if not done.wait(provider["timeout"]):
            self._finish(invocation_id, "unknown", error={"code": "PROVIDER_TIMEOUT",
                         "message": "Deadline elapsed; execution may still be in progress"})
        with connect_db(self.db_path) as db:
            return receipt(db.execute("SELECT * FROM capability_invocations WHERE invocation_id=?", (invocation_id,)).fetchone())

    def _finish(self, invocation_id, state, result=None, error=None):
        with connect_db(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM capability_invocations WHERE invocation_id=?", (invocation_id,)).fetchone()
            if row["state"] not in ("running", "planned"):
                return
            event_id = append_event(db, invocation_id, row["session_id"], "invocation_" + state, {})
            payload = {"state": state, "result": result, "error": error, "event_id": event_id}
            db.execute("UPDATE capability_invocations SET state=?,result_ref=? WHERE invocation_id=?",
                       (state, canonical(payload), invocation_id))
            db.commit()

    def drain(self, provider_id=None):
        with self._lock, connect_db(self.db_path) as db:
            if provider_id is None:
                self._closed = True
            targets = list(self._active) if provider_id is None else [provider_id]
            for target in targets:
                generation = self._active.pop(target, None)
                if generation is not None:
                    db.execute("UPDATE provider_generations SET state='draining' WHERE provider_id=? AND generation=?",
                               (target, generation))
                    db.commit()
                    self._dispose_drained(db, (target, generation))
            return {"state": "draining", "providers": targets}

    def reconcile_provider(self, provider_id, new_descriptor, handler=None):
        if handler is None:
            raise CapabilityError("UNAVAILABLE", "Replacement requires an explicitly injected handler", 503)
        return self.register_provider(provider_id, handler, new_descriptor)

    def recover_inflight(self):
        """Call on exclusive daemon startup, before registering handlers; never replay."""
        with self._lock, connect_db(self.db_path) as db:
            if self._generations:
                raise CapabilityError("CONFLICT", "Recovery requires an unstarted runtime", 409)
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM capability_invocations WHERE state IN ('planned','running')").fetchall()
            for row in rows:
                event_id = append_event(db, row["invocation_id"], row["session_id"], "invocation_unknown", {"recovered": True})
                payload = {"state": "unknown", "result": None, "error": {"code": "UNKNOWN_OUTCOME"}, "event_id": event_id}
                db.execute("UPDATE capability_invocations SET state='unknown',result_ref=? WHERE invocation_id=?",
                           (canonical(payload), row["invocation_id"]))
            db.execute("UPDATE provider_generations SET state='failed',lease_count=0 WHERE state IN ('loading','active','draining')")
            db.commit()
            return {"recovered": len(rows), "state": "unknown"}

    recover = recover_inflight
    close = drain
