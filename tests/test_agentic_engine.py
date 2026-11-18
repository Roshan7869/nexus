#!/usr/bin/env python3
"""Agentic harness engine: bounded loop, fail-closed, no fabricated outcomes.

Stdlib only — runs with `python -m unittest tests.test_agentic_engine` and does
not need SQLite, fastapi, an LLM, or the repo's `conftest.py` DB fixtures.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.local_harness.engine import (  # noqa: E402
    BLOCKED,
    BUDGET_EXHAUSTED,
    COMPLETED,
    FAILED,
    REPEAT_GUARD,
    UNVERIFIED,
    UNVERIFIED_OUTCOME,
    AgenticHarness,
    Decision,
    EngineResult,
    LocalHarnessEngine,
    Observation,
    ToolCall,
    session_id_of,
)


class FakeSession:
    """Minimal stand-in for LocalHarnessSession (no broker, no DB)."""

    def __init__(self, sid: str = "sess-1", sites: dict | None = None) -> None:
        self.inner = SimpleNamespace(session_id=sid)
        self.site_status = sites or {}
        self.dispose_calls = 0

    def dispose(self) -> None:
        self.dispose_calls += 1


def engine(**kw) -> LocalHarnessEngine:
    """Engine with an injected fake session and reporting disabled (no DB)."""
    kw.setdefault("session_factory", lambda task, project, ide, tt: FakeSession())
    kw.setdefault("report", False)
    return LocalHarnessEngine(**kw)


class TestFailClosed(unittest.TestCase):
    def test_no_policy_blocks_and_never_opens_session(self):
        opened = []

        def factory(*a):
            opened.append(a)
            return FakeSession()

        e = LocalHarnessEngine(session_factory=factory, report=False)
        res = e.run("do something")
        self.assertEqual(res.status, BLOCKED)
        self.assertEqual(res.error, "no_policy_configured")
        self.assertEqual(opened, [])
        self.assertFalse(res.verified)

    def test_session_open_failure_blocks(self):
        def boom(*a):
            raise RuntimeError("db unavailable")

        e = LocalHarnessEngine(session_factory=boom, decide=lambda c: Decision("finish"),
                              report=False)
        res = e.run("task")
        self.assertEqual(res.status, BLOCKED)
        self.assertIn("session_open_failed", res.error or "")
        self.assertIn("db unavailable", res.error or "")

    def test_policy_abort_is_failure_not_success(self):
        res = engine(decide=lambda c: Decision("abort", reason="cannot proceed")).run("t")
        self.assertEqual(res.status, FAILED)
        self.assertIn("policy_abort", res.error or "")
        self.assertFalse(res.verified)

    def test_invalid_decision_kind_fails(self):
        res = engine(decide=lambda c: Decision("teleport")).run("t")
        self.assertEqual(res.status, FAILED)
        self.assertIn("invalid_decision", res.error or "")

    def test_policy_returning_none_fails(self):
        res = engine(decide=lambda c: None).run("t")
        self.assertEqual(res.status, FAILED)
        self.assertEqual(res.error, "policy_returned_none")


class TestNoFabricatedOutcome(unittest.TestCase):
    def test_finish_without_verifier_is_unverified(self):
        res = engine(decide=lambda c: Decision("finish", summary="all done!")).run("t")
        self.assertEqual(res.status, UNVERIFIED)
        self.assertEqual(res.outcome, UNVERIFIED_OUTCOME)
        self.assertIsNone(res.quality)
        self.assertFalse(res.verified)
        # the policy's own claim is kept as a summary, never promoted to a verdict
        self.assertEqual(res.summary, "all done!")

    def test_finish_with_verifier_completes(self):
        e = engine(
            decide=lambda c: Decision("finish", summary="cited 3 papers"),
            verifier=lambda r: ("correct", 0.9),
        )
        res = e.run("t")
        self.assertEqual(res.status, COMPLETED)
        self.assertEqual(res.outcome, "correct")
        self.assertEqual(res.quality, 0.9)
        self.assertTrue(res.verified)

    def test_verifier_can_refuse_completion(self):
        e = engine(
            decide=lambda c: Decision("finish", summary="done"),
            verifier=lambda r: ("wrong", 0.0),
        )
        res = e.run("t")
        self.assertEqual(res.status, COMPLETED)
        self.assertEqual(res.outcome, "wrong")
        self.assertTrue(res.verified)  # verified: we have a real verdict, even a bad one

    def test_verifier_returning_none_stays_unverified(self):
        e = engine(decide=lambda c: Decision("finish"), verifier=lambda r: None)
        res = e.run("t")
        self.assertEqual(res.status, UNVERIFIED)
        self.assertEqual(res.outcome, UNVERIFIED_OUTCOME)

    def test_verifier_raising_stays_unverified(self):
        def bad_verifier(r):
            raise ValueError("scorer down")

        e = engine(decide=lambda c: Decision("finish"), verifier=bad_verifier)
        res = e.run("t")
        self.assertEqual(res.status, UNVERIFIED)
        self.assertEqual(res.outcome, UNVERIFIED_OUTCOME)


class TestBoundedLoop(unittest.TestCase):
    def test_budget_exhausted_when_policy_keeps_calling(self):
        seq = {"n": 0}

        def decide(ctx):
            seq["n"] += 1
            return Decision("tool", call=ToolCall("search", {"q": seq["n"]}))

        res = engine(decide=decide, execute=lambda c: Observation(ok=True, content="x"),
                     max_steps=4).run("t")
        self.assertEqual(res.status, BUDGET_EXHAUSTED)
        self.assertEqual(len(res.steps), 4)
        self.assertIn("max_steps_reached", res.error or "")

    def test_repeat_guard_stops_identical_calls(self):
        call = ToolCall("search", {"q": "same"})
        res = engine(decide=lambda c: Decision("tool", call=call),
                     execute=lambda c: Observation(ok=True),
                     max_steps=10).run("t")
        self.assertEqual(res.status, BUDGET_EXHAUSTED)
        self.assertIn("repeat_guard", res.error or "")
        self.assertEqual(len(res.steps), REPEAT_GUARD)

    def test_loop_finishes_before_budget(self):
        calls = {"n": 0}

        def decide(ctx):
            calls["n"] += 1
            if calls["n"] <= 2:
                return Decision("tool", call=ToolCall("search", {"q": str(calls["n"])}))
            return Decision("finish", summary="ok")

        res = engine(decide=decide, execute=lambda c: Observation(ok=True),
                     max_steps=8).run("t")
        self.assertEqual(res.status, UNVERIFIED)  # finished with no verifier
        self.assertEqual(len(res.steps), 2)


class TestToolExecution(unittest.TestCase):
    def test_unknown_tool_is_recorded_not_fatal(self):
        res = engine(decide=lambda c: Decision("tool", call=ToolCall("nope"))).run("t")
        self.assertEqual(res.status, BUDGET_EXHAUSTED)  # loop kept going, then ran out
        self.assertEqual(res.steps[0].observation.ok, False)
        self.assertIn("unknown_tool", res.steps[0].observation.error or "")

    def test_tool_from_registry_runs(self):
        seen = []

        def search(args):
            seen.append(args)
            return Observation(ok=True, content="hit")

        res = engine(
            decide=lambda c: Decision("finish") if c.steps else
            Decision("tool", call=ToolCall("search", {"q": "rag"})),
            tools={"search": search},
            verifier=lambda r: ("correct", 1.0),
        ).run("t")
        self.assertEqual(seen, [{"q": "rag"}])
        self.assertEqual(res.status, COMPLETED)

    def test_tool_exception_becomes_failed_observation(self):
        def boom(args):
            raise RuntimeError("tool crashed")

        res = engine(
            decide=lambda c: Decision("finish") if c.steps else
            Decision("tool", call=ToolCall("boom")),
            tools={"boom": boom},
        ).run("t")
        self.assertEqual(res.steps[0].observation.ok, False)
        self.assertIn("tool crashed", res.steps[0].observation.error or "")

    def test_artifacts_are_collected(self):
        art = {"title": "A paper", "url": "https://example.org/p"}

        res = engine(
            decide=lambda c: Decision("finish") if c.steps else
            Decision("tool", call=ToolCall("research")),
            execute=lambda c: Observation(ok=True, artifacts=(art,)),
        ).run("t")
        self.assertEqual(res.artifacts, [art])


class TestTeardown(unittest.TestCase):
    def test_dispose_runs_on_success(self):
        fake = FakeSession()
        e = LocalHarnessEngine(session_factory=lambda *a: fake,
                              decide=lambda c: Decision("finish"), report=False)
        e.run("t")
        self.assertEqual(fake.dispose_calls, 1)

    def test_dispose_runs_when_policy_raises(self):
        fake = FakeSession()

        def decide(ctx):
            raise RuntimeError("policy exploded")

        e = LocalHarnessEngine(session_factory=lambda *a: fake, decide=decide, report=False)
        res = e.run("t")
        self.assertEqual(res.status, FAILED)
        self.assertIn("policy exploded", res.error or "")
        self.assertEqual(fake.dispose_calls, 1)

    def test_dispose_runs_when_executor_path_raises(self):
        fake = FakeSession()

        def factory(*a):
            return fake

        e = LocalHarnessEngine(
            session_factory=factory,
            decide=lambda c: Decision("finish") if c.steps else
            Decision("tool", call=ToolCall("t")),
            execute=lambda c: (_ for _ in ()).throw(RuntimeError("executor died")),
            report=False,
        )
        res = e.run("t")
        # _run_tool does not wrap an injected executor; the loop catches it
        self.assertEqual(res.status, FAILED)
        self.assertEqual(fake.dispose_calls, 1)

    def test_dispose_runs_when_report_fails(self):
        fake = FakeSession()

        e = LocalHarnessEngine(
            session_factory=lambda *a: fake,
            decide=lambda c: Decision("finish", summary="x"),
            report=True,  # will import dispose_by_id and fail without a DB
        )
        res = e.run("t")
        self.assertEqual(fake.dispose_calls, 1)  # teardown survived reporting failure
        self.assertIn(res.status, (UNVERIFIED, FAILED, BLOCKED))

    def test_sites_released_surfaced_from_session(self):
        fake = FakeSession(sites={"https://a.example": "bound",
                                  "https://b.example": "timeout"})
        e = LocalHarnessEngine(session_factory=lambda *a: fake,
                              decide=lambda c: Decision("finish"), report=False)
        res = e.run("t")
        self.assertEqual(res.sites_released, ["https://a.example"])


class TestPlanningAndSessionId(unittest.TestCase):
    def test_plan_detects_research_intent(self):
        e = engine(decide=lambda c: Decision("finish"))
        plan = e.plan("literature review of arxiv papers on routing")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.intent, "research")
        self.assertIn("research", plan.toolsets)

    def test_plan_detects_scholarly_phrasings(self):
        e = engine(decide=lambda c: Decision("finish"))
        for task in (
            "retrieval augmented generation",
            "benchmark for dataset evaluation",
            "find the preprint and proceedings",
        ):
            with self.subTest(task=task):
                self.assertEqual(e.plan(task).intent, "research")

    def test_plain_coding_task_stays_general(self):
        e = engine(decide=lambda c: Decision("finish"))
        self.assertEqual(e.plan("fix the login button CSS").intent, "general")

    def test_plan_survives_planner_failure(self):
        e = engine(decide=lambda c: Decision("finish"))
        e.plan = lambda task: None  # simulate unavailable planner
        res = e.run("t")
        self.assertIsNone(res.plan)
        self.assertEqual(res.status, UNVERIFIED)

    def test_session_id_from_inner(self):
        self.assertEqual(session_id_of(FakeSession("abc")), "abc")
        self.assertEqual(session_id_of(SimpleNamespace(session_id="direct")), "direct")
        self.assertIsNone(session_id_of(None))

    def test_turn_context_exposes_tools_and_history(self):
        seen = {}

        def decide(ctx):
            seen["tools"] = ctx.tools
            seen["step"] = ctx.step
            return Decision("finish")

        engine(decide=decide, tools={"alpha": lambda a: Observation(ok=True)}).run("t")
        self.assertEqual(seen["tools"], ("alpha",))
        self.assertEqual(seen["step"], 0)


class TestHarnessAdapter(unittest.TestCase):
    def test_agentic_harness_run_turn_executes_loop(self):
        h = AgenticHarness(engine(decide=lambda c: Decision("finish", summary="done")))
        self.assertEqual(h.id(), "local")
        self.assertTrue(h.supports_chat())
        self.assertIsNotNone(h.detect())

        import asyncio
        parts = asyncio.run(h.run_turn(object(), "hello"))
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].kind, "text")
        self.assertEqual(parts[0].text, "done")

    def test_wire_parts_include_tool_then_text(self):
        e = engine(
            decide=lambda c: Decision("finish", summary="s") if c.steps else
            Decision("tool", call=ToolCall("search", {"q": "x"})),
            execute=lambda c: Observation(ok=True),
        )
        res = e.run("t")
        parts = e.wire_parts(res)
        self.assertEqual([p.kind for p in parts], ["tool", "text"])
        self.assertEqual(parts[0].tool, "search")

    def test_result_is_json_safe(self):
        import json
        res = engine(decide=lambda c: Decision("finish", summary="x")).run("t")
        json.dumps(res.to_dict())  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
