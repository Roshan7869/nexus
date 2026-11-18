# Local Harness per-file build guides — index (M1–M20)

Micro-level pseudocode plans, one file per change. Parent design: `docs/assistant_local_harness_design.md`. Research grounding: `docs/nexus_cordis_tencent_evaluation.md` (Cordis 2608.25512 + Tencent Hunyuan family).

## GitNexus standing note (applies to every M-doc)
- Index 2026-08-18 (8944 nodes / 15688 edges / 300 processes), HEAD drifted → STALE; `analyze` 2026-09-14 exit 0 but did not advance (KuzuDB v43 skew + FTS unavailable). Each M-doc lists the exact `context`/`impact`/`query` calls to re-run once the MCP graph is fresh, plus the `read`/`grep` equivalents actually used here.
- AGENTS.md gates: `impact()` before editing any symbol; `detect_changes()` before commit; halt-and-warn on HIGH/CRITICAL.

## New files (CREATE) — base harness
| # | Doc | Target | Risk |
|---|---|---|---|
| M1 | `M01-package-init.md` | `server/local_harness/__init__.py` | none |
| M2 | `M02-context-types.md` | `server/local_harness/context.py` | none |
| M3 | `M03-session.md` | `server/local_harness/session.py` | none alone / HIGH once wired (M7) |
| M4 | `M04-loader.md` | `server/local_harness/loader.py` | none |
| M8 | `M08-opencode-adapter.md` | `server/opencode_adapter.py` | none |
| M12 | `M12-redis-cache.md` | `server/cache/redis_cache.py` | none (guarded) |
| M18 | `M18-migration.md` | `db/migrations/020_local_harness.sql` | low |
| M19 | `M19-tests.md` | `tests/test_local_harness.py` | none |
| M20 | this index | `docs/local_harness/README.md` | — |

## New files (CREATE) — OpenResearch + Archify adaptations (research-backed)
| # | Doc | Target | Research root |
|---|---|---|---|
| A01 | `A01-harness-registry.md` | `server/local_harness/harness.py` | OpenResearch `harness/mod.rs` trait+registry |
| A03 | `A03-playbook.md` | `server/local_harness/playbook.py` | OpenResearch `SYSTEM_PROMPT.md` durable/task split |
| A06b | `A06b-nexus-exp.md` | `scripts/nexus_exp.py` | OpenResearch cardinal rules 1–4 |
| A06c | `A06c-migration-021.md` | `db/migrations/021_experiments.sql` | OpenResearch `store.rs` + Cordis Thm80 |
| A07 | `A07-artifacts.md` | `server/execution_plane.py` (+manifest keys) | OpenResearch orx-reports/evidence |
| A09 | `A09-skill-packs.md` | `skills/nexus-*/SKILL.md` | OpenResearch `orx skills add` + skill-builder |
| B01 | `B01-runtime-map.md` | `docs/nexus-maps/nexus-runtime.architecture.json/.html` | Archify SKILL.md authoring path |
| B03 | `B03-flow-irs.md` | `docs/nexus-maps/*.workflow|sequence|lifecycle.json` | Archify type router + Cordis Alg5 |

## Modifications (MODIFY, additive-default-off unless noted)
| # | Doc | Target | Risk |
|---|---|---|---|
| M5 | `M05-mcp-tools.md` | `server/nexus-mcp-enhanced.py` (+4 tools, no signature changes) | medium |
| M6 | `M06-execution-plane.md` | `server/execution_plane.py` (+`local_session=None`; opencode already allowlisted) | medium |
| M7 | `M07-broker.md` | `server/tool_session_broker.py` (+`ctx=None`, dispose after learn) | HIGH |
| M9 | `M09-preconditions.md` | `server/precondition.py` (+subscribe/notify, `check()` untouched) | HIGH |
| M10 | `M10-fusion.md` | `server/router_v2/fusion.py` (+`shared` term @0.0, recycle cap @0) | medium |
| M11 | `M11-router-v5.md` | `server/router_v2/v5.py` (extracted gate + Chimera behind flag) | HIGH |
| M13 | `M13-context-cache.md` | `server/context_harness/cache.py` (L4 wrapper + `NEXUS_DB_PATH` fix) | medium |
| M14 | `M14-rate-proxy.md` | `server/integrations/rate_limited_proxy.py` (docs only) | none |
| M15 | `M15-circuit-breaker.md` | `server/resilience/circuit_breaker.py` (+realm, default-compatible) | medium |
| M16 | `M16-mcp-config.md` | `.mcp.json` (+5 env pins) | low |
| M17 | `M17-opencode-shell.md` | `opencode-with-nexus.sh` (+ide export, status probe) | low |

## Build order
Phase 0: M1→M2→M3→M4→M19-skeleton→M12→M18 (pure-new, safe) · Phase 1: M8→M5→M6→M7→M17→M16 (IDE wiring) · Phase 2: M9→M10→M11 (reactive+routing behind flags, A/B) · Phase 3: M13→M15→M14 (cache/compat hardening) · Gates: `pytest tests/ -q`, `bash scripts/ci_eval.sh`, chaos-100, kill-FAISS, <2s reconcile, p95<300ms.
