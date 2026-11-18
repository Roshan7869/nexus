"""Fiber layer — Cordis-style parallel tool/skill execution for the agentic engine.

Two Cordis mechanisms are mapped here, both verified against this installation:

From the DSH Cordis runtime (loader dialect + live plugin rows):
  * ``isolate: true | <realm>`` — each fiber gets its own child ``NexusContext``
    with realm ``local:fiber:<n>``. A fiber's tool effects are registered on, and
    disposed with, that fiber's context and no other (DSH practice #2: "Registrations
    are effects owned by a context").
  * ``fiberPhase`` — fibers carry an explicit lifecycle
    (PENDING → RUNNING → COMPLETED | FAILED | CANCELLED | TIMEOUT); terminal
    states are write-once.

From the Cordis paper (arXiv:2608.25512, already the basis of ``context.py``):
  * Quiescence (Thm80) — ``FiberPool.run`` is a barrier: it returns one merged
    report only when every fiber has reached a terminal state. The merged result
    is a function of the fibers' final states, never of timing.
  * Invertible effects, armed at-most-once — unchanged; each context is disposed
    exactly once by exactly one thread.

THE THREADING RULE (why this is safe without locks in context.py):
  ``NexusContext.dispose`` is armed/at-most-once but not thread-safe, and
  ``effect()`` appends to the accumulator. If a coordinator thread disposed a
  fiber's context while its worker was mid-call, a late ``effect()`` would append
  an inverse *after* disposal — a leak. So no thread ever touches another fiber's
  context: each child context is owned exclusively by its worker, which disposes
  it in its own ``finally``. The coordinator only reads terminal state under a
  per-fiber lock and never disposes anything. Cancellation is cooperative (a
  shared event checked before the call starts); a mid-flight call always runs to
  completion and disposes normally.

engine.py imports this module lazily (inside the loop) to avoid a cycle; this
module imports engine's data types at module level, which is safe because engine
never imports fibers at import time.

Stdlib only.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

from server.local_harness.context import NexusContext
from server.local_harness.engine import Observation, ToolCall

# ── lifecycle (DSH fiberPhase analogue) ──────────────────────────────────────

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TIMEOUT = "timeout"

_TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED, TIMEOUT})


@dataclass
class Fiber:
    """One parallel branch: a call, its isolated context, its terminal state."""
    id: str
    call: ToolCall
    state: str = PENDING
    observation: Observation | None = None
    error: str | None = None
    duration_ms: float = 0.0
    realm: str = ""
    context: NexusContext | None = None     # owned exclusively by its worker
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        return {
            "fiber": self.id,
            "tool": self.call.name,
            "state": self.state,
            "ok": self.observation.ok if self.observation else None,
            "error": self.error or (self.observation.error if self.observation else None),
            "duration_ms": round(self.duration_ms, 2),
            "realm": self.realm,
        }


@dataclass
class FiberReport:
    """Quiescent merge of a parallel batch. Produced only at the barrier."""
    fibers: list[Fiber] = field(default_factory=list)
    max_observed_concurrency: int = 0
    quiescent: bool = True
    leaked: list[str] = field(default_factory=list)   # fibers non-terminal after grace

    @property
    def observations(self) -> list[Observation]:
        return [f.observation for f in self.fibers
                if f.state == COMPLETED and f.observation is not None]

    @property
    def artifacts(self) -> list[dict]:
        out: list[dict] = []
        for f in self.fibers:
            if f.state == COMPLETED and f.observation is not None:
                for art in f.observation.artifacts:
                    if isinstance(art, dict):
                        out.append(art)
        return out

    @property
    def n_completed(self) -> int:
        return sum(1 for f in self.fibers if f.state == COMPLETED)

    @property
    def n_failed(self) -> int:
        return sum(1 for f in self.fibers if f.state == FAILED)

    @property
    def n_cancelled(self) -> int:
        return sum(1 for f in self.fibers if f.state == CANCELLED)

    @property
    def n_timeout(self) -> int:
        return sum(1 for f in self.fibers if f.state == TIMEOUT)

    def to_dict(self) -> dict:
        return {
            "fibers": [f.to_dict() for f in self.fibers],
            "completed": self.n_completed, "failed": self.n_failed,
            "cancelled": self.n_cancelled, "timeout": self.n_timeout,
            "max_observed_concurrency": self.max_observed_concurrency,
            "quiescent": self.quiescent,
            "leaked": list(self.leaked),
        }


def _default_max_concurrent() -> int:
    try:
        return max(1, min(16, int(os.environ.get("NEXUS_FIBER_MAX_CONCURRENT", "4"))))
    except (TypeError, ValueError):
        return 4


def _default_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("NEXUS_FIBER_TIMEOUT", "30")))
    except (TypeError, ValueError):
        return 30.0


def _default_on_fail() -> str:
    v = (os.environ.get("NEXUS_FIBER_ON_FAIL") or "continue").strip().lower()
    return v if v in ("continue", "cancel") else "continue"


class _Peak:
    """Atomic live-worker counter with peak tracking (the honest cap measurement)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)

    def exit(self) -> None:
        with self._lock:
            self._live = max(0, self._live - 1)


class FiberPool:
    """Bounded parallel executor: isolation, cooperative cancel, quiescent merge.

    ``execute`` receives ``(call, fiber_context)`` so a tool may register effects
    on its fiber's isolated context; those effects are disposed by the fiber's own
    worker when the fiber ends — on every path, success and failure alike.
    """

    def __init__(self, parent_realm: str = "local:default", *,
                 max_concurrent: int | None = None,
                 timeout: float | None = None,
                 on_fail: str | None = None,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.parent_realm = parent_realm
        self.max_concurrent = max_concurrent or _default_max_concurrent()
        self.timeout = timeout if timeout is not None else _default_timeout()
        self.on_fail = on_fail or _default_on_fail()
        self._clock = clock

    # ── terminal-state discipline ─────────────────────────────────────────
    @staticmethod
    def _set_terminal(fiber: Fiber, state: str, *, observation: Observation | None = None,
                      error: str | None = None) -> bool:
        """Write-once terminal transition. Returns False if already terminal."""
        with fiber._lock:
            if fiber.state in _TERMINAL:
                return False
            fiber.state = state
            if observation is not None:
                fiber.observation = observation
            if error is not None:
                fiber.error = error
            return True

    def run(self, calls: list[ToolCall],
            execute: Callable[[ToolCall, NexusContext], Observation]) -> FiberReport:
        """Fan out ``calls``; return one quiescent report when every fiber is terminal.

        Never raises: a broken executor becomes a FAILED fiber, recorded honestly.
        """
        calls = [c for c in (calls or []) if isinstance(c, ToolCall)]
        report = FiberReport()
        if not calls:
            return report

        fibers = [Fiber(id=f"f{i + 1}", call=c, realm=f"{self.parent_realm}:fiber:{i + 1}")
                  for i, c in enumerate(calls)]
        report.fibers = fibers
        cancel_event = threading.Event()
        peak = _Peak()

        def worker(fiber: Fiber) -> None:
            # Exclusive owner of fiber.context from creation to disposal.
            ctx = NexusContext(realm=fiber.realm)
            fiber.context = ctx
            started = self._clock()
            try:
                if cancel_event.is_set():
                    self._set_terminal(fiber, CANCELLED, error="cancelled_before_start")
                    return
                with fiber._lock:
                    fiber.state = RUNNING
                peak.enter()
                try:
                    obs = execute(fiber.call, ctx)
                    if not isinstance(obs, Observation):
                        obs = Observation(ok=True, content=str(obs) if obs is not None else "")
                except Exception as exc:
                    obs = Observation(ok=False, error=f"{type(exc).__name__}: {exc}")
                finally:
                    peak.exit()
                fiber.duration_ms = (self._clock() - started) * 1000.0
                if obs.ok:
                    self._set_terminal(fiber, COMPLETED, observation=obs)
                else:
                    self._set_terminal(fiber, FAILED, observation=obs, error=obs.error)
                    if self.on_fail == "cancel":
                        cancel_event.set()
            finally:
                # THE invariant: dispose on EVERY path, by the owning thread only.
                try:
                    ctx.dispose()
                except Exception:
                    pass

        executor = ThreadPoolExecutor(max_workers=self.max_concurrent,
                                      thread_name_prefix="nexus-fiber")
        try:
            futures = {executor.submit(worker, f): f for f in fibers}
            # Per-fiber timeout within one batch deadline. A timeout does not and
            # cannot kill the worker thread; it marks the fiber terminal and lets
            # the barrier below decide quiescence honestly.
            deadline = self._clock() + self.timeout
            for fut in futures:
                remaining = max(0.0, deadline - self._clock())
                done, not_done = wait([fut], timeout=remaining)
                if not_done:
                    fiber = futures[fut]
                    self._set_terminal(fiber, TIMEOUT, error=f"fiber_timeout:{self.timeout}s")
                    if self.on_fail == "cancel":
                        cancel_event.set()

            # Quiescence barrier: grace-wait ALL futures, not only fibers whose
            # state is non-terminal — a TIMEOUT-marked fiber's worker may still
            # be mid-call, and quiescence is about the workers, not the labels.
            # Python cannot kill threads, so after the grace period the pool
            # returns honestly non-quiescent with the leaked fiber ids; the
            # abandoned worker still disposes its own context in its finally,
            # so teardown is guaranteed eventually, merely later.
            grace = 2 * self.timeout
            pending = list(futures)
            if pending:
                _done, still = wait(pending, timeout=grace)
                for fut in still:
                    fiber = futures[fut]
                    self._set_terminal(fiber, TIMEOUT,
                                       error="unquiescent_after_grace")
                    report.quiescent = False
                    report.leaked.append(fiber.id)
                if still:
                    # Return without blocking forever on hung workers. Their
                    # contexts are still disposed by their own finally; only
                    # the barrier's "all disposed before return" is relaxed,
                    # and that relaxation is reported, never hidden.
                    executor.shutdown(wait=False, cancel_futures=True)
                    executor = None  # finally-block must not shutdown again

            report.max_observed_concurrency = peak.peak
        finally:
            if executor is not None:
                executor.shutdown(wait=True)

        return report
