"""Isolated SQLite and injected-provider tests for lifecycle and native claims."""
import copy
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.capability_adapters import CapabilityAdapters
from server.capability_catalog import CapabilityCatalog, connect_db, initialize_schema
from server.capability_runtime import CapabilityError, CapabilityRuntime, arguments_hash


PRINCIPAL = {"id": "alice", "harness": "codex", "projects": ["demo"], "verifier": False}
OTHER = {"id": "alice", "harness": "hermes", "projects": ["demo"], "verifier": False}


@pytest.fixture
def setup_runtime(tmp_path):
    path = tmp_path / "capability.db"
    initialize_schema(path)
    descriptor = {"canonical_id": "test/echo", "kind": "tool", "version": "v1",
                  "provider_id": "fake", "method": "echo", "availability": "available",
                  "input_schema": {"type": "object", "properties": {"value": {"type": "integer"}},
                                   "required": ["value"], "additionalProperties": False},
                  "execution_policy": {"allow": True, "methods": ["echo"]}}
    native = {**descriptor, "canonical_id": "test/native", "kind": "native_action",
              "adapter_id": "codex", "method": "edit", "execution_policy": {"allow": True, "methods": ["edit"]}}
    native.pop("provider_id")
    with connect_db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        for desc in (descriptor, native):
            db.execute("INSERT INTO capability_catalog VALUES (?,?,?,?,?,?)",
                       (desc["canonical_id"], desc["kind"], "test", desc["canonical_id"], "v1", json.dumps(desc)))
            db.execute("INSERT INTO capability_versions VALUES (?,?,?,?,?)",
                       (desc["canonical_id"], "v1", arguments_hash(desc), json.dumps(desc), time.time()))
        for sid, owner in (("s1", PRINCIPAL), ("s2", OTHER), ("s3", PRINCIPAL)):
            db.execute("INSERT INTO capability_sessions VALUES (?,?,?,?,?,?,?)",
                       (sid, owner["id"], owner["harness"], "demo", json.dumps({"project": "demo", "niche": "test", "niche_version": "1"}),
                        "active", time.time() + 3600))
    runtime = CapabilityRuntime(path, CapabilityCatalog(path))
    yield path, runtime
    runtime.drain()


def request(**overrides):
    return {"capability_id": "test/echo", "version": "v1", "session_id": "s1",
            "arguments": {"value": 1}, "idempotency_key": "once", **overrides}


def rows(path, sql, params=()):
    with connect_db(path) as db:
        return [dict(row) for row in db.execute(sql, params).fetchall()]


def assert_no_leases(path):
    assert all(row["lease_count"] == 0 for row in rows(path, "SELECT * FROM provider_generations"))


def test_execution_is_durable_and_idempotent(setup_runtime):
    path, runtime = setup_runtime
    # The fake's side effect is durable in an isolated database, separate from its receipt.
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE fake_effects (value INTEGER)")
    def handler(args, context):
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO fake_effects VALUES (?)", (args["value"],))
        return args
    runtime.register_provider("fake", handler)
    first = runtime.invoke(request(), PRINCIPAL)
    assert first["state"] == "succeeded" and first["result"] == {"value": 1}
    assert runtime.invoke(request(), PRINCIPAL) == first
    assert len(rows(path, "SELECT * FROM fake_effects")) == 1
    assert len(rows(path, "SELECT * FROM capability_events")) == 2
    assert first["event_id"] in {r["event_id"] for r in rows(path, "SELECT * FROM capability_events")}
    assert_no_leases(path)
    with pytest.raises(CapabilityError, match="different bindings"):
        runtime.invoke(request(arguments={"value": 2}), PRINCIPAL)


def test_unknown_write_never_replays(setup_runtime):
    path, runtime = setup_runtime
    calls = []
    def handler(args, context):
        calls.append(args)
        raise ConnectionResetError("secret provider endpoint")
    runtime.register_provider("fake", handler)
    first = runtime.invoke(request(), PRINCIPAL)
    assert first["state"] == "unknown"
    assert "secret" not in json.dumps(first)
    assert runtime.invoke(request(), PRINCIPAL) == first and len(calls) == 1
    assert_no_leases(path)


def test_concurrent_retry_does_not_execute_twice(setup_runtime):
    path, runtime = setup_runtime
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def handler(args, context):
        calls.append(args)
        entered.set()
        assert finish.wait(3)
        return "done"
    runtime.register_provider("fake", handler)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runtime.invoke, request(), PRINCIPAL)
        try:
            assert entered.wait(3)
            retry = runtime.invoke(request(), PRINCIPAL)
            assert retry["state"] == "running"
            assert rows(path, "SELECT lease_count FROM provider_generations")[0]["lease_count"] == 1
        finally:
            finish.set()
        assert future.result()["invocation_id"] == retry["invocation_id"]
    assert len(calls) == 1
    assert_no_leases(path)


def test_upgrade_pins_two_sessions_and_disposes_once(setup_runtime):
    path, runtime = setup_runtime
    calls, disposed = [], []
    runtime.register_provider("fake", lambda args, ctx: calls.append("old"), {"dispose": lambda: disposed.append("old")})
    leases = [runtime.acquire("test/echo", "v1", sid) for sid in ("s1", "s2")]
    replacement = runtime.register_provider("fake", lambda args, ctx: "new")
    assert replacement["generation"] == 2
    assert not disposed
    result = runtime.invoke(request(session_id="s2"), OTHER)
    assert result["provider_generation"] == 2 and result["result"] == "new"
    runtime.release(leases[0])
    assert not disposed
    runtime.release(leases[1])
    runtime.release(leases[1])
    assert disposed == ["old"] and not calls
    assert rows(path, "SELECT state FROM provider_generations WHERE generation=1")[0]["state"] == "inactive"
    assert_no_leases(path)


def test_dependency_loss_and_cycle_are_rejected(setup_runtime):
    path, runtime = setup_runtime
    disposed = []
    runtime.register_provider("dep", lambda args, ctx: None, {"dispose": lambda: disposed.append("dep")})
    runtime.register_provider("fake", lambda args, ctx: "ok", {"dependencies": ["dep"]})
    lease = runtime.acquire("test/echo", "v1", "s1")
    assert len(lease.pins) == 2
    with pytest.raises(CapabilityError, match="cycle"):
        runtime.register_provider("dep", lambda args, ctx: None, {"dependencies": ["fake"]})
    runtime.drain("dep")
    assert not disposed
    with pytest.raises(CapabilityError):
        runtime.invoke(request(), PRINCIPAL)
    runtime.release(lease)
    assert disposed == ["dep"]
    assert_no_leases(path)
    assert not rows(path, "SELECT * FROM capability_invocations")


def test_partial_initialization_rolls_back_in_reverse_order(setup_runtime):
    path, runtime = setup_runtime
    disposed = []
    class Broken:
        def initialize(self, context):
            context.effect(lambda: lambda: disposed.append("first"))
            context.effect(lambda: lambda: disposed.append("second"))
            raise RuntimeError("init failed")
        def close(self):
            disposed.append("close")
        def __call__(self, args, context):
            pytest.fail("failed provider executed")
    runtime.register_provider("fake", lambda args, ctx: "old")
    with pytest.raises(RuntimeError):
        runtime.register_provider("fake", Broken())
    assert disposed == ["second", "first", "close"]
    assert runtime.invoke(request(), PRINCIPAL)["result"] == "old"
    assert rows(path, "SELECT state FROM provider_generations WHERE generation=2")[0]["state"] == "failed"
    assert_no_leases(path)


def test_shutdown_during_timeout_retains_handles_until_exit(setup_runtime):
    path, runtime = setup_runtime
    finish, cleaned = threading.Event(), threading.Event()
    disposed, calls = [], []
    def handler(args, context):
        calls.append(1)
        context.effect(lambda: lambda: cleaned.set())
        assert finish.wait(3)
        return "late result"
    runtime.register_provider("fake", handler, {"timeout_seconds": .03, "max_concurrency": 1,
                                               "dispose": lambda: disposed.append(1)})
    try:
        first = runtime.invoke(request(), PRINCIPAL)
        assert first["state"] == "unknown" and first["error"]["code"] == "PROVIDER_TIMEOUT"
        assert runtime.invoke(request(), PRINCIPAL) == first
        runtime.drain()
        assert not cleaned.is_set() and not disposed
        assert rows(path, "SELECT lease_count FROM provider_generations")[0]["lease_count"] == 1
    finally:
        finish.set()
    assert cleaned.wait(3)
    # release marks invocation cleanup before the generation disposer; synchronize on runtime lock.
    with runtime._lock:
        assert disposed == [1]
    assert_no_leases(path)
    assert rows(path, "SELECT state FROM capability_invocations")[0]["state"] == "unknown"
    assert calls == [1]


@pytest.mark.parametrize("change", [
    {"arguments": {"value": "wrong"}}, {"arguments": {"value": 1, "extra": True}},
    {"session_id": "s2"}, {"version": ""}, {"arguments": {"value": float("nan")}},
])
def test_invalid_or_unauthorized_request_cannot_execute(setup_runtime, change):
    path, runtime = setup_runtime
    calls = []
    runtime.register_provider("fake", lambda args, ctx: calls.append(1))
    with pytest.raises(CapabilityError):
        runtime.invoke(request(**change), PRINCIPAL)
    assert calls == []
    assert not rows(path, "SELECT * FROM capability_invocations")
    assert_no_leases(path)


def test_unregistered_provider_and_bulkhead(setup_runtime):
    path, runtime = setup_runtime
    with pytest.raises(CapabilityError):
        runtime.invoke(request(), PRINCIPAL)
    finish, entered = threading.Event(), threading.Event()
    def handler(args, context):
        entered.set()
        assert finish.wait(3)
    runtime.register_provider("fake", handler, {"max_concurrency": 1})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runtime.invoke, request(), PRINCIPAL)
        try:
            assert entered.wait(3)
            with pytest.raises(CapabilityError, match="concurrency"):
                runtime.invoke(request(idempotency_key="second"), PRINCIPAL)
            assert rows(path, "SELECT lease_count FROM provider_generations")[0]["lease_count"] == 1
        finally:
            finish.set()
        assert future.result()["state"] == "succeeded"
    assert_no_leases(path)


def test_pinned_version_cannot_bypass_current_policy_or_removal(setup_runtime):
    path, runtime = setup_runtime
    calls = []
    runtime.register_provider("fake", lambda args, ctx: calls.append(1))
    with connect_db(path) as db:
        descriptor = json.loads(db.execute("SELECT metadata_json FROM capability_catalog WHERE capability_id='test/echo'").fetchone()[0])
        descriptor["execution_policy"]["allow"] = False
        db.execute("UPDATE capability_catalog SET metadata_json=? WHERE capability_id='test/echo'", (json.dumps(descriptor),))
    with pytest.raises(CapabilityError) as denied:
        runtime.invoke(request(), PRINCIPAL)
    assert denied.value.code == "FORBIDDEN"
    with connect_db(path) as db:
        descriptor["execution_policy"]["allow"] = True
        descriptor.update(availability="unavailable", reason="SOURCE_REMOVED")
        db.execute("UPDATE capability_catalog SET metadata_json=? WHERE capability_id='test/echo'", (json.dumps(descriptor),))
    with pytest.raises(CapabilityError) as unavailable:
        runtime.invoke(request(), PRINCIPAL)
    assert unavailable.value.code == "UNAVAILABLE" and not calls
    assert_no_leases(path)


def test_provider_failure_and_version_conflict(setup_runtime):
    path, runtime = setup_runtime
    calls = []
    def broken(args, ctx):
        calls.append(ctx.state["method"])
        raise RuntimeError("private credentials")
    runtime.register_provider("fake", broken)
    result = runtime.invoke(request(), PRINCIPAL)
    assert result["state"] == "failed" and result["error"]["code"] == "PROVIDER_FAILED"
    assert "private" not in json.dumps(result) and calls == ["echo"]
    with connect_db(path) as db:
        descriptor = json.loads(db.execute("SELECT metadata_json FROM capability_catalog WHERE capability_id='test/echo'").fetchone()[0])
        descriptor["version"] = "v2"
        db.execute("INSERT INTO capability_versions VALUES (?,?,?,?,?)", ("test/echo", "v2", "hash2", json.dumps(descriptor), time.time()))
    with pytest.raises(CapabilityError) as conflict:
        runtime.invoke(request(version="v2"), PRINCIPAL)
    assert conflict.value.code == "CONFLICT" and calls == ["echo"]
    assert_no_leases(path)


def test_recovery_marks_orphans_unknown_without_execution(setup_runtime):
    path, runtime = setup_runtime
    with connect_db(path) as db:
        db.execute("INSERT INTO capability_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   ("orphan", "alice", "s1", "test/echo", "v1", 1, "once", arguments_hash({"value": 1}), "running", None, None))
        db.execute("INSERT INTO provider_generations VALUES ('fake',1,'active','{}',1)")
    fresh = CapabilityRuntime(path, CapabilityCatalog(path))
    assert fresh.recover_inflight()["recovered"] == 1
    assert fresh.recover_inflight()["recovered"] == 0
    calls = []
    fresh.register_provider("fake", lambda args, ctx: calls.append(1))
    result = fresh.invoke(request(), PRINCIPAL)
    assert result["state"] == "unknown" and result["invocation_id"] == "orphan" and not calls
    fresh.drain()
    assert_no_leases(path)


def make_envelope(path):
    adapters = CapabilityAdapters(path, b"test-secret")
    adapters.register_harness(PRINCIPAL, {"native_verbs": ["edit"]})
    envelope = adapters.prepare_native_action(request(capability_id="test/native", principal=PRINCIPAL))
    return adapters, envelope


def test_native_claim_survives_restart_and_never_rewards(setup_runtime):
    path, _ = setup_runtime
    adapters, envelope = make_envelope(path)
    assert adapters.prepare_native_action(request(capability_id="test/native", principal=PRINCIPAL)) == envelope
    restarted = CapabilityAdapters(path, b"test-secret")
    claim = {"envelope": envelope, "state": "succeeded", "result": {"edited": True}}
    result = restarted.accept_native_receipt(envelope["envelope_id"], claim, PRINCIPAL)
    assert result["state"] == "succeeded" and result["verified"] is False
    assert restarted.accept_native_receipt(envelope["envelope_id"], claim, PRINCIPAL) == result
    assert not rows(path, "SELECT * FROM capability_rewards")
    assert len(rows(path, "SELECT * FROM capability_events WHERE type='native_execution_claim'")) == 1
    with pytest.raises(CapabilityError):
        restarted.accept_native_receipt(envelope["envelope_id"], claim, {**PRINCIPAL, "projects": []})


@pytest.mark.parametrize("mutation", ["signature", "session", "arguments", "principal"])
def test_native_receipt_rejects_tampering_and_cross_harness(setup_runtime, mutation):
    path, _ = setup_runtime
    adapters, original = make_envelope(path)
    envelope = copy.deepcopy(original)
    principal = PRINCIPAL
    if mutation == "signature":
        envelope["signature"] = "bad"
    elif mutation == "session":
        envelope["session_id"] = "s2"
    elif mutation == "arguments":
        envelope["arguments"]["value"] = 99
    else:
        principal = OTHER
    with pytest.raises(CapabilityError):
        adapters.accept_native_receipt(original["envelope_id"], {"envelope": envelope, "state": "succeeded"}, principal)
    assert rows(path, "SELECT state FROM capability_invocations")[0]["state"] == "native_pending"
    assert not rows(path, "SELECT * FROM capability_rewards")


def test_native_expiry_and_unsupported_verb(setup_runtime):
    path, _ = setup_runtime
    adapters = CapabilityAdapters(path, b"test-secret")
    adapters.register_harness(PRINCIPAL, {})
    with pytest.raises(CapabilityError, match="negotiated"):
        adapters.prepare_native_action(request(capability_id="test/native", principal=PRINCIPAL))
    assert not rows(path, "SELECT * FROM capability_invocations")
    adapters, envelope = make_envelope(path)
    with connect_db(path) as db:
        db.execute("UPDATE capability_sessions SET expires_at=0 WHERE session_id='s1'")
    with pytest.raises(CapabilityError) as error:
        adapters.accept_native_receipt(envelope["envelope_id"], {"envelope": envelope, "state": "succeeded"}, PRINCIPAL)
    assert error.value.code == "EXPIRED"


@pytest.mark.parametrize("harness", ["codex", "claude-code", "opencode", "hermes"])
def test_harness_features_have_no_assumed_native_verbs(setup_runtime, harness):
    path, _ = setup_runtime
    adapters = CapabilityAdapters(path, b"test-secret")
    profile = adapters.register_harness({**PRINCIPAL, "harness": harness}, {})
    assert profile["permitted_native_verbs"] == []
    assert profile["dynamic_catalog"] is False and profile["skill_reads"] is False
