"""Contract tests for the stdio MCP facade over the Shared Capability API."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.capability_api import CapabilityServiceGraph, create_capability_router
from server.capability_catalog import CapabilityCatalog, connect_db, initialize_schema
from server.capability_adapters import CapabilityAdapters
from server.capability_events import CapabilityEvents
from server.capability_recommender import CapabilityRecommender
from server.capability_runtime import CapabilityRuntime, arguments_hash
from server.ephemeral_mcp_proxy import CAPABILITY_BASELINE_TOOLS


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    db_path = tmp_path / "capability-bridge.db"
    skill_root = tmp_path / "skills" / "python_debug"
    skill_root.mkdir(parents=True)
    skill_text = "# Python Debugger\n\nRead this verified test skill.\n"
    (skill_root / "SKILL.md").write_text(skill_text)
    skill_hash = hashlib.sha256(skill_text.encode()).hexdigest()

    tool = {
        "canonical_id": "test/calculator",
        "kind": "tool",
        "version": "v1",
        "provider_id": "math_provider",
        "method": "add",
        "availability": "available",
        "name": "Calculator",
        "description": "Add two integers",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        "execution_policy": {"allow": True, "methods": ["add"]},
    }
    skill = {
        "canonical_id": "test/python_debug",
        "kind": "skill",
        "version": skill_hash,
        "availability": "available",
        "publisher": "test",
        "aliases": [],
        "name": "Python Debugger",
        "description": "Debug Python stack traces",
        "_root": str(skill_root),
        "_base_root": str(skill_root),
        "_files": {"SKILL.md": skill_hash},
    }
    initialize_schema(db_path)
    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        for desc in (tool, skill):
            public = {k: v for k, v in desc.items() if not k.startswith("_")}
            db.execute(
                "INSERT INTO capability_catalog VALUES (?,?,?,?,?,?)",
                (desc["canonical_id"], desc["kind"], "test", desc["canonical_id"],
                 desc["version"], json.dumps(public)),
            )
            db.execute(
                "INSERT INTO capability_versions VALUES (?,?,?,?,?)",
                (desc["canonical_id"], desc["version"], arguments_hash(desc),
                 json.dumps(desc), 1.0),
            )
        db.execute("INSERT INTO catalog_revisions VALUES (1, 'bridge-test', ?)", (1.0,))
        for desc in (tool, skill):
            public = {k: v for k, v in desc.items() if not k.startswith("_")}
            db.execute(
                "INSERT INTO catalog_revision_members VALUES (1, ?, ?, 'available', ?)",
                (desc["canonical_id"], desc["version"], json.dumps(public)),
            )
        db.commit()

    catalog = CapabilityCatalog(db_path)
    runtime = CapabilityRuntime(db_path, catalog)
    calls = []
    runtime.register_provider(
        "math_provider",
        lambda args, _ctx: calls.append(dict(args)) or {"sum": args["a"] + args["b"]},
    )
    graph = CapabilityServiceGraph(
        catalog, runtime, CapabilityRecommender(db_path, catalog),
        CapabilityEvents(db_path), CapabilityAdapters(db_path, secret=b"bridge-test-secret"),
    )
    app = FastAPI()
    app.include_router(create_capability_router(graph))
    client = TestClient(app)

    # Adapt the stdlib urllib bridge to the real FastAPI router in-process.
    requested = []

    def urlopen(req, timeout=5.0):
        requested.append(req)
        parsed = urlsplit(req.full_url)
        response = client.request(
            req.get_method(), parsed.path + ("?" + parsed.query if parsed.query else ""), content=req.data,
            headers={k: v for k, v in req.header_items()},
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                req.full_url, response.status_code, response.reason_phrase,
                response.headers, io.BytesIO(response.content),
            )
        return io.BytesIO(response.content)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("NEXUS_DAEMON_URL", "http://capability.test")
    monkeypatch.setenv("NEXUS_IDE", "codex")
    spec = importlib.util.spec_from_file_location(
        "nexus_mcp_capability_bridge_test",
        Path(__file__).resolve().parents[1] / "server" / "nexus-mcp-enhanced.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, client, requested, calls, db_path


def _json_call(module, name, *args, **kwargs):
    return json.loads(getattr(module, name)(*args, **kwargs))


def test_search_and_skill_read_resolve_exact_descriptor_version(bridge):
    module, _, requested, _, _ = bridge
    result = _json_call(module, "capability_search", "python_debug")
    descriptor = next(item for item in result["page"]["items"]
                      if item["canonical_id"] == "test/python_debug")
    assert descriptor["version"] == skill_hash

    content = _json_call(module, "skill_read", "test/python_debug", path="")
    assert content["skill"]["version"] == skill_hash
    assert content["skill"]["path"] == "SKILL.md"
    assert "verified test skill" in content["skill"]["content"]
    skill_request = requested[-1]
    assert f"/skills/test%2Fpython_debug/content?version={skill_hash}&path=SKILL.md" in skill_request.full_url
    assert skill_request.get_header("X-principal-id") == "nexus-codex"
    assert skill_request.get_header("X-harness-id") == "codex"


def test_session_recommend_and_invoke_idempotency_and_close(bridge):
    module, client, _, provider_calls, db_path = bridge
    opened = _json_call(module, "capability_session_open", project="demo")
    session_id = opened["session"]["session_id"]
    assert opened["session"]["harness_id"] == "codex"

    recommendation = _json_call(
        module, "capability_recommend", session_id, "calculator", k=3, seed=7,
    )
    assert recommendation["recommendation"]["recommendations"]

    one = _json_call(
        module, "capability_invoke", "test/calculator", method="add",
        args={"a": 2, "b": 3}, session_id=session_id,
        version="v1", idempotency_key="stable-key",
    )
    two = _json_call(
        module, "capability_invoke", "test/calculator", method="add",
        args={"a": 2, "b": 3}, session_id=session_id,
        version="v1", idempotency_key="stable-key",
    )
    assert one["receipt"]["invocation_id"] == two["receipt"]["invocation_id"]
    assert one["receipt"]["result"] == {"sum": 5}
    assert provider_calls == [{"a": 2, "b": 3}]

    changed = _json_call(
        module, "capability_invoke", "test/calculator", method="add",
        args={"a": 4, "b": 3}, session_id=session_id,
        version="v1", idempotency_key="stable-key",
    )
    assert changed["error"] is True and changed["status"] == 409

    mismatch = _json_call(
        module, "capability_invoke", "test/calculator", method="subtract",
        args={"a": 1, "b": 1}, session_id=session_id,
        version="v1", idempotency_key="other-key",
    )
    assert mismatch["error"] is True and mismatch["status"] == "INVALID_ARGUMENT"

    foreign_close = client.post(
        f"/capabilities/v1/sessions/{session_id}/close",
        json={"outcome": "correct", "notes": "foreign"},
        headers={"x-principal-id": "someone-else", "x-harness-id": "codex"},
    )
    assert foreign_close.status_code == 403

    closed = _json_call(
        module, "capability_session_close", session_id,
        evidence={"invocation_id": "unverified"}, notes="done",
    )
    assert closed["closed"]["status"] == "closed"
    with connect_db(db_path) as db:
        event = db.execute(
            "SELECT payload_json FROM capability_events WHERE event_id=?",
            ("closed:" + session_id,),
        ).fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["task_verdict"] == "correct"
    assert "Legacy evidence payload (not independently verified)" in payload["notes"]
    rejected = _json_call(
        module, "capability_invoke", "test/calculator", args={"a": 1, "b": 1},
        session_id=session_id, version="v1", idempotency_key="after-close",
    )
    assert rejected["error"] is True
    invalid_after_close = _json_call(
        module, "capability_invoke", "test/calculator", session_id=session_id,
        version="v1", idempotency_key="after-close",
    )
    assert invalid_after_close["error"] is True


def test_bridge_rejects_missing_invoke_identity_and_harness_mismatch(bridge):
    module, _, requested, _, _ = bridge
    before = len(requested)
    missing_key = _json_call(
        module, "capability_invoke", "test/calculator", session_id="s1",
    )
    missing_session = _json_call(
        module, "capability_invoke", "test/calculator", idempotency_key="k1",
    )
    mismatch = _json_call(
        module, "capability_session_open", harness_id="claude-code",
    )
    assert missing_key["status"] == "INVALID_ARGUMENT"
    assert missing_session["status"] == "INVALID_ARGUMENT"
    assert mismatch["status"] == "INVALID_ARGUMENT"
    assert len(requested) == before

    unknown = _json_call(module, "capability_get", "not/found")
    assert unknown["error"] is True
    assert unknown["status"] == 400


def test_session_open_is_always_in_baseline_surface():
    assert "capability_session_open" in CAPABILITY_BASELINE_TOOLS
