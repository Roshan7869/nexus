# NEXUS Phase 1 — Cordis Architecture Implementation Plan

Status: **implemented** (P1.0–P1.6; commits `0ced4ad`, `1e9fa43`, `b7c04ec`, `1c77eb0`, `1ac11b2`, `f36667a`, `6a3abaa`). Full suite 490 passed. Live end-to-end (gate 3) not yet run. · Scope: `server/local_harness/`, `config/site_registry.yaml`, `server/site_registry.py`, `server/context_harness/` (harvest only), `tests/test_local_harness.py`

Source pattern: Cordis, "A Programming Paradigm for Spatiotemporal Composability" ([arXiv:2608.25512](https://arxiv.org/pdf/2608.25512)). We adapt the pattern (revertible effects + reactive coeffects), not the TypeScript code.

---

## 1. Goal

Make a local-harness session bind the sites a task needs, load them when the session starts, and remove everything they created when the session closes — with no leftover state.

Concretely: `open_local_session(task, project, task_type)` → sites for that task are activated as coeffects → each site's harvest registers an undo → `dispose_by_id(session_id)` runs those undos LIFO.

## 2. Current state (verified)

| Piece | Location | State |
|---|---|---|
| Effect primitive (run now, keep inverse) | `server/local_harness/context.py:69` `effect()` | Exists, tested |
| Coeffect declaration | `context.py:79` `require()` | Exists, tested |
| LIFO, at-most-once dispose | `context.py:41` `NexusContext.dispose()` | Exists, tested |
| Activate/deactivate on liveness change | `session.py:66` `refresh()` | Exists, tested |
| Cluster-implied coeffects | `session.py:17` `CLUSTER_KEYS` | Exists (`db`, `faiss`, `git`, `internet`) |
| Open → report → dispose path | `session.py:105` `open_local_session`, `:131` `dispose_by_id` | Exists, tested |
| Task → site binding | — | **Missing** |
| Site coeffect keys (`site:<id>`) | — | **Missing** |
| Harvest with undo | — | **Missing** (`context_harness` has `harness_target_site`, not wired) |
| Persisted wrapper registry | `session.py:31` `_WRAPPERS` | Process-local; lost on restart (documented, by design) |

Test baseline: `tests/test_local_harness.py` → 49 passed, 0 failed.

**Blocker:** `server/local_harness/` and `tests/test_local_harness.py` are **untracked in git**. No baseline commit exists.

## 3. Design

### 3.1 Config schema (new `tasks:` block)

Added under each project in `config/site_registry.yaml`:

```yaml
projects:
  kaari:
    sites: [...]            # unchanged: project-wide fallback
    tasks:
      coding:
        sites:
          - https://cloudinary.com/documentation/upload_images
      design:
        sites:
          - https://impeccable.style
```

Rule: a task uses `tasks.<task_type>.sites` if present; otherwise the project's `sites`. Unknown task types fall back to project sites. Never raise.

### 3.2 Coeffect keys

- Site coeffect key = `site:<sha1(url)[:12]>` (stable, no secrets in key).
- `open_local_session` calls `require(*site_keys)` in addition to `keys_for_cluster()`.
- Liveness for a `site:` key = harvested cache entry present and not expired (provider check added to `server/local_harness/providers.py`).

### 3.3 Harvest-on-activate with inverse

For each activated site key:

```python
def bind_site(lh, url):
    entry = harvest(url)                 # context_harness fetch_page; no side effect on failure
    store_cache(entry)                   # side effect
    lh.effect(lambda: lambda: drop_cache(entry.key))   # inverse registered
```

Rules:
- Harvest failure → do **not** register an inverse; mark key unsatisfied; session still opens (degraded, reported in result).
- Inverses are idempotent (`drop_cache` on missing key is a no-op).

### 3.4 Teardown

`dispose_by_id` already drains the accumulator. Phase 1 adds: report includes `sites_released: [...]` so callers can confirm cleanup.

## 4. Tasks

Each task is one commit. Do not start task N+1 until task N's gate passes.

| # | Task | Files | Gate |
|---|---|---|---|
| **P1.0** | Baseline: commit `server/local_harness/` + its tests as-is (after user approval) | git | `pytest tests/test_local_harness.py` → 49 pass |
| **P1.1** | Add `tasks:` schema + `get_task_sites(project, task_type)` loader; fallback to project sites | `config/site_registry.yaml`, `server/site_registry.py` | New tests: task hit, task miss → fallback, unknown project → empty, no raise |
| **P1.2** | `site_key(url)` + `require()` site keys in `open_local_session`; add `site:` liveness to providers | `server/local_harness/session.py`, `providers.py` | `require` contains site keys; liveness false when no cache entry |
| **P1.3** | `bind_site()` harvest with `effect()` inverse; failure path registers no inverse | `server/local_harness/` (new `sites.py`, ≤500 lines) | Harvest ok → cache row exists; dispose → row gone; harvest fails → session opens, no inverse |
| **P1.4** | `dispose_by_id` returns `sites_released`; flap test (activate/deactivate/activate) | `session.py` | Flap disposes exactly once; `sites_released` matches bound sites |
| **P1.5** | Expose `task_type` and optional `sites` on the MCP `local_harness_open` tool | `server/nexus-mcp-enhanced.py` | Tool signature test; default behaviour unchanged |
| **P1.6** | Update `skills/nexus-harness/SKILL.md` and `AGENTS.md` skill table to describe site binding | `skills/nexus-harness/SKILL.md`, `AGENTS.md` | Doc matches tool signature |

## 5. Gates (Phase 1 exit)

1. `pytest tests/test_local_harness.py -q` → all pass, plus new tests for P1.1–P1.5.
2. `pytest tests/ -q` → no regressions vs baseline (470 passed, 24 skipped at last full run).
3. End-to-end: open a `coding` session for `kaari` → sites harvested → report → cache rows removed → `sites_released` lists them.
4. No file over 500 lines (`CLAUDE.md` convention).
5. `detect_changes()` run before each commit (`AGENTS.md`); impact analysis run before editing existing symbols (`session.py`, `providers.py`, `nexus-mcp-enhanced.py`).

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Harvesting is network-bound; slow sites delay session open | Medium | Per-site timeout; harvest budget in ms; skip-and-report |
| Cache store location undecided (`project_contexts` vs new table) | Medium | Decide in P1.3 review; prefer existing table to avoid a migration |
| `_WRAPPERS` lost on server restart → undo never runs | Medium | Documented limitation for Phase 1; Phase 2 persists the session record |
| Untracked code could be lost | High | P1.0 before anything else |
| Injecting sites changes ranking for existing tasks | Low | Only tasks with a `tasks:` entry are affected; no entry → unchanged path |

## 7. Decisions needed

1. **Commit P1.0 now?** Commits the untracked `server/local_harness/` package as a baseline. (Recommended: yes.)
2. **Cache store:** reuse `project_contexts` (no migration) or add a `harvested_sites` table (cleaner, needs `db/migrations/` entry)? (Recommended: reuse, to keep Phase 1 small.)
3. **Persistence across restarts:** accept the Phase 1 limitation, or persist session records now? (Recommended: accept; revisit in Phase 2.)
4. **Schema confirmation:** is the `tasks: { <task_type>: { sites: [...] } }` shape in §3.1 what you want?

## 8. Out of scope (Phase 1)

- Hot module replacement and config reconciliation (Cordis component loader) — revisit only after the effect/coeffect core is used by more than sites.
- Porting Cordis TypeScript code.
- Tencent model-architecture items from `docs/nexus_cordis_tencent_evaluation.md`.
- The routing-ranking changes in `NEXUS_REMEDIATION_EXECUTION_PLAN_v4.md` (tracked separately).

## 9. References

- Cordis paper: https://arxiv.org/pdf/2608.25512
- Existing harness design: `docs/assistant_local_harness_design.md`, `docs/local_harness/README.md`
- Remediation plan (separate track): `NEXUS_REMEDIATION_EXECUTION_PLAN_v4.md`
