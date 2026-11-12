# NEXUS LLD — Low-Level Design

Status: DRAFT v1 · 2026-09-13
Companions: `00-HLD.md`, `02-PLAN-MACRO.md`, `03-PLAN-MICRO.md`, `04-FEASIBILITY.md`

Conventions: section IDs (`S1`–`S8`) from `00-HLD.md` §4. Every path is real and present at HEAD
`c8782ad`. Line numbers were verified live.

---

## 1. S1 — Resource Catalog & Ingestion

### 1.1 Data model (live schema)

`resources` columns: `id, name, resource_type, repo_name, file_path, description, body_text, status,
tier, embedding, token_count, times_triggered, times_correct, times_wrong, last_used_at, created_at,
updated_at, cluster, cluster_confidence, archive_reason, archived_at, lifecycle_state`

Live cross-tab — the defect in one table:

| status | lifecycle_state | n |
|---|---|---|
| active | NULL | 1,730 |
| archived | archived | 734 |
| archived | NULL | 659 |
| **active** | **archived** | **132** |

### 1.2 LLD: collapse to one lifecycle

```sql
-- migration NNNN_unify_lifecycle.sql  (idempotent, recorded in schema_migrations)
UPDATE resources SET lifecycle_state = CASE
    WHEN status = 'archived' THEN 'archived'      -- resolves the 132 contradictions + 659 NULLs
    WHEN status = 'active'   THEN 'active'        -- 1,730 NULLs
    ELSE COALESCE(lifecycle_state, 'unknown')
  END
 WHERE lifecycle_state IS NULL
    OR (status = 'active' AND lifecycle_state = 'archived');
-- 2,521 rows touched
```

Then a new table with `CHECK (lifecycle_state IN ('active','archived','quarantined','unknown'))`,
copy, rename. Copy-don't-ALTER so the down-migration is available and the daemon can be stopped
mid-flight. `status` is retained as a **derived, read-only** column only until S2/S7 stop reading it.

**Invariant:** no row may have `status != lifecycle_state` except `quarantined`.
**Verification:** `SELECT COUNT(*) FROM resources WHERE status != lifecycle_state AND lifecycle_state != 'quarantined'` = 0.

### 1.3 Fiber state machine (runtime layer, S7 co-owner)

```python
class FiberState(str, Enum):
    INACTIVE  = "inactive"
    RELOADING = "reloading"
    ACTIVE    = "active"
    UNLOADING = "unloading"
```

DB `lifecycle_state` is the **persisted projection** of θ. The Fiber is authoritative in-memory; the
column is a cache. Any divergence is a bug, and the `CHECK` turns silent divergence into a loud one.

---

## 2. S2 — Router

### 2.1 Live control flow (`server/router_v2/v5.py`)

```
unified_route_v5(query, top_k=10, cold_fraction=0.3, ...)      # v5.py:401
  ├─ _l1_intent(query, db_path)                                # v5.py:199
  ├─ if confidence > short_circuit_confidence
  │    and pool_size >= short_circuit_min_pool:                 # v5.py:463
  │       _short_circuit(intent, top_k, db_path)                # v5.py:237  <-- NO COLD TIER
  │       return _result(..., "short_circuit", ...)             # v5.py:466
  └─ else: _v5_rerank(topic_merged, intent, top_k, cold_fraction, ...)  # v5.py:487
            ├─ hot_k  = max(1, round(top_k * (1 - cold_fraction)))      # v5.py:391
            ├─ cold_k = top_k - hot_k
            ├─ SELECT id FROM resources WHERE id IN (...) AND times_triggered > 0   # v5.py:365
            └─ return hot[:hot_k] + cold[:cold_k]                       # v5.py:397
```

### 2.2 The defect, precisely

`_short_circuit` (v5.py:237-268) builds from `intent["intent_rids"]`, applies `_autocut`, returns.
It never calls `_v5_rerank`. The caller passes `cold_fraction=0.0` on that path with the comment at
v5.py:481 — `# v5 does its own two-tier split in L3` — which is true of the full pipeline and false
of the short-circuit. The `times_triggered > 0` queries at v5.py:252 and :365 **annotate**, they do
not filter (`"hot": rid in triggered`, v5.py:266).

### 2.3 LLD: cold quota on the fast path

```python
def _short_circuit(intent, top_k, db_path, cold_fraction: float = 0.0):
    ...
    merged = _autocut(merged, top_k)
    if cold_fraction > 0.0:
        cold_k = top_k - max(1, int(round(top_k * (1.0 - cold_fraction))))
        if cold_k > 0:
            have = sum(1 for s in merged if not s.get("hot"))
            if have < cold_k:
                merged = merged[: top_k - (cold_k - have)] + _cold_backfill(
                    db_path, exclude={s["resource_id"] for s in merged},
                    limit=cold_k - have, pool=intent["intent_rids"])
    return merged
```

`_cold_backfill` selects `times_triggered = 0` rows from the cluster pool, reusing the existing
`times_triggered > 0` query inverted. ~10 LOC net.

**Alternative (cheaper, blunter):** when the short-circuit cannot fill the cold quota, fall through to
`full_pipeline`. One `if`, zero new functions — but costs latency on every short-circuit. Choose
after measuring; see `04-FEASIBILITY.md` §5.

**Regression gate:** top-1 agreement on `router_ab_suite` (20 queries) must not drop; `catalog.
invoke_cold_target_pct` ≥ 35.0 measured **on the short-circuit path**, not just overall.

### 2.4 Waterfall post-filter (`G_INTERCEPT`)

The natural interception point is after `_autocut`. Five dispatch modes; a listener that does not
call `next()` vetoes. Attach here rather than inside `_autocut` so the veto is observable and ordered.

---

## 3. S3 — Context Harness Engine (primary graft site)

### 3.1 Two registries, one canonical

```
fusion.py:34          class RRFFuser  (unregister at :44)     <- OLD
fusion.py:158-160     fuser.register(BackendDef("nexus"|"internet"|"memory", 1.0, fn))
server.py:918-1188    fuser.register(BackendDef(...)) x13     <- OLD, production
backend_protocol.py:35  class BackendRegistry                 <- NEW
backend_protocol.py:70  async def fuse_registered(...)        <- NEW
backend_protocol.py:142 "New code should prefer fuse_registered()" <- POLICY
```

The 13 production backends: `scrapling_fetch, sherlock_probe, scrapecraft_pattern, scrapling_spider,
arxiv, semantic_scholar, github, paperswithcode, google_scholar, openalex, crossref, pubmed,
target_site`. New registry has zero production users.

### 3.2 `BackendProtocol` contract (as-is)

```python
@runtime_checkable
class BackendProtocol(Protocol):
    name: str
    weight: float
    cache_ttl: float
    timeout_seconds: float
    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]: ...
    async def is_available(self) -> bool: ...
```

`fuse_registered()` runs all registered backends in parallel with timeout, dedup, weight, and error
isolation, fusing by RRF (`RRF_K = 60.0`).

### 3.3 LLD: add disposal without changing the contract

```python
# server/context_harness/backend_protocol.py  (additive only)
class BackendRegistry:
    def __init__(self):
        self._backends: dict[str, BackendProtocol] = {}
        self._stack = ExitStack()
        self._closed = False

    def register(self, backend: BackendProtocol) -> None:
        if self._closed:
            raise Disposed("BackendRegistry is closed")
        if backend.name in self._backends:
            raise KeyError(f"Backend '{backend.name}' is already registered")
        self._backends[backend.name] = backend
        close = getattr(backend, "aclose", None) or getattr(backend, "close", None)
        if close is not None:
            self._stack.callback(close)        # <- the entire CORDIS kernel

    def unregister(self, name: str) -> None:
        self._backends.pop(name, None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stack.close()                    # LIFO, reverse order
```

Note the contract is **unchanged** — `close()` is optional via `getattr`. Backends that implement it
become disposable; the other thirteen keep working untouched. This is why the graft is cheap.

### 3.4 Channels → backends mapping

`channels/` holds 15 modules: `arxiv_search, crossref_search, dispatcher, github_search,
google_scholar, openalex_search, paperswithcode_search, pubmed_search, scrapecraft_pattern,
scrapling_fetch, scrapling_spider, semantic_scholar, sherlock_probe, target_site`.

`target_site` is the **user-extensible** one — it is the entry point for custom per-use-case sites.
See `05-SITES-REGISTRY.md`.

### 3.5 Content pipeline (unchanged by this design)

`ContentRouter` (content_router.py) consumes ranked resource IDs from S2, loads content, applies
`compression.py` (`CompressedWindow`, `PackedSection`, `VolatileContentDetector`, `WindowPacker`,
`estimate_tokens`), returns the window. Caches: `L0Cache`, `L2Cache`, `get_ro_conn()`.

---

## 4. S4 — Harness Engine & Toolsets

### 4.1 Current: a static partition (`harness_engine/toolsets.py`)

```python
CORE_TOOLS     = ("find_skills", "report_outcome", "router_status", "learning_stats")
RESEARCH_TOOLS = ("harness_research", "harness_smart_research", "harness_research_plan",
                  "harness_arxiv", "harness_scholar_s2", "harness_github_search",
                  "harness_paperswithcode", "harness_openalex", "harness_crossref",
                  "harness_pubmed", "research_sites_list")
BROWSER_TOOLS  = ("harness_fetch", "harness_fetch_batch", "harness_target_site",
                  "harness_spider_start", "harness_spider_status", "harness_spider_results",
                  "harness_pattern_extract", "harness_sherlock", "browser_harness_status")
MEMORY_TOOLS   = ("context_harness", "context_search", "context_store", "context_doctor", ...)
```

Tuple constants — a partition that cannot be extended without a code edit.

### 4.2 LLD: Registry-backed toolsets

```python
class ToolsetRegistry:
    """Same shape as BackendRegistry. Mounting a toolset mounts its tools;
    unmounting disposes them. CORE_TOOLS stays a module-level constant (always mounted)."""
    def mount(self, name: str, tools: Iterable[str], *, requires: tuple[str, ...] = ()) -> Handle: ...
```

`requires` captures toolset dependencies (`BROWSER_TOOLS` needs the fetch channel healthy).
Unmounting a dependency cascades (`C_CASCADE`) by registering the child handle on the parent stack.

### 4.3 `attach.py` / `plan.py` / `orchestrator.py`

`attach` is already a mount: it binds a plan to a live execution surface. Under this design it returns
a `Handle` instead of mutating global state, so a failed attach cannot leave a half-bound plan.

---

## 5. S5 — MCP Surface

### 5.1 Servers (`.mcp.json`)

```
gitnexus      /home/roshan/.npm-global/bin/gitnexus mcp
claude-flow   /home/roshan/.npm-global/bin/ruflo mcp start   (autoStart false, 15 agents, hierarchical-mesh)
nexus         venv/bin/python3 server/nexus-mcp-enhanced.py
              env: NEXUS_DAEMON_URL=http://127.0.0.1:8080   <-- DEAD PORT (live: :8000)
                   USE_UNIFIED_ROUTER=2, NEXUS_THOMPSON_ROUTING=true,
                   NEXUS_CLUSTER_ROUTING=true, NEXUS_REFLECTION=true, NEXUS_IDE=claude-code
```

### 5.2 LLD: disposal for MCP registrations

`ephemeral_mcp_proxy.py` + `mcp_sync.py` + `mcp_sync_log`. Every synced tool is a registration.
Apply the same `Handle` discipline: syncing a server registers its tools *and* their removal on
desync. Today `sync_mcp_tools` upserts (`mcp_sync.py:222`) with no removal path — a desynced server's
tools persist. 329 MCP resources exist, **28 active (8.5%)**.

---

## 6. S6 — Execution / Agent Plane

### 6.1 The live multi-step flow (first disposer adopter)

```
harness_study_project → _harvest_all → harvest_site → _one → fetch_page
```

Per acquisition there are at least four resources needing reverse teardown: DB connection, HTTP
session, temp file, and any partially-written rows. Today teardown is distributed `try/finally`.

### 6.2 LLD: `server/disposer.py` (the whole kernel, ~30 LOC)

```python
"""CORDIS/DSH disposal kernel, adapted. The kernel is one constraint:
every resource registered while mounting simultaneously records its cleanup;
unload runs those cleanups in reverse."""
from contextlib import ExitStack


class Disposed(RuntimeError):
    """Raised on use-after-dispose (C_USE_AFTER)."""


class Handle:
    __slots__ = ("_stack", "_closed", "name")

    def __init__(self, stack: ExitStack, name: str):
        self._stack, self._closed, self.name = stack, False, name

    def effect(self, kind: str, cleanup, *, key=None):
        """Register a resource + its cleanup. C_REVERSE is free (ExitStack is LIFO)."""
        if self._closed:
            raise Disposed(f"{self.name}: cannot register {kind}")
        self._stack.callback(cleanup)
        return cleanup

    def adopt(self, child: "Handle") -> None:
        """C_CASCADE: disposing the parent disposes the child."""
        if self._closed:
            raise Disposed(f"{self.name}: cannot adopt {child.name}")
        self._stack.callback(child.close)

    def close(self) -> None:
        """C_IDEMPOTENT: safe to call twice. C_REVERSE: LIFO order."""
        if self._closed:
            return
        self._closed = True
        self._stack.close()


def atomic(build) -> Handle:
    """C_ATOMIC: a failed recomposition keeps the previous tree.
    pop_all() transfers the ledger only on success."""
    stack = ExitStack()
    h = Handle(stack, "recompose")
    try:
        build(h)
    except BaseException:
        stack.close()
        raise
    return Handle(stack.pop_all(), "active")
```

What `ExitStack` gives free: LIFO ordering, exception-safety on the stack, `pop_all()` atomic
transfer. What it lacks and we add: the `_closed` guard (use-after-dispose) and named kinds.

### 6.3 `G_MODULE` — explicitly NOT solved here

268 of 300 traced flows cross a community boundary; no abstraction names it. Fiber trees compose
runtime resources; they cannot impose static structure. **Fix:** `import-linter` contracts or a
package restructure, as a separate effort.

---

## 7. S7 — Learning & Feedback

### 7.1 `G_STATE`

`precondition.py:65-114` — a process-wide singleton `StateGates`. Live (backs MCP tools
`check_preconditions` / `update_state`; the research's retraction is correct, do **not** delete).
`GLOBAL_GATES` is read by `nexus-mcp-enhanced.py`, which is why deleting it broke things before.

### 7.2 Test-order leak (fix first — it is the baseline)

`tests/test_unified.py` passes 38/38 alone; full suite is 2 failed / 374 passed, both failures on the
learner-**disabled** path. Cause: learner state is mutated by one test and not restored.

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def _isolate_learner(monkeypatch):
    monkeypatch.setenv("USE_UNIFIED_ROUTER", "2")
    from server import learning
    saved = getattr(learning, "_ENABLED", None)
    yield
    if saved is not None:
        learning._ENABLED = saved
```

### 7.3 `StateGates` → Fiber

`StateGates` is `(gate_name → bool)` with no parent and no unload. Under C3 it becomes a `Fiber`
whose θ is the gate set and whose Γ disposes registered gates. One-flow change, additive.

---

## 8. S8 — Ops & Observability

`ops_metrics.py`, `harness_metrics.py`, `process_supervisor.py`, `dashboard/`,
`logs/ops_eval_latest.json`.

Live metrics that act as gates:
```
routing_coverage.total_routes = 6670     by_outcome.routed = 13
harness.metrics.closed_routes = 6657     (99.8% closed)
router.router_status.use_unified_router = 2   cold_fraction = 0.3   router_mode = unified_v5
catalog.invoke_cold_target_pct = 35.0    invoke_cold_gate_pass = True
```

`invoke_cold_target_pct` is the metric Track A2 must move **on the short-circuit path**.

### 8.1 New metric required by this design

`lifecycle_divergence_count` — rows where DB `lifecycle_state` disagrees with the in-memory Fiber θ.
Must be 0 in steady state after A3+C3. This is the observability that makes the dual-column collapse
verifiable rather than asserted.
