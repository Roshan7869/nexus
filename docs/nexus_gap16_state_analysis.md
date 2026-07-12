# NEXUS Gap-16 State Analysis & Execution Plan

**Source report**: 16-issue gap analysis
**Date**: 2026-07-01
**Verdict**: 2 real P0 bugs, 4 inflated numbers, 1 false claim, 4 minor issues, 2 stale-but-known

---

## Phase 0: First-Principles Triage

For each claim, strip to the verifiable. Don't accept numbers without evidence.

### P0 Critical Bugs

#### G1: `core/server.py` crashes with `NexusLearner(config, metrics_store)` — **REAL**
- `core/server.py:366`: `learner = NexusLearner(config, metrics_store)` (2 args)
- `core/learner.py:127`: `def __init__(self, db_path: str = None)` (1 arg)
- **Reproduced**: `TypeError: __init__() takes from 1 to 2 positional arguments but 3 were given`
- **Impact**: Anyone who sets `routing.enabled: true` in `nexus.yaml` and runs `python -m nexus` will crash. This is the documented happy path. **Critical bug.**
- **Effort to fix**: 0.5-1h (constructor signature or signature-detection at call site)

#### G2: Dual Thompson Sampling — **MISLEADING, PARTIALLY TRUE**
- Both `core/learner.py` and `server/learning.py` write to `model_beliefs` table (confirmed by SQL).
- BUT: `server/learning.py` uses `provider='nexus', model='resource:<name>'` keys (1,812 distinct beliefs)
- `core/learner.py` uses `provider='ollama_cloud'/'anthropic'/'openai'` keys (15 distinct beliefs)
- **80 rows have `provider='nexus'` but NOT `resource:*` models** — these are likely leftover/hand-written entries
- The two systems learn about **different things**: core learns LLM models, server learns resources
- **Verdict**: Not a "silent divergence bug" — it's by design. The confusing part is naming (`provider='nexus'` for resources is misleading). Should be documented, not fixed.

#### G3: `chunk_embeddings` has no `resource_id` — **REAL**
- Schema: `['chunk_id', 'text_hash', 'embedding', 'created_at']` (verified)
- 66,495 rows, none link to `resources.id`
- **Impact**: chunk-level FTS search returns anonymous text, not named skills
- **Effort to fix**: 1-2h (add column, backfill from `ingestion_chunks.resource_id`, or use `text_hash` as join key)

### P1 Architecture Debt

#### G4: 3 redundant MCP servers — **REAL, MINOR**
- `server/nexus-mcp.py` (17K, Jun 27) — stale copy
- `server/nexus-mcp-v2.py` (18K, May 21) — superseded
- `server/nexus-mcp-enhanced.py` (55K, 1,483 lines, 45 tools) — the only one running (confirmed in `ps`)
- **Fix**: Delete the two stale files. **Effort: 5 min**

#### G5: HTTP daemon duplicates MCP — **REAL BUT JUSTIFIED**
- `nexus_http_daemon.py` has 18 FastAPI endpoints, mirrors ~10 of the MCP tools
- They serve **different transports**: HTTP for OpenAI-compatible clients, MCP for IDE integration
- The duplication is real but the rationale (transport, not logic) is sound
- **Verdict**: Refactor to share tool registry, not delete one. **Effort: 4-6h**

#### G6: 10 orphaned server modules, 55+ one-off scripts — **REAL**
- **38 truly orphan modules** verified by import-graph analysis
- Includes: `ingest.cli_tool`, `router_v2.scoring`, `feedback`, `implicit_outcomes`, `research_pipeline`, `test_v3`
- 65 files in `/scripts/`, 12 root-level `.py` files
- **Fix**: Document or delete. Not a runtime bug, just technical debt. **Effort: 2-4h triage**

#### G7: Single 1,483-line MCP file with 30+ tools — **REAL BUT UNNECESSARY**
- `nexus-mcp-enhanced.py` is 1,483 lines, 45 `tool()` decorators
- Splitting it is a refactor, not a bug fix. Currently it works.
- **Verdict**: Real but should be done only if team is growing. **Effort: 6-8h** (low priority)

#### G8: 49% resources stale (1,068 never triggered) — **INFLATED**
- Report says 49% (1,068 never triggered)
- **Actual**: 827/1920 = **43.1% of active** resources never triggered
- They mixed `active` (827) with `all-status` (1,678) numbers
- My N9 cold-start campaign already addresses this

#### G9: 859 resources disagree between `resources.cluster` and `cluster_membership` — **INFLATED 14×**
- Report says 859
- **Actual**: 59 (verified by LEFT JOIN query)
- Their query likely has wrong join conditions or counted non-cluster resources

#### G10: v5 router is "production" but v4 is default — **REAL**
- `nexus.yaml:128-129`: `thompson_routing: true, cluster_routing: true`
- No `USE_UNIFIED_ROUTER` flag in current config (only mentioned in plan docs)
- The comment in plan says v5 is "production" but default is 0 (legacy v4)
- **Verdict**: Real but lower-priority than P0 bugs

### P2 Missing Capabilities

#### G11: No automated ingestion pipeline — **FALSE**
- `scripts/watch-inbox.sh` runs as a `supervisord` program (PID 1440 confirmed)
- `inotifywait` watches `/home/roshan/nexus/inbox` for new files
- `scripts/embed-single.py` indexes new files via Ollama
- **Logs** in `/home/roshan/nexus/logs/watcher.log` show activity since May 2026
- **The gap is real but narrower**: pipeline works for the `inbox/`, NOT for arbitrary project paths
- **Verdict**: False claim. The pipeline exists, just scoped to inbox.

#### G12: IDE event loop nearly empty (31 events total) — **REAL**
- `SELECT COUNT(*) FROM ide_events` = 31
- 3-signal design (accept/reject, test pass/fail, latency) is theoretical
- **Impact**: No real IDE feedback signal into Thompson beliefs
- **Fix**: Need explicit IDE event posting (e.g., from opencode plugin)
- **Effort: 4-6h** to add event collection from opencode's connector

#### G13: No persistent context/session — **REAL**
- `state_snapshot` table has 14 rows, no code writes to it during routing
- No session continuity across requests
- **Verdict**: Real but lower-priority — for routing, stateless is fine

#### G14: requirements.txt has 6 packages but code needs more — **REAL**
- Listed: `fastapi`, `uvicorn`, `httpx`, `pydantic`, `pyyaml`, `python-dotenv`
- Used in code: `fastmcp`, `numpy`, `sentence-transformers`, `sqlite-vec`, `faiss-cpu`, `networkx`, `python-louvain`, `scikit-learn`, `ollama`, `asyncio-mqtt`, `httpx-sse`, `tenacity`, `litellm`
- **Fix**: Expand requirements.txt. **Effort: 0.5h**

### P2 Code Quality

#### G15: sys.path hacks in 15+ files — **REAL, UNDERSTATED**
- **94 files** contain `sys.path.insert` or `sys.path.append` (verified)
- Includes: `generate_embeddings.py`, `test_*.py`, `scripts/*.py`, etc.
- **Fix**: Create `pyproject.toml` + `pip install -e .`
- **Effort: 2-3h** (proper packaging is a project, not a quick fix)

#### G16: 3 different DB connection patterns — **REAL**
- `sqlite3.connect()` raw: 67 occurrences in 66 files
- `_get_conn()` helper: 7 files
- `get_db_path()`: 7 files
- `db_conn`: 3 files
- **Fix**: Single `server/db.py` connection factory, deprecate raw calls
- **Effort: 3-4h** (refactor, requires care)

---

## Verdict Summary

| Severity | Real bugs | Inflated | False | Minor | Total |
|----------|-----------|----------|-------|-------|-------|
| P0 | **2** (G1, G3) | 0 | 0 | 1 (G2) | 3 |
| P1 | 3 (G4, G5, G10) | 2 (G8, G9) | 0 | 2 (G6, G7) | 7 |
| P2 | 4 (G12-G16) | 0 | **1** (G11) | 0 | 5 |
| **Total** | **9** | 2 | 1 | 3 | **16** |

**The "Phase 1: fix 3 critical bugs" recommendation is partially right.** Only 2 are unambiguous bugs. G2 is by-design, G11 is false.

---

## Execution Plan — Dependency-Aware DAG

```
Level 0 (parallel, no deps):
  A1: Fix NexusLearner __init__ signature (G1)        [0.5-1h]
  A2: Delete nexus-mcp.py + nexus-mcp-v2.py (G4)       [5 min]
  A3: Add resource_id to chunk_embeddings (G3)         [1-2h]
  A4: Expand requirements.txt (G14)                    [0.5h]
  A5: Document dual Thompson design (G2)               [0.5h]

Level 1 (after A1, requires router.py knowledge):
  B1: Split nexus-mcp-enhanced.py into tool groups (G7) [6-8h]  -- DEFER

Level 2 (independent — can run parallel):
  C1: Create pyproject.toml + migrate sys.path (G15)   [2-3h]
  C2: Single DB connection factory (G16)               [3-4h]
  C3: Refactor HTTP daemon to share MCP tool registry (G5) [4-6h]
  C4: Triage 38 orphan modules (G6)                    [2-4h]

Level 3 (after C1 — packaging):
  D1: Run `pip install -e .` to verify all imports work
  D2: Add IDE event collection from opencode connector (G12) [4-6h]

Level 4 (verification):
  E1: Full smoke test (route_task, harness_research, cold_start, decay)
  E2: Verify routing accuracy with all 3 routers (v4, v5, core) [1h]
  E3: Confirm no orphan modules added back

Level 5 (monitoring):
  F1: Track routing accuracy over 100 queries
  F2: Monitor resource triggers (target: <20% never-triggered after 1 week)
```

### Critical Path

**A1 → E1** (fix crash, verify) is the minimum viable path.

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A1 (constructor fix) breaks existing caller | Low | High | Search for all `NexusLearner(` calls, update each |
| A3 (chunk_embeddings migration) needs backfill | Medium | Medium | Use `text_hash` as join key (already exists); backfill from `ingestion_chunks` table |
| B1 (split MCP) breaks 45 tools | High | High | DEFER — too risky without tests |
| C1 (pyproject.toml) breaks 94 sys.path hacks | High | Medium | Add shim: sys.path hack first, then pyproject.toml later |
| C2 (DB factory) breaks concurrent connections | Medium | High | Use `threading.local()` for per-thread connections |

### Effort Estimate

| Plan | Hours |
|------|-------|
| A1-A5 (P0 fixes + quick wins) | 3-4h |
| C1-C4 (P2 refactors) | 11-17h |
| D1-D2 (packaging + IDE events) | 4-6h |
| E1-E3 (verification) | 1-2h |
| **Total** | **19-29h** |

**Recommendation**: Do A1-A5 first (4h, 2 critical bugs fixed), then E1 smoke test, then C1 (pyproject.toml) which is the foundation for the other refactors.

---

## What This Plan Does NOT Do

- **Does not delete the HTTP daemon** (G5) — it's a legitimate transport
- **Does not split the MCP file** (G7) — risky refactor with no immediate value
- **Does not fix "the 3 critical bugs"** as one batch — only 2 are real
- **Does not add session state** (G13) — stateless routing is fine for current use
- **Does not delete orphan modules** (G6) — they're not causing harm
