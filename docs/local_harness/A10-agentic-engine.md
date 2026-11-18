# A10 — Agentic harness engine

**Files:** `server/local_harness/engine.py`, `server/local_harness/toolbox.py`
**Tests:** `tests/test_agentic_engine.py` (31 tests, stdlib-only)
**Demo:** `scripts/agentic_engine_demo.py`

## Why this exists

`session.py` owns *state*: revertible effects, coeffects, bound sites, LIFO teardown.
Nothing owned *behaviour* — deciding what to do next, doing it, and recording what
actually happened. `harness.py` declared the protocol but `run_turn()` was a stub:

```python
raise UnsupportedHarness("streaming run_turn lands in A2; use task_start payload path")
```

`engine.py` is that missing loop, kept independent of any model so it is testable
without SQLite, the network, fastapi, or an LLM.

## Shape

```python
LocalHarnessEngine(
    session_factory = None,   # (task, project, ide, task_type) -> session
    decide          = None,   # TurnContext -> Decision      (the policy / "brain")
    execute         = None,   # ToolCall -> Observation      (override dispatch)
    verifier        = None,   # EngineResult -> (label, quality) | None
    tools           = None,   # {name: (args) -> Observation}
    max_steps       = 8,
    report          = True,   # call dispose_by_id (learning) on close
)
```

`run(task, project=, ide=, task_type=)` returns an `EngineResult` carrying the whole
audit trail: `status`, `outcome`, `quality`, `verified`, `steps[]`, `artifacts[]`,
`sites_released[]`, `error`.

Loop: `plan → open session → (decide → execute → observe)* → finish → verify → report + dispose LIFO`.

## Four invariants, each enforced by a test

| # | Invariant | Mechanism | Test |
|---|---|---|---|
| 1 | **Fail closed** | no `decide` → `blocked`, session never opened; session-open error → `blocked`; `abort` → `failed` | `TestFailClosed` |
| 2 | **No fabricated outcome** | `outcome` stays `"unverified"` unless a verifier returns a verdict; the policy's `summary` is never promoted | `TestNoFabricatedOutcome` |
| 3 | **Guaranteed teardown** | `dispose()` in a `finally`, and a failing report cannot strand the session | `TestTeardown` |
| 4 | **Bounded** | `max_steps`, plus `REPEAT_GUARD` for identical consecutive calls | `TestBoundedLoop` |

Invariant 2 is the load-bearing one. It matches the repo's enforcement suite
(`test_no_self_grade.py`, `test_sweeper_no_fabricated_correct.py`,
`test_quality_zero_preserved.py`): a turn that "finished" without a scorer reports
`unverified`, which `server/learning.py::_UNOBSERVED_LABELS` treats as telemetry,
never as a reward.

## Status vocabulary

| Status | Means |
|---|---|
| `blocked` | precondition unmet, nothing ran |
| `failed` | ran, but the policy/executor errored |
| `budget_exhausted` | steps or repeat guard hit without finishing |
| `completed` | policy finished **and** a verifier returned a verdict |
| `unverified` | policy finished; nothing verified it |

## Wiring

`AgenticHarness` implements the `Harness` protocol over the engine, so `run_turn`
works. Registering it is one line and is left to the caller so `OpenCodeHarness`
(still `UnsupportedHarness`, by design — it delegates to the IDE) is untouched:

```python
from server.local_harness.harness import REGISTRY
from server.local_harness.engine import AgenticHarness

REGISTRY.append(AgenticHarness())
```

`toolbox.build_default_tools()` binds `research` (harness_engine orchestrator),
`browser_status`, and `echo`. Every binding degrades to
`Observation(ok=False, error=...)` instead of raising, so a missing dependency shows
up in `steps[]` rather than vanishing.

## Verified end to end on Windows (no yaml/fastmcp/faiss installed)

```
$ python scripts/agentic_engine_demo.py --tool echo --no-report "smoke test"
status: unverified   session_id: 221157f689714d6c   verified: false
```

A **real** broker session opened despite `route_for_task` failing on `No module named
'yaml'` — degradation is recorded, not hidden. The research run recorded
`research_error: import_failed: No module named 'fastmcp'` in `steps[0].error` and
still closed with `outcome: unverified`.

## Deliberately not done

- **No MCP tool registration.** Wiring `local_harness_agentic_run` into
  `nexus-mcp-enhanced.py` is the next step but cannot be tested from here (that
  module needs `fastmcp`), so it was left out rather than written blind.
- **No LLM policy.** `decide` is the seam; a model-backed policy plugs in there.
- **No changes to `harness.py`/`session.py`/`tool_session_broker.py`.** Additive only.

## Side fix

`harness_engine/toolsets.py` `_RESEARCH_RE` missed common scholarly phrasings, so
`"retrieval augmented generation"` planned as `general`. Added `retrieval|benchmark|
dataset|preprint|proceedings|manuscript|publication|corpus|methodology`, with tests
covering both the new matches and a plain coding task that must stay `general`.
