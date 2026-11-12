"""Append-only usage and verified, invocation-scoped learning on an injected DB."""
from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime, timezone

from server.capability_catalog import connect_db


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _expired(value):
    try:
        epoch = float(value)
        return not math.isfinite(epoch) or epoch <= time.time()
    except (ValueError, TypeError):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=timezone.utc) <= datetime.now(timezone.utc)


def _session(conn, session_id, principal=None, active=True):
    row = conn.execute("SELECT * FROM capability_sessions WHERE session_id=?",
                       (session_id,)).fetchone()
    if row is None:
        raise ValueError("SESSION_NOT_FOUND")
    session = dict(row)
    if principal is not None:
        if (principal.get("id") != session["principal_id"] or
                principal.get("harness") != session["harness_id"]):
            raise PermissionError("SESSION_OWNER_MISMATCH")
        projects = principal.get("projects", [])
        if "*" not in projects and session["project"] not in projects:
            raise PermissionError("PROJECT_DENIED")
    if active and (session["status"] != "active" or _expired(session["expires_at"])):
        raise ValueError("SESSION_INACTIVE")
    session["context"] = json.loads(session["context_json"])
    return session


def _append(conn, event):
    event = dict(event)
    event_id = event.get("event_id") or uuid.uuid4().hex
    session_id = event["session_id"]
    invocation_id = event.get("invocation_id")
    _session(conn, session_id, active=False)
    if invocation_id:
        inv = conn.execute("SELECT session_id FROM capability_invocations WHERE invocation_id=?",
                           (invocation_id,)).fetchone()
        if inv is None or inv["session_id"] != session_id:
            raise ValueError("INVOCATION_SESSION_MISMATCH")
    payload = _json(event.get("payload", {}))
    previous = conn.execute("SELECT * FROM capability_events WHERE event_id=?",
                            (event_id,)).fetchone()
    if previous:
        if (previous["session_id"], previous["invocation_id"], previous["type"],
                previous["payload_json"]) != (session_id, invocation_id, event["type"], payload):
            raise ValueError("EVENT_CONFLICT")
        return event_id
    conn.execute("INSERT INTO capability_events "
                 "(event_id,invocation_id,session_id,type,payload_json,created_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (event_id, invocation_id, session_id, event["type"], payload, time.time()))
    return event_id


class CapabilityEvents:
    """Callers supply authenticated principal/verifier dictionaries, never body claims.

    Evidence is a dictionary binding invocation_id, capability_id and version.
    Binary evidence also requires a nonempty evidence_ref (or ref).
    """

    def __init__(self, db_path):
        if db_path is None:
            raise ValueError("db_path is required")
        self.db_path = db_path

    def open_session(self, principal, context=None, session_id=None, ttl_seconds=3600):
        context = dict(context or {})
        if not principal.get("id") or not principal.get("harness"):
            raise PermissionError("AUTHENTICATED_PRINCIPAL_REQUIRED")
        if not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 86400:
            raise ValueError("INVALID_SESSION_TTL")
        project = context.get("project", "default")
        if "*" not in principal.get("projects", []) and project not in principal.get("projects", []):
            raise PermissionError("PROJECT_DENIED")
        session_id = session_id or uuid.uuid4().hex
        with connect_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT session_id FROM capability_sessions WHERE session_id=?",
                                    (session_id,)).fetchone()
            if existing:
                session = _session(conn, session_id, principal)
                if session["context"] != context:
                    raise ValueError("SESSION_CONTEXT_CONFLICT")
                return session
            conn.execute("INSERT INTO capability_sessions "
                         "(session_id,principal_id,harness_id,project,context_json,status,expires_at) "
                         "VALUES (?,?,?,?,?,'active',?)",
                         (session_id, principal["id"], principal["harness"], project,
                          _json(context), time.time() + ttl_seconds))
            return _session(conn, session_id, principal)

    def append_event(self, event):
        if event.get("type") == "outcome_verified":
            raise PermissionError("USE_RECORD_VERIFIED_OUTCOME")
        with connect_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            return _append(conn, event)

    def record_verified_outcome(self, invocation_id, verdict, evidence, verifier):
        if not isinstance(verifier, dict) or verifier.get("verifier") is not True or not verifier.get("id"):
            raise PermissionError("TRUSTED_VERIFIER_REQUIRED")
        if verdict not in {"correct", "wrong", "partial", "skip", "timeout", "cancelled"}:
            raise ValueError("INVALID_VERDICT")
        if not isinstance(evidence, dict):
            raise ValueError("BOUND_EVIDENCE_REQUIRED")
        with connect_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM capability_invocations WHERE invocation_id=?",
                               (invocation_id,)).fetchone()
            if row is None:
                raise ValueError("INVOCATION_NOT_FOUND")
            inv = dict(row)
            session = _session(conn, inv["session_id"], active=False)
            projects = verifier.get("projects", [])
            if "*" not in projects and session["project"] not in projects:
                raise PermissionError("PROJECT_DENIED")
            for key in ("invocation_id", "capability_id", "version"):
                if evidence.get(key) != inv[key]:
                    raise ValueError("EVIDENCE_BINDING_MISMATCH")
            if inv["state"] not in {"succeeded", "failed"}:
                raise ValueError("INVOCATION_NOT_EXECUTED")
            if verdict not in {"correct", "wrong"}:
                event_id = _append(conn, {"event_id": evidence.get("event_id"),
                    "session_id": inv["session_id"], "invocation_id": invocation_id,
                    "type": "outcome_observed", "payload": {"verdict": verdict, "evidence": evidence}})
                return {"invocation_id": invocation_id, "event_id": event_id,
                        "verdict": verdict, "reward_applied": False}
            evidence_ref = evidence.get("evidence_ref", evidence.get("ref"))
            if not isinstance(evidence_ref, str) or not evidence_ref.strip():
                raise ValueError("EVIDENCE_REF_REQUIRED")
            old = conn.execute("SELECT * FROM capability_rewards WHERE invocation_id=?",
                               (invocation_id,)).fetchone()
            event_id = "verified:" + invocation_id
            if old:
                if (old["verdict"], old["evidence_ref"], old["verified_by"]) != (
                        verdict, evidence_ref, verifier["id"]):
                    raise ValueError("REWARD_CONFLICT")
                saved = conn.execute("SELECT payload_json FROM capability_events WHERE event_id=?",
                                     (event_id,)).fetchone()
                return json.loads(saved["payload_json"])
            context = session["context"]
            if inv.get("decision_id"):
                decision = conn.execute("SELECT session_id,draws_json FROM recommendation_decisions WHERE decision_id=?",
                                        (inv["decision_id"],)).fetchone()
                if decision:
                    if decision["session_id"] != inv["session_id"]:
                        raise ValueError("DECISION_SESSION_MISMATCH")
                    logged = json.loads(decision["draws_json"])
                    if isinstance(logged, dict):
                        context = logged.get("context", context)
            niche_version = str(context.get("niche_version", "cluster-keywords-v1"))
            niche = str(context.get("niche", "general"))
            conn.execute("INSERT INTO capability_rewards "
                         "(invocation_id,verdict,evidence_ref,verified_by,created_at) VALUES (?,?,?,?,?)",
                         (invocation_id, verdict, evidence_ref, verifier["id"], time.time()))
            success, failure = int(verdict == "correct"), int(verdict == "wrong")
            conn.execute("INSERT INTO capability_beliefs "
                         "(capability_id,version,niche_version,niche,successes,failures) VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(capability_id,version,niche_version,niche) DO UPDATE SET "
                         "successes=successes+excluded.successes, failures=failures+excluded.failures",
                         (inv["capability_id"], inv["version"], niche_version, niche, success, failure))
            belief = dict(conn.execute("SELECT * FROM capability_beliefs WHERE "
                "capability_id=? AND version=? AND niche_version=? AND niche=?",
                (inv["capability_id"], inv["version"], niche_version, niche)).fetchone())
            receipt = {"invocation_id": invocation_id, "event_id": event_id, "verdict": verdict,
                       "reward_applied": True, "belief": belief}
            _append(conn, {"event_id": event_id, "session_id": inv["session_id"],
                          "invocation_id": invocation_id, "type": "outcome_verified", "payload": receipt})
            return receipt

    def close_session(self, session_id, task_verdict=None, principal=None):
        with connect_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _session(conn, session_id, principal, active=False)
            event_id = _append(conn, {"event_id": "closed:" + session_id,
                "session_id": session_id, "type": "session_closed", "payload": {"task_verdict": task_verdict}})
            conn.execute("UPDATE capability_sessions SET status='closed' WHERE session_id=?", (session_id,))
            return {"session_id": session_id, "status": "closed", "event_id": event_id}

    def metrics(self, context=None):
        context = dict(context or {})
        filters, params = [], []
        for key in ("session_id", "project", "principal_id"):
            if key in context:
                filters.append("s." + key + "=?")
                params.append(context[key])
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with connect_db(self.db_path) as conn:
            events = conn.execute("SELECT e.type, COUNT(*) AS n FROM capability_events e "
                "JOIN capability_sessions s USING(session_id)" + where + " GROUP BY e.type", params).fetchall()
            counts = {row["type"]: row["n"] for row in events}
            invs = conn.execute("SELECT i.state,COUNT(*) AS n FROM capability_invocations i "
                "JOIN capability_sessions s USING(session_id)" + where + " GROUP BY i.state", params).fetchall()
            states = {row["state"]: row["n"] for row in invs}
            rewards = conn.execute("SELECT r.verdict,COUNT(*) AS n FROM capability_rewards r "
                "JOIN capability_invocations i USING(invocation_id) "
                "JOIN capability_sessions s USING(session_id)" + where + " GROUP BY r.verdict", params).fetchall()
            binary = {row["verdict"]: row["n"] for row in rewards}
            legacy_columns = {row["name"] for row in conn.execute("PRAGMA table_info(resources)")}
            legacy_keys = [key for key in ("times_triggered", "times_correct", "times_wrong")
                           if key in legacy_columns]
            legacy = {}
            if legacy_keys:
                sums = conn.execute("SELECT " + ",".join("SUM(" + key + ")" for key in legacy_keys)
                                    + " FROM resources").fetchone()
                legacy = dict(zip(legacy_keys, sums))
        successes, failures = binary.get("correct", 0), binary.get("wrong", 0)
        return {"exposure_count": counts.get("recommended", 0), "selection_count": counts.get("selected", 0),
                "skill_read_count": counts.get("skill_read", 0), "invocation_count": sum(states.values()),
                "terminal_outcome_count": states.get("succeeded", 0) + states.get("failed", 0),
                "unknown_outcome_count": states.get("unknown", 0), "successes": successes, "failures": failures,
                "success_posterior": (1 + successes) / (2 + successes + failures),
                "events": counts, "legacy": {"label": "legacy", "included_in_quality": False,
                                               "counters": legacy, "scope": "all_resources"}}
