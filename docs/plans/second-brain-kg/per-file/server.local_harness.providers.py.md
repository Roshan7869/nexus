# `server/local_harness/providers.py` — provider liveness snapshot (NEW)

| Field | Value |
|-------|-------|
| Layer / Phase | L0 / okf-native-cordis **Phase 4** (M9 spatial coeffects) |
| Status | DONE (implemented 2026-09-25, okf-native-cordis Phase 4) |
| New or edit | **NEW** (additive; delete-to-rollback) |
| Depends on | Phase 3 (harness context wiring); Phase 2 only if FAISS arm removed |
| First-principle atom | **A6_serve** |
| Gaps served | **G4** (daemon/process reliability), **G6** (MCP transport health) |

## Role — what this file is
A tiny, **stdlib-only, never-raising** probe module. Its single public function
`liveness_snapshot(realm)` returns `{coeffect_key: alive_bool}` for the Cordis coeffect
providers (`db`, `faiss`, `git`, `internet`, `embedder`). It is the **live signal source**
for the harness's spatial discipline: `session.refresh()` diffs this snapshot against the
currently-committed bindings and drives ACTIVE⇄INACTIVE transitions.

It holds no state, owns no lifecycle, and imports server internals **lazily** inside each
probe so a missing/ò broken subsystem degrades to `False` instead of crashing the harness.

## Issue it handles
Today `session.py:70` and `:74` are stubbed (`# ... provider handshake (M9) ...`). Only the
**temporal** half of Cordis (dispose/effect) runs live; the **spatial** half (provider death →
dependent deactivation) never fires because nothing feeds `classify_transition`
(`context.py:63`) real liveness. `providers.py` is that missing signal source.

## Research grounding
- **`arXiv:2602.10479` (Agentic AI software architecture)** — control-plane separation +
  circuit breakers. We take: liveness probing is a *cross-cutting reactive concern*, kept out
  of the effect body; a dead provider trips a "breaker" (deactivation) rather than throwing.
- **`arXiv:2503.06705` (Self-adaptive cloud reliability)** — supervised, health-driven state
  transitions. We take: bind→ACTIVE only after a provider *commits* healthy; loss → teardown.
- **`arXiv:2510.13467` (NetMCP / SONAR health-aware routing)** — health probes gate
  availability. We take: `internet`/`db` probes are best-effort with short timeouts; the probe
  result gates coeffect availability, exactly like NetMCP gates MCP endpoints.
- Cordis harness theory (harness design doc, spatial coeffect discipline) — the *why*: the
  spatial half of the effect system requires an external liveness oracle. This file is it.

## Interface / contract
```python
def liveness_snapshot(realm: str) -> dict[str, bool]:
    """Best-effort {coeffect_key: alive}. MUST NOT raise. Missing subsystem -> False."""
```
Invariants:
- Total function: any internal exception is caught → that key is `False`.
- Cheap: `internet` probe timeout ≤ 0.3s; `db` probe is `SELECT 1` on a short-lived conn.
- Pure read: never mutates DB, FAISS, or filesystem.
- Keys returned are a **superset** of any realm's `CLUSTER_KEYS`; callers read only the deps
  they declared (`session.refresh` filters by `self.ctx.deps`).

## Guided plan (diff sketch from Phase 4 doc)
```python
def liveness_snapshot(realm: str) -> dict[str, bool]:
    live = {}
    try:
        from server.db import get_connection
        c = get_connection(); c.execute("SELECT 1"); c.close(); live["db"] = True
    except Exception: live["db"] = False
    try:                                   # faiss only if still on hot path (Phase 2)
        from server.cluster_faiss import index_ready
        live["faiss"] = bool(index_ready())
    except Exception: live["faiss"] = False
    live["git"]      = _git_ok()           # `git rev-parse` in cwd, bounded
    live["internet"] = _net_ok(timeout=0.3)
    live["embedder"] = _embedder_ok()      # model importable / warm
    return live
```
Helpers `_git_ok`, `_net_ok`, `_embedder_ok` are private, each wrapped in try/except → bool.
`session.refresh()` (edited in Phase 4) calls this when no snapshot is injected; tests inject
a fake dict to exercise flap/idempotency without touching real providers.

## As built (deltas vs sketch, 2026-09-25)
- `cluster_faiss.index_ready` **does not exist** (sketch assumed it); the faiss probe runs a
  **real read** — `ClusterNumpySearch().get_cluster_resource_ids(["general"], limit=1)` —
  so "alive" means the legacy cluster path can actually serve, not just import.
- `embedder` probe is **import-only** (no model load: 36s cold start would violate the
  sub-second snapshot contract; `retrieve_chunks` already fails soft to FTS-only).
- `refresh()` recommit after recovery reuses the **stored resolver** from
  `commit_providers` (uids stay `key:session_id`-shaped instead of the sketch's
  `f"{k}:{id(self)}"`).
- `open_local_session` sets `state = ACTIVE` at open (providers commit there, so the
  session is live; `refresh()` reacts from ACTIVE thereafter).

## Acceptance (subset of Phase 4)
- [x] Every probe returns a bool even when the subsystem is absent (each probe wrapped;
      `test_liveness_snapshot_shape_never_raises`).
- [x] `internet` probe respects the 0.3s timeout (socket timeout; no DNS — direct 1.1.1.1).
- [x] Fake provider `True→False` via injected snapshot drives `refresh()` ACTIVE→INACTIVE and
      runs the registered inverse exactly once (`test_m9_provider_loss_disposes_once`).
- [x] Flap `True→False→True` → no double-dispose, no crash (`test_m9_flap_no_double_dispose`).
- Suite: `tests/test_local_harness.py` 41/41 pass.

## Impact gate + rollback
```
impact({target:"refresh", direction:"upstream", file_path:"server/local_harness/session.py"})
#   → LOW, 0 callers (driver TBD by design)
impact({target:"open_local_session", direction:"upstream"})
#   → LOW, 1 caller (MCP local_harness_open)
```
Rollback: file is additive — delete it and drop the `from ... import liveness_snapshot` line
added to `session.py`; the stubbed `refresh` returns behavior to temporal-only.
