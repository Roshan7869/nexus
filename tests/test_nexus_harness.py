import json
import asyncio

import pytest

from clients.python.nexus_harness import NexusHarness


class Result:
    isError = False
    structuredContent = None

    def __init__(self, payload):
        self.structuredContent = {"result": json.dumps(payload)}
        self.content = []


class Session:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def initialize(self):
        return type("Init", (), {"tools": []})()

    async def list_tools(self):
        names = {
            "project_attach", "project_resume", "project_note", "task_start", "task_complete",
            "capability_session_open", "capability_session_close", "capability_get", "skill_read"}
        return type("Tools", (), {"tools": [type("Tool", (), {"name": n})() for n in names]})()

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.fail == name:
            raise RuntimeError(name)
        values = {
            "task_start": {"session_id": "task-1"},
            "capability_session_open": {"session": {"session_id": "cap-1"}},
            "capability_get": {"descriptor": {"version": "3.9"}},
            "skill_read": {"skill": {"content": "skill text"}},
        }
        return Result(values.get(name, {"ok": True}))


@pytest.mark.asyncio
async def test_lifecycle_pins_version_and_keeps_distinct_sessions():
    session = Session()
    async with NexusHarness("demo", "/repo", "inspect", session=session) as harness:
        assert (await harness.read_skill("nexus-router"))["content"] == "skill text"
        harness.record("correct", "Checked the requested operation.", {"checked": True})
    calls = session.calls
    assert [name for name, _ in calls[:5]] == ["project_attach", "project_resume", "task_start",
                                               "capability_session_open", "capability_get"]
    skill_args = next(args for name, args in calls if name == "skill_read")
    assert skill_args["version"] == "3.9"
    assert next(args for name, args in calls if name == "capability_session_close")["session_id"] == "cap-1"
    assert next(args for name, args in calls if name == "task_complete")["session_id"] == "task-1"


@pytest.mark.asyncio
async def test_unverified_work_defaults_to_partial():
    session = Session()
    async with NexusHarness("demo", "/repo", "inspect", session=session):
        pass
    close = next(args for name, args in session.calls if name == "capability_session_close")
    assert close["outcome"] == "partial"


@pytest.mark.asyncio
async def test_callback_exception_still_attempts_both_closures():
    session = Session()
    with pytest.raises(ValueError):
        async with NexusHarness("demo", "/repo", "inspect", session=session) as harness:
            harness.record("correct", "prematurely verified")
            raise ValueError("callback")
    names = [name for name, _ in session.calls]
    assert "capability_session_close" in names and "task_complete" in names
    close = next(args for name, args in session.calls if name == "capability_session_close")
    assert close["outcome"] == "partial"


@pytest.mark.asyncio
async def test_partial_open_failure_completes_started_task():
    session = Session(fail="capability_session_open")
    with pytest.raises(RuntimeError):
        async with NexusHarness("demo", "/repo", "inspect", session=session):
            pass
    names = [name for name, _ in session.calls]
    assert "task_complete" in names
    assert "capability_session_close" not in names


@pytest.mark.asyncio
async def test_close_failure_does_not_skip_task_completion():
    session = Session(fail="capability_session_close")
    with pytest.raises(RuntimeError, match="cleanup failed"):
        async with NexusHarness("demo", "/repo", "inspect", session=session):
            pass
    assert any(name == "task_complete" for name, _ in session.calls)


@pytest.mark.asyncio
async def test_cleanup_attempts_both_closures_when_both_fail():
    session = Session()
    original = session.call_tool

    async def broken(name, args):
        session.calls.append((name, args))
        if name in {"capability_session_close", "task_complete"}:
            raise RuntimeError(name)
        return await original(name, args)

    session.call_tool = broken
    with pytest.raises(RuntimeError, match="cleanup failed"):
        async with NexusHarness("demo", "/repo", "inspect", session=session):
            pass
    attempted = [name for name, _ in session.calls]
    assert "capability_session_close" in attempted and "task_complete" in attempted


@pytest.mark.asyncio
async def test_cancel_during_capability_open_closes_started_task():
    session = Session()
    original = session.call_tool

    async def cancelled(name, args):
        if name == "capability_session_open":
            raise asyncio.CancelledError()
        return await original(name, args)

    session.call_tool = cancelled
    with pytest.raises(asyncio.CancelledError):
        async with NexusHarness("demo", "/repo", "inspect", session=session):
            pass
    assert any(name == "task_complete" for name, _ in session.calls)
