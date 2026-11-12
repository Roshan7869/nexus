"""Pure capability policy decisions; transport supplies authenticated principals."""
import hashlib
import json
import math
import time
from datetime import datetime

POLICY_VERSION = "1"


def argument_hash(args):
    """Canonical argument digest shared by approvals and runtime idempotency."""
    return hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _decision(allowed, reason):
    return {"allowed": bool(allowed), "reason": reason}


def _timestamp(value):
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _scope(scopes, value):
    return isinstance(scopes, (list, tuple)) and ("*" in scopes or value in scopes)


def visibility(principal, descriptor):
    """Visibility restricts discovery without granting execution permission."""
    if not isinstance(principal, dict) or not principal.get("id"):
        return _decision(False, "UNAUTHENTICATED")
    rule = descriptor.get("visibility", "public")
    if rule == "public":
        return _decision(True, "VISIBLE")
    if not isinstance(rule, dict):
        return _decision(False, "HIDDEN")
    if rule.get("principals") and not _scope(rule["principals"], principal["id"]):
        return _decision(False, "HIDDEN")
    if rule.get("harnesses") and not _scope(rule["harnesses"], principal.get("harness")):
        return _decision(False, "HIDDEN")
    if rule.get("projects"):
        projects = principal.get("projects", [])
        if "*" not in projects and not any(_scope(rule["projects"], p) for p in projects):
            return _decision(False, "HIDDEN")
    return _decision(bool(rule), "VISIBLE" if rule else "HIDDEN")


def eligibility(context, descriptor, provider_snapshot):
    """Filter using a cached provider snapshot; no execution or provider probing."""
    principal = context.get("principal", context)
    visible = visibility(principal, descriptor)
    if not visible["allowed"]:
        return visible
    if not _scope(principal.get("projects", []), context.get("project")):
        return _decision(False, "PROJECT_DENIED")
    if descriptor.get("availability", "available") not in ("available", "active", True):
        return _decision(False, descriptor.get("reason") or "UNAVAILABLE")
    policy = descriptor.get("execution_policy", {})
    if descriptor.get("kind") != "skill" and policy.get("allow") is not True:
        return _decision(False, "EXECUTION_DENIED")
    if policy.get("projects") and not _scope(policy["projects"], context.get("project")):
        return _decision(False, "PROJECT_DENIED")
    if policy.get("harnesses") and not _scope(policy["harnesses"], principal.get("harness")):
        return _decision(False, "HARNESS_DENIED")
    if policy.get("principals") and not _scope(policy["principals"], principal.get("id")):
        return _decision(False, "EXECUTION_DENIED")
    if descriptor.get("kind") == "native_action":
        adapters = context.get("adapters", {})
        adapter = adapters.get(descriptor.get("adapter_id"), {})
        verb = descriptor.get("method", descriptor.get("verb"))
        if (not adapter or verb not in adapter.get("permitted_native_verbs", [])
                or adapter.get("harness", principal.get("harness")) != principal.get("harness")):
            return _decision(False, "INCOMPATIBLE_ADAPTER")
    required = list(descriptor.get("requirements", []))
    if descriptor.get("kind") == "tool" and descriptor.get("provider_id"):
        required.append(descriptor["provider_id"])
    for dependency in required:
        state = provider_snapshot.get(dependency)
        if isinstance(state, dict):
            state = state.get("state", state.get("availability"))
        if state not in (True, "active", "available"):
            return _decision(False, "DEPENDENCY_UNAVAILABLE")
    return _decision(True, "ELIGIBLE")


def _validate(value, schema, depth=0):
    """Use the installed JSON Schema validator, failing closed if unavailable."""
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def authorize_invocation(principal, session, descriptor, args, approval=None):
    """Authorize a pinned invocation, independent of recommendation membership.

    approval supplies only an id. The corresponding verified record must be in
    context['_verified_approvals'], populated only by trusted server code. Never
    copy this reserved context field from a request. Bind harness and session.
    """
    session = dict(session)
    if (session.get("principal_id") != principal.get("id")
            or session.get("harness_id") != principal.get("harness")):
        return _decision(False, "SESSION_OWNER_MISMATCH")
    try:
        expires = _timestamp(session.get("expires_at"))
        if session.get("status") != "active" or not math.isfinite(expires) or expires <= time.time():
            return _decision(False, "SESSION_EXPIRED")
    except (TypeError, ValueError, OverflowError):
        return _decision(False, "SESSION_EXPIRED")
    context = session.get("context", session.get("context_json", {}))
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except ValueError:
            return _decision(False, "INVALID_CONTEXT")
    context = {**context, "project": session.get("project"), "principal": principal}
    if str(context.get("policy_version", POLICY_VERSION)) != POLICY_VERSION:
        return _decision(False, "POLICY_CHANGED")
    eligible = eligibility(context, descriptor, context.get("provider_snapshot", {}))
    if not eligible["allowed"]:
        return eligible
    if not descriptor.get("version"):
        return _decision(False, "VERSION_REQUIRED")
    policy = descriptor.get("execution_policy", {})
    if policy.get("principals") and not _scope(policy["principals"], principal.get("id")):
        return _decision(False, "EXECUTION_DENIED")
    method = descriptor.get("method", descriptor.get("verb"))
    if descriptor.get("kind") != "skill" and (not method or method not in policy.get("methods", [])):
        return _decision(False, "METHOD_DENIED")
    try:
        encoded = json.dumps(args, allow_nan=False).encode()
        if not isinstance(args, dict) or len(encoded) > 65536:
            return _decision(False, "INVALID_ARGUMENTS")
        schema = descriptor.get("input_schema", descriptor.get("inputSchema"))
        if descriptor.get("kind") != "skill" and not isinstance(schema, dict):
            return _decision(False, "SCHEMA_REQUIRED")
        _validate(args, schema or {"type": "object"})
    except Exception:
        return _decision(False, "INVALID_ARGUMENTS")
    if policy.get("approval_required"):
        expected = {"principal_id": principal.get("id"), "harness_id": principal.get("harness"),
                    "session_id": session.get("session_id"), "project": session.get("project"),
                    "capability_id": descriptor.get("canonical_id", descriptor.get("capability_id")),
                    "version": descriptor["version"], "args_hash": argument_hash(args)}
        approval_id = approval.get("id") if isinstance(approval, dict) else approval
        records = context.get("_verified_approvals", {})
        approved = records.get(approval_id) if isinstance(approval_id, str) and isinstance(records, dict) else None
        if not isinstance(approved, dict) or approved.get("verified") is not True:
            return _decision(False, "APPROVAL_REQUIRED")
        try:
            expiry = _timestamp(approved.get("expires_at"))
            if (not math.isfinite(expiry) or expiry <= time.time()
                    or any(approved.get(k) != v for k, v in expected.items())):
                return _decision(False, "APPROVAL_INVALID")
        except (TypeError, ValueError, OverflowError):
            return _decision(False, "APPROVAL_INVALID")
    return _decision(True, "AUTHORIZED")
