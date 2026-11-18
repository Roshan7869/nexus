# A11 — Fiber layer: Cordis-style parallel tool/skill execution

**Files:** `server/local_harness/fibers.py` (new), `server/local_harness/engine.py` (Decision kind `"parallel"`, `Step.fiber`, `EngineResult.fiber_reports`)
**Tests:** `tests/test_fibers.py` — 19 tests, all passing; `tests/test_agentic_engine.py` 31/31; `tests/test_local_harness.py` 49/49

## Why

The agentic engine executed one tool per step. Research-shaped tasks fan out
naturally ("search arXiv + OpenAlex + Crossref at once"), so serial execution paid
the full latency of each call in sequence. This layer adds bounded parallelism
without giving up any invariant the harness was built on.

## Which Cordis, precisely

Two referents were verified, not assumed:

**DSH Cordis runtime** (installed here — loader dialect in `composition-reference.md`, live `plugin_manager` rows):
- `isolate: true | <realm>` → each fiber owns a child `NexusContext` with realm `local:fiber:<n>`. A fiber's tool effects are registered on, and disposed with, that fiber's context and no other — practice #2, *"registrations are effects owned by a context."*
- `fiberPhase` (seen on every live plugin row) → fibers carry an explicit lifecycle `PENDING → RUNNING → COMPLETED | FAILED | CANCELLED | TIMEOUT`; terminal states are write-once under a per-fiber lock.

**Cordis paper** (arXiv:2608.25512 — already the basis of `context.py`):
- Quiescence (Thm80): `FiberPool.run` is a barrier. One merged `FiberReport`, a function of final fiber states, never of timing.
- Invertible effects, armed at-most-once dispose — unchanged.

## Shape

```python
Decision(kind="parallel", calls=(ToolCall(...), ToolCall(...)))   # one decision, N fibers

FiberPool(parent_realm, max_concurrent=4, timeout=30, on_fail="continue"|"cancel")
         .run(calls, execute) -> FiberReport
```

- A parallel batch counts as **one step** toward `max_steps`; each fiber has its own timeout.
- The audit trail stays per-branch: one `Step` per fiber, tagged `fiber=f<n>`; `EngineResult.fiber_reports` carries the full pool report per batch.
- Knobs: `NEXUS_FIBER_MAX_CONCURRENT` (default 4, cap 16), `NEXUS_FIBER_TIMEOUT` (s, default 30), `NEXUS_FIBER_ON_FAIL` (`continue` | `cancel`).

## The threading rule (the one that matters)

`NexusContext.dispose` is armed/at-most-once but not thread-safe; `effect()` appends to the accumulator. A coordinator thread disposing a fiber's context while its worker is mid-call would let a late `effect()` append an inverse **after** disposal — a leak.

So **no thread ever touches another fiber's context.** Each child context is created by its worker, used by its worker, and disposed in its worker's own `finally` — on every path: success, failure, executor exception, cancel-before-start, and timeout. The coordinator only reads terminal state under the per-fiber lock and never disposes anything. Cancellation is cooperative (a shared event checked before the call starts); a mid-flight call always runs to completion and disposes normally.

One honest relaxation: after a fiber times out and the grace period (2× timeout) expires, the pool returns **non-quiescent** with the leaked fiber ids rather than blocking forever on a hung tool — Python cannot kill threads. The abandoned worker still disposes its own context eventually; only the "all disposed before return" guarantee is relaxed, and it is *reported* (`quiescent: false`, `leaked: [...]`), never hidden.

## Invariants, each with a test

| # | Invariant | Test |
|---|---|---|
| 1 | Distinct realm + context per fiber (`isolate`) | `TestIsolation` |
| 2 | Fiber effects disposed exactly once, invisible to siblings | `TestIsolation` |
| 3 | `run()` returns only when every fiber is terminal (Thm80) | `TestQuiescence` |
| 4 | Failure is a terminal state, recorded not raised | `TestQuiescence` |
| 5 | Timeout marks the fiber and reports non-quiescence honestly | `TestQuiescence` |
| 6 | Disposal on every terminal path (success/fail/exception/cancel) | `TestTeardownEveryPath` |
| 7 | LIFO inverse order preserved, each exactly once | `TestTeardownEveryPath` |
| 8 | Observed concurrency ≤ `max_concurrent` | `TestBounded` |
| 9 | Engine: per-fiber steps, merged artifacts, per-batch report | `TestEngineIntegration` |
| 10 | Parallel batch = one step toward budget | `TestEngineIntegration` |
| 11 | Parallel failure recorded, not hidden | `TestEngineIntegration` |
| 12 | Identical repeated batch trips the repeat guard | `TestEngineIntegration` |
| 13 | Empty batch fails closed | `TestEngineIntegration` |

## Not done, deliberately

- **No MCP tool registration** — `nexus-mcp-enhanced.py` wiring is unchanged; same reason as A10 (needs `fastmcp` live-testing in the server process).
- **No LLM parallel policy** — the policy still decides *what* to parallelize; a model-backed `decide` can emit `parallel` decisions through the same seam.
- **SQLite note:** tools execute on worker threads; the engine's DB writes happen on the coordinator thread (report/close), never inside fibers. Fiber tools must be thread-safe or use their own connections — the default toolbox (network research, echo) is.
