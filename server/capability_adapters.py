"""Signed, durable correlations for actions executed by an authenticated harness."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid

from server.capability_catalog import connect_db, initialize_schema
from server.capability_runtime import (
    CapabilityError, append_event, arguments_hash, authorize, canonical, epoch,
    existing_invocation, insert_invocation, receipt, validate_request,
)


class CapabilityAdapters:
    """Negotiated features are explicit claims from an authenticated transport.

    No harness gets assumed native verbs. Registrations are process-owned and
    must be renegotiated after restart; envelopes and accepted receipts persist.
    prepare_native_action takes the invocation request plus principal (trusted
    identity injected by the facade). accept_native_receipt takes
    {envelope: <the complete issued envelope>, state: succeeded|failed, result}.
    A receipt is an execution claim, never a verified quality reward.
    """

    def __init__(self, db_path, secret: bytes):
        if not isinstance(secret, bytes) or not secret:
            raise CapabilityError("INVALID_ARGUMENT", "A nonempty signing secret is required")
        self.db_path, self.secret = db_path, secret
        self._profiles, self._lock = {}, threading.RLock()
        initialize_schema(db_path)

    def register_harness(self, identity, features):
        if not isinstance(identity, dict) or not identity.get("id") or not identity.get("harness"):
            raise CapabilityError("UNAUTHORIZED", "Authenticated identity required", 401)
        if not isinstance(features, dict):
            raise CapabilityError("INVALID_ARGUMENT", "features must be an object")
        verbs = features.get("permitted_native_verbs", features.get("native_verbs", []))
        media = features.get("result_media_types", ["application/json"])
        if any(not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values)
               for values in (verbs, media)):
            raise CapabilityError("INVALID_ARGUMENT", "Verbs and media types must be string lists")
        profile = {"principal_id": identity["id"], "harness": identity["harness"],
                   "adapter_id": features.get("adapter_id", identity["harness"]),
                   "dynamic_catalog": features.get("dynamic_catalog") is True,
                   "skill_reads": features.get("skill_reads") is True,
                   "resource_reads": features.get("resource_reads") is True,
                   "permitted_native_verbs": sorted(set(verbs)), "result_media_types": sorted(set(media))}
        with self._lock:
            self._profiles[(identity["id"], identity["harness"])] = json.loads(canonical(profile))
        return profile

    def _sign(self, payload):
        return hmac.new(self.secret, canonical(payload).encode(), hashlib.sha256).hexdigest()

    def prepare_native_action(self, invocation):
        validate_request(invocation)
        invocation = json.loads(canonical(invocation))
        principal = invocation.get("principal")
        if not isinstance(principal, dict):
            raise CapabilityError("UNAUTHORIZED", "Facade must inject authenticated principal", 401)
        with self._lock, connect_db(self.db_path) as db:
            row = db.execute("SELECT v.descriptor_json,c.kind,c.metadata_json FROM capability_versions v "
                             "JOIN capability_catalog c USING(capability_id) WHERE v.capability_id=? AND v.version=?",
                             (invocation["capability_id"], invocation["version"])).fetchone()
            if row is None:
                raise CapabilityError("NOT_FOUND", "Capability version not found", 404)
            descriptor = json.loads(row["descriptor_json"])
            current = json.loads(row["metadata_json"])
            descriptor.update({key: current[key] for key in
                               ("availability", "reason", "execution_policy", "visibility") if key in current})
            descriptor.update(kind=row["kind"], version=invocation["version"], canonical_id=invocation["capability_id"])
            if descriptor["kind"] != "native_action":
                raise CapabilityError("INVALID_ARGUMENT", "Capability is not a native action")
            profile = self._profiles.get((principal.get("id"), principal.get("harness")))
            verb = descriptor.get("method", descriptor.get("verb"))
            if (profile is None or verb not in profile["permitted_native_verbs"]
                    or descriptor.get("adapter_id") != profile["adapter_id"]):
                raise CapabilityError("UNSUPPORTED", "Native verb was not negotiated", 422)
            session = authorize(db, invocation, principal, descriptor,
                                {"adapters": {profile["adapter_id"]: profile}, "provider_snapshot": {}})
            db.execute("BEGIN IMMEDIATE")
            previous = existing_invocation(db, invocation, principal)
            if previous is not None:
                event = db.execute("SELECT payload_json FROM capability_events WHERE invocation_id=? "
                                   "AND type='native_envelope'", (previous["invocation_id"],)).fetchone()
                if event is None:
                    raise CapabilityError("CONFLICT", "Key belongs to a shared execution", 409)
                return json.loads(event["payload_json"])
            invocation_id, envelope_id = str(uuid.uuid4()), str(uuid.uuid4())
            envelope = {"envelope_id": envelope_id, "invocation_id": invocation_id,
                        "principal_id": principal["id"], "harness_id": principal["harness"],
                        "session_id": invocation["session_id"], "capability_id": invocation["capability_id"],
                        "version": invocation["version"], "args_hash": arguments_hash(invocation["arguments"]),
                        "arguments": invocation["arguments"], "verb": verb,
                        "expires_at": min(time.time() + 300, epoch(session["expires_at"])),
                        "policy_decision": {"allowed": True, "project": session["project"]},
                        "required_result_schema": {"state": ["succeeded", "failed"], "verified": False},
                        "result_media_types": profile["result_media_types"]}
            envelope["signature"] = self._sign(envelope)
            insert_invocation(db, invocation, principal, invocation_id, None, "native_pending")
            append_event(db, invocation_id, invocation["session_id"], "native_envelope", envelope)
            db.commit()
            return envelope

    def accept_native_receipt(self, envelope_id, native_receipt, principal):
        if not isinstance(native_receipt, dict) or len(canonical(native_receipt).encode()) > 1024 * 1024:
            raise CapabilityError("INVALID_ARGUMENT", "Expected bounded JSON receipt")
        envelope = native_receipt.get("envelope")
        if not isinstance(envelope, dict) or envelope.get("envelope_id") != envelope_id:
            raise CapabilityError("INVALID_ARGUMENT", "Complete signed envelope required")
        payload = {k: v for k, v in envelope.items() if k != "signature"}
        signature = envelope.get("signature")
        if not isinstance(signature, str) or not hmac.compare_digest(signature, self._sign(payload)):
            raise CapabilityError("FORBIDDEN", "Envelope signature mismatch", 403)
        if not isinstance(principal, dict) or not principal.get("id"):
            raise CapabilityError("UNAUTHORIZED", "Authenticated identity required", 401)
        if (principal.get("id"), principal.get("harness")) != (envelope["principal_id"], envelope["harness_id"]):
            raise CapabilityError("FORBIDDEN", "Envelope targets another principal or harness", 403)
        if native_receipt.get("state") not in ("succeeded", "failed"):
            raise CapabilityError("INVALID_ARGUMENT", "Expected terminal execution state")
        if arguments_hash(envelope["arguments"]) != envelope["args_hash"]:
            raise CapabilityError("FORBIDDEN", "Argument hash mismatch", 403)
        with self._lock, connect_db(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            stored = db.execute("SELECT payload_json FROM capability_events WHERE invocation_id=? "
                                "AND type='native_envelope'", (envelope["invocation_id"],)).fetchone()
            if stored is None or canonical(json.loads(stored[0])) != canonical(envelope):
                raise CapabilityError("FORBIDDEN", "Envelope is not the durable issued action", 403)
            row = db.execute("SELECT * FROM capability_invocations WHERE invocation_id=?",
                             (envelope["invocation_id"],)).fetchone()
            if any(row[k] != envelope[k] for k in ("principal_id", "session_id", "capability_id", "version", "args_hash")):
                raise CapabilityError("FORBIDDEN", "Invocation binding mismatch", 403)
            session = db.execute("SELECT * FROM capability_sessions WHERE session_id=?",
                                 (envelope["session_id"],)).fetchone()
            if (session is None or session["principal_id"] != principal["id"]
                    or session["harness_id"] != principal.get("harness")):
                raise CapabilityError("FORBIDDEN", "Session ownership mismatch", 403)
            if "*" not in principal.get("projects", []) and session["project"] not in principal.get("projects", []):
                raise CapabilityError("FORBIDDEN", "Project is outside principal scope", 403)
            # Exact transport retries return the first claim even after envelope expiry.
            if row["state"] in ("succeeded", "failed"):
                return receipt(row)
            if row["state"] != "native_pending":
                raise CapabilityError("CONFLICT", "Invocation cannot accept a native claim", 409)
            if (epoch(envelope["expires_at"]) <= time.time() or session["status"] != "active"
                    or epoch(session["expires_at"]) <= time.time()):
                raise CapabilityError("EXPIRED", "Native action has expired", 410)
            event_id = append_event(db, envelope["invocation_id"], envelope["session_id"],
                                    "native_execution_claim", {"state": native_receipt["state"], "verified": False})
            result = {"state": native_receipt["state"], "result": native_receipt.get("result"),
                      "error": native_receipt.get("error"), "verified": False, "event_id": event_id}
            db.execute("UPDATE capability_invocations SET state=?,result_ref=? WHERE invocation_id=?",
                       (result["state"], canonical(result), envelope["invocation_id"]))
            db.commit()
            return receipt(db.execute("SELECT * FROM capability_invocations WHERE invocation_id=?",
                                      (envelope["invocation_id"],)).fetchone())
