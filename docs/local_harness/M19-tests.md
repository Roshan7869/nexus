# M19 — `tests/test_local_harness.py` (CREATE, acceptance suite)

## GitNexus queries
```
query({search_query: "tool session broker tests execution plane contract"})
```
Used: `bash ls tests/test_*.py` (30 files; patterns: `test_tool_session_broker.py`, `test_execution_plane_p4.py`, `test_unified.py`, phase tests P0-P9). Cordis acceptance: Thm7 (recovery invariant), Thm70 (ordering), Thm73 (termination), Thm80 (quiescence); Tencent gates: agreement +3-5pp, p95 flat.

## Current state
No local-harness tests. Broker/execution-plane suites are the style reference (in-memory DB fixtures via `conftest.py`, learning disabled by default for determinism).

## Blast radius: NONE (new test file)

## Pseudocode
```python
# tests/test_local_harness.py
import os
import pytest
from server.local_harness.context import NexusContext, classify_transition
from server.local_harness.session import LocalHarnessSession
from server.local_harness.loader import EntryTree, Entry, reconcile

def test_notify_trichotomy(): ...
def test_lifo_dispose():        # push 3 inverses, dispose, assert reverse order
def test_armed_at_most_once():  # dispose twice, second is no-op
def test_realm_isolation():     # local:A vs local:B breaker keys differ (M15)
def test_reconcile_insert_delete(): ...
def test_disabled_is_fresh_not_unretire(): ...
def test_intercept_in_place_no_reload(): ...
def test_rollback_on_error():   # failed apply restores backup
def test_ctx_none_identical():  # M7: broker without ctx == legacy behavior
def test_chaos_no_leak():       # 100 sessions w/ random failures -> _active empty (M3/M7)
def test_opencode_ide_preserved():  # M6: ide=="opencode" survives sanitize
def test_weights_default_identical():  # M10: w_shared=0 -> ranking unchanged
def test_gate_default_identical():     # M11: estimator 0.0 -> same short-circuit

# Flags: suite must pass with NEXUS_HARNESS_REDIS unset (M12 off) and with
# NEXUS_LOCAL_HARNESS=0 (M5 legacy delegate).
```

## Build steps
1. Write suite alongside implementation (TDD per M-doc).
2. `pytest tests/test_local_harness.py -v` green; then `pytest tests/ -q` (no regressions).
3. Chaos + kill-FAISS + <2s-reconcile drills recorded as CI logs in PR.

## Tests
This file IS the tests (self-describing). Gate: 100% of its cases pass offline.
