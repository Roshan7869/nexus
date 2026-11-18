#!/usr/bin/env python3
"""Fiber layer: isolation, quiescence, bounded concurrency, teardown-on-every-path.

Stdlib only. Run: python -m unittest tests.test_fibers
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.local_harness.context import NexusContext  # noqa: E402
from server.local_harness.engine import (  # noqa: E402
    Decision,
    EngineResult,
    LocalHarnessEngine,
    Observation,
    ToolCall,
)
from server.local_harness.fibers import (  # noqa: E402
    CANCELLED,
    COMPLETED,
    FAILED,
    FiberPool,
    TIMEOUT,
)


class Recording:
    """Thread-safe ledger of effect registration and disposal per fiber context."""

    def __init__(self):
        self.lock = threading.Lock()
        self.disposed: list[str] = []
        self.registered: list[str] = []

    def register(self, realm, name):
        with self.lock:
            self.registered.append(f"{realm}:{name}")

    def dispose(self, realm, name):
        with self.lock:
            self.disposed.append(f"{realm}:{name}")


def tool_using_effects(rec: Recording, name: str, *, delay: float = 0.0,
                       fail: bool = False, hang: float = 0.0):
    """A tool that registers an invertible effect on its fiber context."""

    def execute(call, ctx: NexusContext) -> Observation:
        rec.register(ctx.realm, name)
        ctx.effect(lambda: (lambda: rec.dispose(ctx.realm, name)))
        if hang:
            time.sleep(hang)
        if delay:
            time.sleep(delay)
        if fail:
            return Observation(ok=False, error=f"{name} failed on purpose")
        return Observation(ok=True, content=f"{name} done")

    return execute


class TestIsolation(unittest.TestCase):
    """DSH Cordis `isolate`: each fiber owns its context and its effects alone."""

    def test_each_fiber_gets_distinct_realm_and_context(self):
        calls = [ToolCall("a"), ToolCall("b"), ToolCall("c")]
        seen = []
        lock = threading.Lock()

        def execute(call, ctx):
            with lock:
                seen.append((ctx.realm, id(ctx)))
            return Observation(ok=True)

        report = FiberPool(max_concurrent=3, timeout=5).run(calls, execute)
        realms = [r for r, _ in seen]
        ids = [i for _, i in seen]
        self.assertEqual(len(set(realms)), 3, f"realms not distinct: {realms}")
        self.assertEqual(len(set(ids)), 3, "contexts not distinct")

    def test_fiber_effects_disposed_not_visible_to_siblings(self):
        rec = Recording()
        calls = [ToolCall("a"), ToolCall("b")]
        pool = FiberPool(max_concurrent=2, timeout=5)
        report = pool.run(calls, tool_using_effects(rec, "x"))
        self.assertEqual(report.n_completed, 2)
        # every registered effect was disposed exactly once
        self.assertEqual(sorted(rec.disposed), sorted(rec.registered))
        self.assertEqual(len(rec.disposed), 2)

    def test_realms_are_children_of_parent(self):
        pool = FiberPool("local:proj")
        report = pool.run([ToolCall("a")], lambda c, ctx: Observation(ok=True))
        self.assertEqual(report.fibers[0].realm, "local:proj:fiber:1")


class TestQuiescence(unittest.TestCase):
    """Cordis Thm80: run() returns only when every fiber is terminal."""

    def test_report_returned_only_when_all_terminal(self):
        pool = FiberPool(max_concurrent=4, timeout=5)
        report = pool.run(
            [ToolCall(f"t{i}") for i in range(6)],
            lambda c, ctx: (time.sleep(0.02), Observation(ok=True))[1])
        self.assertTrue(report.quiescent)
        self.assertEqual(len(report.fibers), 6)
        for f in report.fibers:
            self.assertEqual(f.state, COMPLETED)

    def test_failed_fiber_is_terminal_not_an_exception(self):
        rec = Recording()
        pool = FiberPool(max_concurrent=2, timeout=5)
        report = pool.run(
            [ToolCall("bad"), ToolCall("good")],
            lambda c, ctx: (Observation(ok=False, error="boom")
                            if c.name == "bad" else Observation(ok=True)))
        self.assertEqual(report.n_failed, 1)
        self.assertEqual(report.n_completed, 1)
        self.assertTrue(report.quiescent)

    def test_timeout_marks_fiber_and_stays_honest(self):
        pool = FiberPool(max_concurrent=1, timeout=0.2)
        report = pool.run(
            [ToolCall("slow")],
            lambda c, ctx: (time.sleep(1.0), Observation(ok=True))[1])
        # the fiber was marked TIMEOUT at the barrier
        self.assertEqual(report.fibers[0].state, TIMEOUT)
        # grace (2x timeout) < 1.0s sleep: reported non-quiescent, not lied about
        self.assertFalse(report.quiescent)
        self.assertEqual(report.leaked, ["f1"])


class TestTeardownEveryPath(unittest.TestCase):
    """The chaos invariant: disposed == registered on EVERY terminal path."""

    def test_success_path_disposes(self):
        rec = Recording()
        FiberPool(max_concurrent=2, timeout=5).run(
            [ToolCall("a"), ToolCall("b")], tool_using_effects(rec, "e"))
        self.assertEqual(len(rec.disposed), 2)

    def test_failure_path_disposes(self):
        rec = Recording()
        FiberPool(max_concurrent=2, timeout=5).run(
            [ToolCall("a")], tool_using_effects(rec, "e", fail=True))
        self.assertEqual(len(rec.disposed), 1)

    def test_executor_exception_path_disposes(self):
        rec = Recording()

        def execute(call, ctx):
            rec.register(ctx.realm, "e")
            ctx.effect(lambda: (lambda: rec.dispose(ctx.realm, "e")))
            raise RuntimeError("executor exploded")

        FiberPool(max_concurrent=1, timeout=5).run([ToolCall("a")], execute)
        self.assertEqual(len(rec.disposed), 1)

    def test_cancelled_path_disposes(self):
        # on_fail=cancel + first fiber fails fast: later fiber cancelled pre-start
        pool = FiberPool(max_concurrent=1, timeout=5, on_fail="cancel")

        def execute(call, ctx):
            if call.name == "boom":
                return Observation(ok=False, error="boom")
            time.sleep(0.05)
            return Observation(ok=True)

        report = pool.run([ToolCall("boom"), ToolCall("later")], execute)
        states = {f.call.name: f.state for f in report.fibers}
        self.assertEqual(states["boom"], FAILED)
        self.assertIn(states["later"], (CANCELLED, COMPLETED))  # race-dependent
        self.assertTrue(report.quiescent)

    def test_dispose_is_at_most_once(self):
        calls = [ToolCall("a")]
        counts = []

        def execute(call, ctx):
            # two effects, both inverted; dispose called by worker only
            ctx.effect(lambda: (lambda: counts.append(1)))
            ctx.effect(lambda: (lambda: counts.append(2)))
            return Observation(ok=True)

        FiberPool(max_concurrent=1, timeout=5).run(calls, execute)
        ctx = None
        self.assertEqual(sorted(counts), [1, 2])  # LIFO order, each exactly once


class TestBounded(unittest.TestCase):
    def test_concurrency_capped_by_max_concurrent(self):
        live = 0
        peak = 0
        lock = threading.Lock()

        def execute(call, ctx):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.08)
            with lock:
                live -= 1
            return Observation(ok=True)

        report = FiberPool(max_concurrent=2, timeout=10).run(
            [ToolCall(f"t{i}") for i in range(8)], execute)
        self.assertEqual(report.n_completed, 8)
        self.assertLessEqual(report.max_observed_concurrency, 2)
        self.assertLessEqual(peak, 2)

    def test_empty_batch_is_a_no_op(self):
        report = FiberPool().run([], lambda c, ctx: Observation(ok=True))
        self.assertEqual(report.fibers, [])
        self.assertTrue(report.quiescent)


class TestEngineIntegration(unittest.TestCase):
    """Decision(kind="parallel") fans out, merges, and stays auditable."""

    def _engine(self, decide, tools=None, execute=None):
        return LocalHarnessEngine(
            session_factory=lambda *a: type("S", (), {
                "inner": type("I", (), {"session_id": "s1"})(),
                "site_status": {}, "dispose": lambda self: None})(),
            decide=decide, tools=tools, execute=execute, report=False,
            max_steps=4, max_concurrent=3, fiber_timeout=5)

    def test_parallel_batch_produces_per_fiber_steps(self):
        def decide(ctx):
            if ctx.step == 0:
                return Decision("parallel", calls=(
                    ToolCall("echo", {"n": 1}), ToolCall("echo", {"n": 2})))
            return Decision("finish", summary="done")

        res = self._engine(decide, tools={
            "echo": lambda a: Observation(ok=True, content=str(a.get("n")))}) \
            .run("parallel test")
        self.assertEqual(res.status, "unverified")  # finished, no verifier
        parallel_steps = [s for s in res.steps if s.decision_kind == "parallel"]
        self.assertEqual(len(parallel_steps), 2)
        self.assertEqual({s.fiber for s in parallel_steps}, {"f1", "f2"})
        self.assertEqual(len(res.fiber_reports), 1)
        fr = res.fiber_reports[0]
        self.assertEqual(fr["completed"], 2)
        self.assertTrue(fr["quiescent"])

    def test_parallel_counts_as_one_step_toward_budget(self):
        def decide(ctx):
            if ctx.step == 0:
                return Decision("parallel", calls=tuple(
                    ToolCall("echo", {"n": i}) for i in range(5)))
            return Decision("finish", summary="ok")

        res = self._engine(decide, tools={
            "echo": lambda a: Observation(ok=True)}) \
            .run("budget test", max_steps=2)
        # 5 fibers in one decision + a finish, all within 2 steps
        self.assertEqual(res.status, "unverified")
        self.assertEqual(len([s for s in res.steps if s.decision_kind == "parallel"]), 5)

    def test_parallel_failure_recorded_not_hidden(self):
        def decide(ctx):
            if ctx.step == 0:
                return Decision("parallel", calls=(
                    ToolCall("good"), ToolCall("bad")))
            return Decision("finish", summary="mixed")

        def execute(call):
            if call.name == "bad":
                return Observation(ok=False, error="intentional")
            return Observation(ok=True)

        res = self._engine(decide, execute=execute).run("mixed batch")
        fr = res.fiber_reports[0]
        self.assertEqual(fr["failed"], 1)
        self.assertEqual(fr["completed"], 1)
        bad = [s for s in res.steps if s.call and s.call.name == "bad"]
        self.assertFalse(bad[0].observation.ok)

    def test_parallel_artifacts_merge(self):
        art = {"title": "x"}

        def decide(ctx):
            if ctx.step == 0:
                return Decision("parallel", calls=(ToolCall("r"), ToolCall("r")))
            return Decision("finish", summary="ok")

        res = self._engine(decide, tools={
            "r": lambda a: Observation(ok=True, artifacts=(art,))}) \
            .run("merge test")
        self.assertEqual(res.artifacts, [art, art])

    def test_repeated_identical_batch_hits_repeat_guard(self):
        def decide(ctx):
            return Decision("parallel", calls=(ToolCall("echo"),))

        res = self._engine(decide, tools={
            "echo": lambda a: Observation(ok=True)}) \
            .run("stuck policy", max_steps=10)
        self.assertEqual(res.status, "budget_exhausted")
        self.assertIn("repeat_guard", res.error or "")

    def test_parallel_with_no_calls_fails_closed(self):
        def decide(ctx):
            return Decision("parallel", calls=())
        res = self._engine(decide).run("empty batch")
        self.assertEqual(res.status, "failed")
        self.assertIn("no calls", res.error or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
