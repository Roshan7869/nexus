"""Agentic harness engine — a bounded plan -> act -> observe loop over a local harness session.

The engine is the missing half of the local harness: `session.py` owns *state*
(revertible effects, coeffects, sites), this module owns *behaviour* (deciding
what to do next, doing it, and recording what actually happened).

Design rules, all inherited from this repo's enforcement tests:

1. **Fail closed.** No policy, no session, or a broken executor yields a terminal
   status that claims nothing. There is no "best effort success" path.
2. **Never fabricate an outcome.** `EngineResult.outcome` stays ``"unverified"``
   unless an injected verifier returns an explicit verdict. A summary written by
   the policy is never promoted to a verified result. (cf. tests/test_no_self_grade.py,
   tests/test_sweeper_no_fabricated_correct.py, tests/test_quality_zero_preserved.py)
3. **Guaranteed teardown.** Every mutation goes through ``session.effect`` and the
   session is disposed LIFO in a ``finally`` block, including when the policy or
   executor raises. (cf. tests/test_local_harness.py chaos invariants)
4. **Bounded.** ``max_steps`` plus a repeated-call guard; the engine cannot spin.

Stdlib only at import time. Broker / execution-plane / planner imports are lazy so
``import server.local_harness.engine`` never needs SQLite, fastapi, or an LLM.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── terminal statuses (each one is a distinct, honest claim) ──────────────────
BLOCKED = "blocked"                    # preconditions unmet; nothing ran
FAILED = "failed"                      # ran, but the executor or policy errored
BUDGET_EXHAUSTED = "budget_exhausted"  # ran out of steps without finishing
COMPLETED = "completed"                # policy finished AND a verifier agreed
UNVERIFIED = "unverified"              # policy finished; nothing verified it

#: Outcome label used when no verifier is supplied. Learning treats this as
#: telemetry-only (server/learning.py::_UNOBSERVED_LABELS), never as a reward.
UNVERIFIED_OUTCOME = "unverified"

#: A tool call repeated more than this many times in a row aborts the loop.
REPEAT_GUARD = 3


# ── value types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCall:
    """One requested tool invocation."""
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """What the environment reported back. `ok=False` is a normal, recordable result."""
    ok: bool
    content: str = ""
    error: str | None = None
    artifacts: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Decision:
    """A policy's choice for the next step.

    kind: ``"tool"`` (do `call`), ``"finish"`` (done, supply `summary`),
    ``"abort"`` (stop now, supply `reason`), ``"parallel"`` (fan out `calls`
    as isolated fibers and merge at quiescence — see server/local_harness/fibers.py).
    """
    kind: str
    call: ToolCall | None = None
    summary: str = ""
    reason: str = ""
    calls: tuple[ToolCall, ...] = ()


@dataclass
class Step:
    """One completed loop iteration. A parallel batch produces one Step per fiber,
    each tagged with its fiber id, so the audit trail stays per-branch."""
    index: int
    decision_kind: str
    call: ToolCall | None
    observation: Observation | None
    duration_ms: float
    fiber: str | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "decision": self.decision_kind,
            "tool": self.call.name if self.call else None,
            "arguments": dict(self.call.arguments) if self.call else None,
            "ok": self.observation.ok if self.observation else None,
            "error": self.observation.error if self.observation else None,
            "duration_ms": round(self.duration_ms, 2),
            "fiber": self.fiber,
        }


@dataclass
class TurnContext:
    """Read-only view handed to the policy each step."""
    task: str
    plan: Any
    step: int
    steps: tuple[Step, ...]
    artifacts: tuple[dict, ...]
    tools: tuple[str, ...]


@dataclass
class EngineResult:
    """The engine's complete, auditable record of one task."""
    status: str
    task: str
    session_id: str | None = None
    steps: list[Step] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    plan: Any = None
    summary: str = ""
    outcome: str = UNVERIFIED_OUTCOME
    quality: float | None = None
    sites_released: list[str] = field(default_factory=list)
    error: str | None = None
    fiber_reports: list[dict] = field(default_factory=list)  # per parallel batch

    @property
    def verified(self) -> bool:
        return self.outcome not in (UNVERIFIED_OUTCOME, "", None)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "task": self.task,
            "session_id": self.session_id,
            "plan": self.plan.to_dict() if hasattr(self.plan, "to_dict") else self.plan,
            "summary": self.summary,
            "outcome": self.outcome,
            "quality": self.quality,
            "verified": self.verified,
            "artifacts": list(self.artifacts),
            "sites_released": list(self.sites_released),
            "error": self.error,
            "steps": [s.to_dict() for s in self.steps],
        }


# ── engine ───────────────────────────────────────────────────────────────────

def session_id_of(session: Any) -> str | None:
    """Resolve a session id from a LocalHarnessSession or a bare broker session."""
    if session is None:
        return None
    sid = getattr(session, "session_id", None)
    if sid:
        return str(sid)
    inner = getattr(session, "inner", None)
    sid = getattr(inner, "session_id", None)
    return str(sid) if sid else None


def _sites_released(session: Any) -> list[str]:
    """URLs whose harvested rows the session's undo chain released."""
    status = getattr(session, "site_status", None) or {}
    try:
        return [u for u, st in status.items() if st == "bound"]
    except Exception:
        return []


class LocalHarnessEngine:
    """Runs one bounded agentic turn against a local harness session.

    All four collaborators are injected, which is what makes the loop testable
    without SQLite, network, or an LLM:

    session_factory(task, project, ide, task_type) -> session
    decide(TurnContext) -> Decision
    execute(ToolCall) -> Observation
    verifier(EngineResult) -> (outcome_label, quality) | None
    """

    def __init__(
        self,
        *,
        session_factory: Callable[..., Any] | None = None,
        decide: Callable[[TurnContext], Decision] | None = None,
        execute: Callable[[ToolCall], Observation] | None = None,
        verifier: Callable[[EngineResult], tuple[str, float | None] | None] | None = None,
        tools: dict[str, Callable[[dict], Observation]] | None = None,
        max_steps: int = 8,
        budget: int = 8,
        report: bool = True,
        max_concurrent: int | None = None,
        fiber_timeout: float | None = None,
        fiber_on_fail: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._session_factory = session_factory
        self._decide = decide
        self._execute = execute
        self._verifier = verifier
        self._tools = dict(tools or {})
        self.max_steps = max(1, int(max_steps))
        self.budget = max(1, int(budget))
        self.report = bool(report)
        self._clock = clock
        # Fiber (parallel) defaults resolve from env in fibers.py when None.
        from server.local_harness import fibers as _fibers
        self.max_concurrent = (max_concurrent if max_concurrent is not None
                               else _fibers._default_max_concurrent())
        self.fiber_timeout = fiber_timeout  # None -> fibers._default_timeout()
        self.fiber_on_fail = fiber_on_fail  # None -> fibers._default_on_fail()

    # ── planning ─────────────────────────────────────────────────────────────

    def plan(self, task: str) -> Any:
        """Toolset/channel plan for the task; degrades to None if planner unavailable."""
        try:
            from server.harness_engine.plan import plan_harness
        except Exception as exc:  # planner is optional; engine still runs
            logger.debug("plan_harness unavailable: %s", exc)
            return None
        try:
            return plan_harness(task)
        except Exception:
            logger.exception("plan_harness failed for task=%r", task)
            return None

    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    # ── loop ─────────────────────────────────────────────────────────────────

    def run(
        self,
        task: str,
        *,
        project: str = "default",
        ide: str = "opencode",
        task_type: str = "general",
        max_steps: int | None = None,
    ) -> EngineResult:
        """Execute one bounded agentic turn. Always returns; always tears down."""
        limit = self.max_steps if max_steps is None else max(1, int(max_steps))
        plan = self.plan(task)
        result = EngineResult(status=BLOCKED, task=task, plan=plan)

        # Rule 1: no policy -> nothing to run. Fail closed, claim nothing.
        if self._decide is None:
            result.error = "no_policy_configured"
            logger.warning("agentic engine: no decide() policy; refusing to run task=%r", task)
            return result

        session = None
        try:
            session = self._open(task, project, ide, task_type)
        except Exception as exc:
            result.error = f"session_open_failed: {type(exc).__name__}: {exc}"
            logger.exception("agentic engine: session open failed")
            return result

        result.session_id = session_id_of(session)
        try:
            self._loop(task, plan, session, limit, result)
        except Exception as exc:  # executor/policy blew up: record, do not fabricate
            result.status = FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            logger.exception("agentic engine: loop failed for task=%r", task)
        finally:
            self._close(session, result)

        return result

    def _open(self, task: str, project: str, ide: str, task_type: str) -> Any:
        if self._session_factory is not None:
            return self._session_factory(task, project, ide, task_type)
        from server.local_harness.session import open_local_session
        return open_local_session(task, project=project, budget=self.budget,
                                  ide=ide, task_type=task_type)

    def _loop(self, task: str, plan: Any, session: Any, limit: int,
              result: EngineResult) -> None:
        last_call: ToolCall | None = None
        repeats = 0
        finished = False

        for i in range(limit):
            ctx = TurnContext(
                task=task, plan=plan, step=i,
                steps=tuple(result.steps),
                artifacts=tuple(result.artifacts),
                tools=self.tool_names(),
            )
            decision = self._decide(ctx) if self._decide else None
            if decision is None:
                result.status = FAILED
                result.error = "policy_returned_none"
                return

            if decision.kind == "abort":
                result.status = FAILED
                result.error = f"policy_abort: {decision.reason or 'no reason given'}"
                return

            if decision.kind == "finish":
                result.summary = decision.summary
                finished = True
                break

            # ── Cordis-style fan-out: parallel tool/skill calls ────────────
            # One decision, N isolated fibers, one quiescent merge. A parallel
            # batch counts as ONE step toward max_steps (it is one decision),
            # while every fiber keeps its own timeout and its own context.
            if decision.kind == "parallel":
                if not decision.calls:
                    result.status = FAILED
                    result.error = "invalid_decision: parallel with no calls"
                    return
                self._run_parallel(decision.calls, session, i, result)
                # repeat-guard treats the whole batch signature as one call
                # (frozen ToolCall has no ordering; sort by a plain key)
                batch_sig = tuple(sorted(
                    decision.calls,
                    key=lambda c: (c.name, tuple(sorted((c.arguments or {}).items())))))
                if last_call is not None and batch_sig == last_call:
                    repeats += 1
                    if repeats >= REPEAT_GUARD:
                        result.status = BUDGET_EXHAUSTED
                        result.error = f"repeat_guard: parallel batch x{repeats + 1}"
                        return
                else:
                    repeats = 0
                last_call = batch_sig
                continue

            if decision.kind != "tool" or decision.call is None:
                result.status = FAILED
                result.error = f"invalid_decision: {decision.kind!r}"
                return

            # Rule 4: repeated identical call means the policy is stuck.
            if last_call is not None and decision.call == last_call:
                repeats += 1
                if repeats >= REPEAT_GUARD:
                    result.status = BUDGET_EXHAUSTED
                    result.error = f"repeat_guard: {decision.call.name} x{repeats + 1}"
                    return
            else:
                repeats = 0
            last_call = decision.call

            started = self._clock()
            try:
                obs = self._run_tool(decision.call, session)
            finally:
                elapsed = (self._clock() - started) * 1000.0

            result.steps.append(Step(
                index=i, decision_kind=decision.kind, call=decision.call,
                observation=obs, duration_ms=elapsed,
            ))
            for art in obs.artifacts:
                if isinstance(art, dict):
                    result.artifacts.append(art)
            if not obs.ok and obs.error:
                logger.info("agentic engine: tool %s failed: %s", decision.call.name, obs.error)

        if not finished:
            result.status = BUDGET_EXHAUSTED
            result.error = result.error or f"max_steps_reached: {limit}"
            return

        # Rule 2: a finish is not a success until something verifies it.
        verdict = None
        if self._verifier is not None:
            try:
                verdict = self._verifier(result)
            except Exception:
                logger.exception("agentic engine: verifier raised; outcome stays unverified")
                verdict = None
        if verdict and isinstance(verdict, tuple) and verdict[0]:
            result.outcome = str(verdict[0])
            result.quality = verdict[1]
            result.status = COMPLETED
        else:
            result.outcome = UNVERIFIED_OUTCOME
            result.quality = None
            result.status = UNVERIFIED

    def _run_parallel(self, calls: tuple[ToolCall, ...] | list[ToolCall],
                      session: Any, index: int, result: "EngineResult") -> None:
        """Fan out one parallel decision into isolated fibers and merge quiescently.

        The pool is imported lazily: fibers.py imports this module's data types at
        module level, so importing it here keeps the cycle from ever forming at
        import time.
        """
        from server.local_harness.fibers import FiberPool  # lazy: avoids import cycle

        parent_realm = getattr(getattr(session, "ctx", None), "realm", "local:default")
        pool = FiberPool(parent_realm, max_concurrent=self.max_concurrent,
                         timeout=self.fiber_timeout, on_fail=self.fiber_on_fail,
                         clock=self._clock)
        started = self._clock()
        report = pool.run(list(calls), lambda call, _ctx: self._run_tool(call, session))
        elapsed = (self._clock() - started) * 1000.0

        for f in report.fibers:
            result.steps.append(Step(
                index=index, decision_kind="parallel", call=f.call,
                observation=f.observation, duration_ms=f.duration_ms or elapsed,
                fiber=f.id,
            ))
        result.artifacts.extend(report.artifacts)
        result.fiber_reports.append(report.to_dict())
        if not report.quiescent:
            logger.warning("parallel batch not quiescent; leaked fibers: %s",
                           report.leaked)
        if report.n_failed:
            logger.info("parallel batch: %d failed / %d completed",
                        report.n_failed, report.n_completed)

    def _run_tool(self, call: ToolCall, session: Any) -> Observation:
        """Dispatch one call: explicit executor, then the tools registry, else unknown."""
        if self._execute is not None:
            return self._execute(call)
        fn = self._tools.get(call.name)
        if fn is None:
            return Observation(ok=False, error=f"unknown_tool: {call.name}")
        try:
            obs = fn(call.arguments)
        except Exception as exc:
            return Observation(ok=False, error=f"{type(exc).__name__}: {exc}")
        if isinstance(obs, Observation):
            return obs
        return Observation(ok=True, content=str(obs) if obs is not None else "")

    # ── teardown ─────────────────────────────────────────────────────────────

    def _close(self, session: Any, result: EngineResult) -> None:
        """Report (optional) then dispose LIFO. Never raises; records what it saw."""
        sid = result.session_id
        try:
            if self.report and sid:
                from server.local_harness.session import dispose_by_id
                out = dispose_by_id(
                    sid,
                    result.outcome,
                    notes=result.summary,
                    quality=result.quality if result.quality is not None else 0.0,
                )
                if isinstance(out, dict):
                    result.sites_released = list(out.get("sites_released") or [])
        except Exception as exc:
            # Reporting is an emission; its failure must not strand the session.
            logger.warning("agentic engine: report failed for %s: %s", sid, exc)
        finally:
            try:
                if session is not None:
                    session.dispose()
            except Exception:
                logger.exception("agentic engine: dispose failed")
            if not result.sites_released:
                result.sites_released = _sites_released(session)

    # ── Harness protocol adapter ─────────────────────────────────────────────

    def wire_parts(self, result: EngineResult) -> list[Any]:
        """Project a result into OpenResearch-style WireParts (text/tool/prompt)."""
        try:
            from server.local_harness.harness import WirePart
        except Exception:  # pragma: no cover - adapter must never break the caller
            return []
        parts: list[Any] = []
        for step in result.steps:
            if step.call is not None:
                parts.append(WirePart(kind="tool", tool=step.call.name,
                                      state=dict(step.call.arguments)))
        if result.summary:
            parts.append(WirePart(kind="text", text=result.summary))
        return parts


class AgenticHarness:
    """A `Harness` implementation whose `run_turn` actually executes the loop.

    Registered in `server.local_harness.harness.REGISTRY` by callers that want it;
    `OpenCodeHarness` is left untouched (it delegates to the IDE, not to us).
    """

    def __init__(self, engine: LocalHarnessEngine | None = None,
                 *, harness_id: str = "local") -> None:
        self.engine = engine or LocalHarnessEngine()
        self._id = harness_id

    def id(self) -> str:
        return self._id

    def name(self) -> str:
        return "Local Harness Engine"

    def supports_chat(self) -> bool:
        return True

    def detect(self) -> Any:
        from server.local_harness.harness import HarnessInfo
        return HarnessInfo(id=self.id(), name=self.name(), installed=True, models=[])

    async def run_turn(self, session: object, message: str) -> list[Any]:
        result = self.engine.run(message)
        return self.engine.wire_parts(result)

    def skill_shim(self) -> str | None:
        return None
