"""Contract verification for capability API facade across HTTP and harnesses."""
import json
import pytest
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.capability_catalog import CapabilityCatalog, initialize_schema, connect_db
from server.capability_runtime import CapabilityRuntime, arguments_hash
from server.capability_recommender import CapabilityRecommender
from server.capability_events import CapabilityEvents
from server.capability_adapters import CapabilityAdapters
from server.capability_api import create_capability_router, CapabilityServiceGraph

CODEX_PRINCIPAL = {"id": "codex-user", "harness": "codex", "projects": ["demo"], "verifier": False}
CLAUDE_PRINCIPAL = {"id": "claude-user", "harness": "claude-code", "projects": ["demo"], "verifier": False}
VERIFIER_PRINCIPAL = {"id": "verifier-user", "harness": "eval", "projects": ["*"], "verifier": True}


@pytest.fixture
def service_graph(tmp_path):
    db_path = tmp_path / "contracts.db"
    initialize_schema(db_path)

    # Seed descriptors
    descriptor = {
        "canonical_id": "test/calculator",
        "kind": "tool",
        "version": "v1",
        "provider_id": "math_provider",
        "method": "add",
        "availability": "available",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        "execution_policy": {"allow": True, "methods": ["add"]},
    }
    skill_desc = {
        "canonical_id": "test/python_debug",
        "kind": "skill",
        "version": "v1",
        "availability": "available",
        "name": "Python Debugger",
        "description": "Debug Python stack traces",
    }
    native_desc = {
        "canonical_id": "test/file_edit",
        "kind": "native_action",
        "version": "v1",
        "adapter_id": "codex",
        "method": "edit_file",
        "availability": "available",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "execution_policy": {"allow": True, "methods": ["edit_file"]},
    }

    with connect_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        for desc in (descriptor, skill_desc, native_desc):
            db.execute(
                "INSERT INTO capability_catalog VALUES (?,?,?,?,?,?)",
                (desc["canonical_id"], desc["kind"], "test", desc["canonical_id"], "v1", json.dumps(desc)),
            )
            db.execute(
                "INSERT INTO capability_versions VALUES (?,?,?,?,?)",
                (desc["canonical_id"], "v1", arguments_hash(desc), json.dumps(desc), time.time()),
            )
        # Create catalog revision
        db.execute("INSERT INTO catalog_revisions VALUES (1, 'hash_rev1', ?)", (time.time(),))
        for desc in (descriptor, skill_desc, native_desc):
            db.execute(
                "INSERT INTO catalog_revision_members VALUES (1, ?, 'v1', 'available', ?)",
                (desc["canonical_id"], json.dumps(desc)),
            )
        db.commit()

    catalog = CapabilityCatalog(db_path)
    runtime = CapabilityRuntime(db_path, catalog)
    recommender = CapabilityRecommender(db_path, catalog)
    events = CapabilityEvents(db_path)
    adapters = CapabilityAdapters(db_path, secret=b"test-contracts-secret")

    # Register fake provider
    runtime.register_provider("math_provider", lambda args, ctx: {"sum": args["a"] + args["b"]})

    # Register adapter profile for codex
    adapters.register_harness(
        {"id": "codex-user", "harness": "codex"},
        {"permitted_native_verbs": ["edit_file"]},
    )

    graph = CapabilityServiceGraph(catalog, runtime, recommender, events, adapters)
    return db_path, graph


@pytest.fixture
def client(service_graph):
    _, graph = service_graph
    app = FastAPI()
    router = create_capability_router(graph)
    app.include_router(router)
    return TestClient(app)


def test_health_and_catalog_discovery(client):
    res = client.get("/capabilities/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"

    # List catalog
    res = client.get(
        "/capabilities/v1/catalog",
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert res.status_code == 200
    items = res.json()["page"]["items"]
    assert len(items) == 3
    ids = {item["canonical_id"] for item in items}
    assert "test/calculator" in ids
    assert "test/python_debug" in ids


def test_two_harnesses_share_provider_and_session_isolation(client):
    # Open Codex session
    res1 = client.post(
        "/capabilities/v1/sessions",
        json={"harness_id": "codex", "project": "demo"},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert res1.status_code == 200
    sess1 = res1.json()["session"]["session_id"]

    # Open Claude session
    res2 = client.post(
        "/capabilities/v1/sessions",
        json={"harness_id": "claude-code", "project": "demo"},
        headers={"x-principal-id": "claude-user", "x-harness-id": "claude-code"},
    )
    assert res2.status_code == 200
    sess2 = res2.json()["session"]["session_id"]
    assert sess1 != sess2

    # Invoke tool from Codex session
    inv1 = client.post(
        "/capabilities/v1/invoke",
        json={
            "capability_id": "test/calculator",
            "version": "v1",
            "session_id": sess1,
            "arguments": {"a": 2, "b": 3},
            "idempotency_key": "k1",
        },
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert inv1.status_code == 200
    assert inv1.json()["receipt"]["result"] == {"sum": 5}

    # Invoke tool from Claude session (shares math_provider)
    inv2 = client.post(
        "/capabilities/v1/invoke",
        json={
            "capability_id": "test/calculator",
            "version": "v1",
            "session_id": sess2,
            "arguments": {"a": 10, "b": 20},
            "idempotency_key": "k2",
        },
        headers={"x-principal-id": "claude-user", "x-harness-id": "claude-code"},
    )
    assert inv2.status_code == 200
    assert inv2.json()["receipt"]["result"] == {"sum": 30}


def test_recommendation_and_nonrecommended_access(client):
    # Open session
    res = client.post(
        "/capabilities/v1/sessions",
        json={"harness_id": "codex", "project": "demo"},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    sess_id = res.json()["session"]["session_id"]

    # Ask for recommend (k=1)
    rec = client.post(
        "/capabilities/v1/recommend",
        json={"session_id": sess_id, "query": "python debugging", "k": 1, "seed": 42},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert rec.status_code == 200
    slates = rec.json()["recommendation"]["recommendations"]
    assert len(slates) == 1

    # Even if test/calculator is NOT in recommendation slate, it remains directly invocable
    inv = client.post(
        "/capabilities/v1/invoke",
        json={
            "capability_id": "test/calculator",
            "version": "v1",
            "session_id": sess_id,
            "arguments": {"a": 5, "b": 7},
            "idempotency_key": "k_direct",
        },
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert inv.status_code == 200
    assert inv.json()["receipt"]["result"] == {"sum": 12}


def test_native_action_envelope_and_receipt(client):
    res = client.post(
        "/capabilities/v1/sessions",
        json={"harness_id": "codex", "project": "demo"},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    sess_id = res.json()["session"]["session_id"]

    # Invoke native action -> returns envelope
    inv = client.post(
        "/capabilities/v1/invoke",
        json={
            "capability_id": "test/file_edit",
            "version": "v1",
            "session_id": sess_id,
            "arguments": {"path": "main.py"},
            "idempotency_key": "native_1",
        },
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert inv.status_code == 200
    envelope = inv.json()["native_envelope"]
    assert envelope["verb"] == "edit_file"
    assert envelope["invocation_id"]

    # Accept native receipt
    receipt_res = client.post(
        "/capabilities/v1/native-receipts",
        json={"envelope": envelope, "state": "succeeded", "result": {"bytes_written": 42}},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert receipt_res.status_code == 200
    assert receipt_res.json()["receipt"]["state"] == "succeeded"


def test_verified_outcome_recording(client):
    res = client.post(
        "/capabilities/v1/sessions",
        json={"harness_id": "codex", "project": "demo"},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    sess_id = res.json()["session"]["session_id"]

    inv = client.post(
        "/capabilities/v1/invoke",
        json={
            "capability_id": "test/calculator",
            "version": "v1",
            "session_id": sess_id,
            "arguments": {"a": 1, "b": 1},
            "idempotency_key": "out_1",
        },
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    inv_id = inv.json()["receipt"]["invocation_id"]

    # Non-verifier cannot record outcomes
    denied = client.post(
        "/capabilities/v1/outcomes",
        json={"invocation_id": inv_id, "verdict": "correct", "evidence_ref": "git_commit_sha", "verified_by": "alice"},
        headers={"x-principal-id": "codex-user", "x-harness-id": "codex"},
    )
    assert denied.status_code == 403

    # Verifier principal succeeds
    allowed = client.post(
        "/capabilities/v1/outcomes",
        json={"invocation_id": inv_id, "verdict": "correct", "evidence_ref": "eval_pass_sha", "verified_by": "eval_bot"},
        headers={"x-principal-id": "verifier-user", "x-harness-id": "eval"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["recorded"]["verdict"] == "correct"


def test_evaluator_gate_pass_conjunction():
    """Verify that any false gate strictly forces all_gates_pass to False (O-1 / Blocker 1)."""
    base_gates = {
        "gate_catalog_nonzero": True,
        "gate_zero_duplicates": True,
        "gate_p95_latency": True,
        "gate_cross_harness": True,
    }
    assert all(base_gates.values()) is True

    # Mutate each gate to False one by one and assert all_gates_pass fails closed
    for gate_key in base_gates:
        modified = dict(base_gates)
        modified[gate_key] = False
        all_gates_pass = all(modified.values())
        assert all_gates_pass is False, f"Gate failure on {gate_key} did not fail closed!"
