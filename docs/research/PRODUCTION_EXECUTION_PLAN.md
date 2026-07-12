# NEXUS Second Brain v2 — Production Execution Plan

| Field | Value |
|-------|--------|
| **Status** | READY FOR EXECUTION (ops phases need out-of-session approval) |
| **Date** | 2026-07-11 |
| **Branch** | `second-brain-v2` |
| **Authority** | [ADR-002](./ADR-002-research-authority-second-brain-v2.md) · [gap-paper-map.yaml](./gap-paper-map.yaml) |
| **Index** | GitNexus `nexus` (~7968 symbols, 300 processes) |
| **Goal** | Production-grade brain restore → unified routing cutover → closed feedback → autonomous gate |

---

## 0. How to read this plan

| Level | Meaning |
|-------|---------|
| **Phase** | Dependency-ordered batch (P0…P5). Do not start P(n+1) until P(n) gates pass. |
| **Task** | One gap (G1…G32) or cross-cutting work item. |
| **Subtask** | Atomic step with owner file/symbol and exit criterion. |
| **File map** | Primary edit / ops targets. **GitNexus symbols** = blast-radius anchors. |
| **Risk** | LOW / MED / HIGH / CRITICAL — CRITICAL = out-of-session only. |

**Production rules**

1. Never kill live MCP mid-agent-session without reconnect plan.  
2. Prefer extend existing modules over new parallel routers.  
3. Every code task ends with a **gate** (SQL check, pytest, curl, or A/B).  
4. Run GitNexus `impact` before editing high-fan-in symbols (`record_outcome`, `find_skills`, `route_task`).  
5. After multi-file code phases, re-index if needed: `node .gitnexus/run.cjs analyze`.

---

## 1. Master gap → file → GitNexus map

| Gap | Priority | Best fit | Primary files | GitNexus symbols / processes | Risk |
|-----|----------|----------|---------------|------------------------------|------|
| **G1** Empty DB | P0 | Restore pre-cutover | `db/nexus.db`, `db/nexus.db.pre-cutover-*`, `server/db.py` | DB path resolution; `/stats` consumers | **CRITICAL** |
| **G2** EFC schema | P0 | Inherit + fix contract | `db/schema.sql`, `migrations/*`, `server/efc.py`, `server/learning.py` | `_bridge_outcome_log`, `compute_efc` | HIGH |
| **G3** Embeddings desync | P0 | Restore + verify | `db/faiss/*`, `server/numpy_router.py`, `server/tool_attention_v4.py`, `scripts/rebuild_faiss.py` | `IntentRouter.load`, `NumpyRouter` | HIGH |
| **G4** System Python HTTP | P0 | supervisord venv | `supervisord.conf`, `scripts/start-daemon.sh` | `nexus_http_daemon.startup` | HIGH |
| **G5** DB locks / bulkhead | P0 | Fixture-only tests | `tests/conftest.py`, `test_*.py` (root), `server/db.py` | live conn sites | MED |
| **G6** MCP flaky | P0 | Single owner + reconnect | `supervisord.conf`, `server/nexus-mcp-enhanced.py`, `.mcp.json` | MCP entry `main` | HIGH |
| **G7** Unified flag off | P1 | Canary 1→2 | `server/nexus-mcp-enhanced.py`, env, `route_ab_compare` | `find_skills`, `_route_unified`, `_route_unified_v5` | MED |
| **G8** Triple routers | P1 | v5 SoT + v4 fallback | `server/router.py`, `router_v2/*`, `router_v2/v5.py` | `route_task`, `unified_route`, `unified_route_v5` | MED |
| **G9** Cold-start | P1 | Flag + UCB path | `router_v2/scoring.py`, `router_v2/fusion.py`, `scripts/cold_warm.py` | `route_two_tier`, `ucb_bonuses` | MED |
| **G10** Chunk links | P1 | Enforce resource_id | `server/router_v2/chunk_*`, `server/ingest/*`, migrations | FTS/RRF keying | MED |
| **G11** Supervisord unused | P1 | Apply conf | `supervisord.conf`, `run/`, `logs/` | ops only | **CRITICAL** |
| **G12** Outcome coverage | P2 | route_id lifecycle | `server/learning.py`, `scripts/auto_close_routes.py`, MCP `log_outcome` | `record_outcome`, `_auto_close_*`, `complete_route_turn` | MED |
| **G13** Silent pass | P2 | Fail-loud log | `server/nexus-mcp-enhanced.py` ~L109–122 | `find_skills` | LOW |
| **G14** Reflexion off | P2 | Cheap model wrong/partial | `server/autonomous_pipeline.py` | `AutonomousToolCaller` | MED |
| **G15** Model vs skill TS | P2 | Dual bandits | `core/learner.py`, `server/learning.py`, `_route_unified` | `NexusLearner`, `_update_thompson_belief` | MED |
| **G16** 3 HTTP apps | P3 | Document + deprecate | `core/server.py`, `nexus_http_daemon.py`, `unified.py` | route_map 43 routes | MED |
| **G17** Import cycle | P3 | Invert deps | `context_harness/engine.py`, `learning.py`, `router.py`, `context_harness/server.py` | GitNexus cycle | MED |
| **G18** Tool overload | P3 | Core MCP set | MCP client config, `tool_attention_v4.py` | top-k process_turn | LOW |
| **G19** Orphan HTTP | P3 | Tag ops | OpenAPI/docs on daemon | api_impact 0 consumers | LOW |
| **G20** on_event | P3 | lifespan | `server/unified.py`, `nexus_http_daemon.py` | `startup` | LOW |
| **G21** Root tests | P3 | Move under tests/ | `test_epsilon.py`, `test_degradation.py`, `test_edges_and_reflection.py` | live DB writers | MED |
| **G22** Autonomy default | P4 | Wire into broker | `autonomous_pipeline.py`, `broker_runtime.py`, MCP `autopilot_run` | `AutonomousToolCaller`, `create_task_run` | MED |
| **G23** Multi-IDE | P4 | 2-session test | `server/ide_manager.py`, daemon `/ide/*` | IDE endpoints | MED |
| **G24** Harness default | P4 | nexus+memory | `context_harness/engine.py`, `server.py` | `ContextHarnessEngine` | LOW |
| **G25** KG backup | P4 | Restore densified | `db/*`, `scripts/densify_kg.py`, `resource_edges` | edge densify scripts | MED |
| **G26** Active-set gate | P4 | Gate ∩ turn set | `hallucination.py`, `autonomous_pipeline.py` | `HallucinationGate.verify` | MED |
| **G27** Plans empty | P4 | Smoke after restore | `orchestrator.py`, `nexus_plan_executor.py`, MCP plan tools | `design_plan`, `execute_phase` | LOW |
| **G28** Coach boundary | P4 | Soft notes | `server/coach/*` | coach feeders | LOW |
| **G29** cluster_membership test | P5 | Fixture or restore | `tests/test_unified.py` | Phase0 migration test | LOW |
| **G30** Hardcoded counts | P5 | Relative gates | `server/test_v3.py` | Phase1/4/13 tests | LOW |
| **G31** Skips inventory | P5 | Mark network | `tests/*` | skip markers | LOW |
| **G32** CI brain gate | P5 | New script + CI | `scripts/brain_health_gate.py` (new), CI yaml | — | MED |

---

## 2. Dependency graph

```
Level 0 (ops CRITICAL, parallel only after stop):  G1
Level 1 (after G1):                                 G2, G3, G25
Level 2 (ops fleet):                                G4, G6, G11
Level 3 (code safe in-session):                     G5, G13, G21, G29, G30
Level 4 (routing cutover):                          G7 → G9 → G8
Level 5 (learning):                                 G12, G14, G15
Level 6 (quality / product):                        G10, G18, G22, G26
Level 7 (architecture):                             G16, G17, G19, G20, G24
Level 8 (hardening):                                G23, G27, G28, G31, G32
```

**Critical path (production):**  
`G1 → G2/G3 → G4/G11 → G13 → G7 → G12 → G9 → G26/G22 → G32`

---

## 3. Phases

### Phase 0 — Pre-flight & freeze (0.5h)

**Goal:** Safe execution envelope.

| Task | Subtasks | Files | Gate |
|------|----------|-------|------|
| **T0.1** Snapshot git + DB | S1 `git status` clean or stash; S2 copy live DB to `db/nexus.db.pre-exec-$(date +%s)`; S3 note PIDs of MCP/HTTP | `.git`, `db/` | Snapshot path recorded |
| **T0.2** Confirm authority docs | S1 open ADR-002 + gap map; S2 no design reopen | `docs/research/*` | Operator ACK |
| **T0.3** GitNexus baseline | S1 `list_repos`; S2 optional `check` cycles | `.gitnexus/` | Index present |

**Exit:** Operator written go for Phase 1 (CRITICAL).

---

### Phase 1 — Brain restore & data contract (G1, G2, G3, G25) — CRITICAL

**Goal:** Production registry with EFC schema + embeddings.  
**Risk:** CRITICAL — stop writers first.

#### Task G1 — Restore authoritative DB

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G1.S1 | Stop HTTP + MCP (out-of-session) | `supervisord` / pkill orphans | No process holds `nexus.db` |
| G1.S2 | Score backups | `db/nexus.db.pre-cutover-1782612021` (preferred), pre-restore, pre-efc | Table of counts |
| G1.S3 | Atomic swap | `cp pre-cutover → db/nexus.db` (+ WAL clean) | file size >> 1MB |
| G1.S4 | Path consistency | `server/db.py`, `NEXUS_DB_PATH`, daemon `DB_PATH` | all resolve same file |
| G1.S5 | Smoke SQL | resources, edges, outcome_log | `resources ≥ 2000` |

**GitNexus:** any code reading `DEFAULT_DB_PATH` / `DB_PATH` — impact before path refactors.

#### Task G2 — EFC schema contract

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G2.S1 | PRAGMA outcome_log | live DB | columns `efc_informativeness..efc_score` exist |
| G2.S2 | Align `db/schema.sql` | `db/schema.sql`, `migrations/` | schema.sql documents EFC |
| G2.S3 | Bridge dry-run | `server/learning.py:_bridge_outcome_log`, `server/efc.py:compute_efc` | insert one test outcome succeeds |
| G2.S4 | Migration idempotence | `scripts/migrate.py` or SQL | re-run safe |

#### Task G3 — Embedding / FAISS integrity

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G3.S1 | Count embeddings | `resource_embeddings` | `> 0` and correlated with resources |
| G3.S2 | Load NumpyRouter / TA | `server/numpy_router.py`, `tool_attention_v4.py:IntentRouter` | no "0 routable embeddings" |
| G3.S3 | Rebuild only if needed | `scripts/rebuild_faiss.py`, `db/faiss/` | index ids ⊆ resource ids |
| G3.S4 | Latency smoke | HTTP `/find_skills` or MCP | p95 acceptable after warm |

#### Task G25 — KG integrity (with restore choice)

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G25.S1 | Count `resource_edges` | DB | edges ≥ 5000 if pre-cutover |
| G25.S2 | If low edges, densify | `scripts/densify_kg.py` (edge_type!) | 0-edge resources < threshold |
| G25.S3 | Cluster membership | `cluster_membership` | rows > 0 |

**Phase 1 exit gates**

```sql
SELECT COUNT(*) FROM resources;              -- ≥ 2000
SELECT COUNT(*) FROM resource_embeddings;    -- > 0
SELECT COUNT(*) FROM resource_edges;         -- >> 0
PRAGMA table_info(outcome_log);              -- efc_* present
```

```bash
curl -s localhost:8080/stats | jq '.resources'   # multi-type, not skill:1 only
```

---

### Phase 2 — Production process fleet (G4, G6, G11) — CRITICAL

**Goal:** Single control plane, venv-only, supervised restart.

#### Task G11 + G4 — Apply supervisord

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G11.S1 | Validate conf | `supervisord.conf` (venv paths) | `supervisord -c … -n` dry or parse |
| G11.S2 | Set autostart true for http+mcp (or documented manual) | `supervisord.conf` | intentional |
| G11.S3 | Start supervisord | `run/supervisor.sock`, `logs/` | `supervisorctl status` RUNNING |
| G4.S1 | Confirm HTTP interpreter | `ps` / `/proc/PID/cmdline` | **venv** python, not `/usr/bin/python3` |
| G4.S2 | Health | `GET /health`, `GET /stats` | embedding cached; resource counts match Phase 1 |

#### Task G6 — MCP stability

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G6.S1 | One MCP program under supervisor | `supervisord.conf`, `.mcp.json` | single pid |
| G6.S2 | Client reconnect docs | `README` / AGENTS note | documented |
| G6.S3 | Handshake smoke | IDE/Grok MCP tools list | no broken pipe |

**Phase 2 exit:** `supervisorctl status` all RUNNING; health OK; MCP tools listable.

---

### Phase 3 — Safety & test bulkheads (G5, G13, G21, G29, G30) — code OK in-session

**Goal:** Prod isolation + observability; tests don’t trash live DB.

#### Task G13 — Fail-loud routing_log (LOW risk, do first)

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G13.S1 | Impact analysis | GitNexus `impact(find_skills)` | blast listed |
| G13.S2 | Replace `except: pass` with `logger.exception` | `server/nexus-mcp-enhanced.py` find_skills L109–122 | log line on forced fail |
| G13.S3 | Optional metric counter | same | optional |
| G13.S4 | Unit: insert success path | `tests/test_feedback_loop.py` | pass |

#### Task G5 + G21 — Test bulkhead

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G5.S1 | Ensure conftest tmp DB | `tests/conftest.py` | no default to live path |
| G21.S1 | Move or mark root tests | `test_epsilon.py`, `test_degradation.py`, `test_edges_and_reflection.py` | not run against live |
| G5.S2 | pytest isolation | `tests/` | 0 "database is locked" from suite |
| G29.S1 | Fix membership test | `tests/test_unified.py` | pass with fixture or live restored |
| G30.S1 | Relative thresholds | `server/test_v3.py` | no magic 1096; skip without `NEXUS_LIVE_TESTS` |

**Phase 3 exit:** `pytest tests/ -q` green (or known skip set only).

---

### Phase 4 — Unified routing canary (G7, G9, G8)

**Goal:** Production SoT = unified v2/v5 with research stack.

#### Task G7 — Canary USE_UNIFIED_ROUTER

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G7.S1 | Baseline A/B offline | MCP `route_ab_compare` | report top-k overlap |
| G7.S2 | Set env `USE_UNIFIED_ROUTER=1` on **one** MCP process | supervisord env / `.mcp.json` | process sees flag |
| G7.S3 | 20-query suite | scripts or manual | top-1 agreement ≥95% vs v4 **or** documented deltas |
| G7.S4 | Promote `=2` (v5) | `router_v2/v5.py:unified_route_v5`, `_route_unified_v5` | canary green |
| G7.S5 | Default conf for production | supervisord environment | default ≥1 after gate |

**GitNexus processes:** `find_skills` → `_route_unified` / `_route_unified_v5` → `unified_route` / `route_task`.

#### Task G9 — Cold-start activation

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G9.S1 | Confirm two-tier in path | `scoring.route_two_tier`, `fusion.two_tier_split` | cold_fraction 0.3 |
| G9.S2 | Preview warm | `scripts/cold_warm.py` | never-triggered surfaced |
| G9.S3 | Optional `--commit` exposure | same | `outcome=routed` only |
| G9.S4 | KPI dashboard query | SQL times_triggered | track 7-day never_triggered % |

#### Task G8 — Deprecate parallel routers

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G8.S1 | Document SoT path | ADR + README | v5 primary |
| G8.S2 | Keep v4 behind flag only | `find_skills` | no third silent path |
| G8.S3 | Later: remove dead exports | after 14d metrics | optional |

**Phase 4 exit:** Production MCP uses unified ≥1; A/B recorded; cold path exercised.

---

### Phase 5 — Closed feedback loop (G12, G14, G15)

**Goal:** Quality-weighted learning at production coverage.

#### Task G12 — Outcome coverage

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G12.S1 | Ensure every find_skills returns/logs route_id | `find_skills`, `routing_log` | id returned |
| G12.S2 | Auto-close cron | `scripts/auto_close_routes.py`, `learning._auto_close_*` | stale NULL closed |
| G12.S3 | Bridge EFC always on terminal | `record_outcome` → `_bridge_outcome_log` | efc_score NOT NULL rate high |
| G12.S4 | Agent policy | CLAUDE/AGENTS already require log_outcome | coverage KPI >80% window |

**GitNexus fan-in on `record_outcome`:** MCP log/report, broker, TA `complete_route_turn`, harness — **impact before edits**.

#### Task G14 — Reflexion wire (optional cost)

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G14.S1 | Inject cheap LLM callback | `AutonomousToolCaller` | wrong→reflection stored |
| G14.S2 | Perplexity remains OFF | flags | no fake scores |

#### Task G15 — Dual bandit clarity

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G15.S1 | Document skill vs model arms | ADR already | README |
| G15.S2 | Stop hardcoding sonnet as "truth" | `_route_unified` | neutral or cascade hook |
| G15.S3 | Optional FrugalGPT module | `core/learner.py` | separate feature flag |

**Phase 5 exit:** coverage metric; EFC populated on new outcomes; no silent insert failures.

---

### Phase 6 — Production quality path (G10, G18, G22, G26)

**Goal:** Chunk quality, small tool surface, autonomous safe default.

#### Task G26 — Active-set hallucination (before autonomy)

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G26.S1 | Impact HallucinationGate | gitnexus impact | callers listed |
| G26.S2 | verify(name, score, active_set=…) | `hallucination.py` | reject ∉ set |
| G26.S3 | after_model already rejects | `autonomous_pipeline.py` | unit 100% reject OOS |
| G26.S4 | Tests | `tests/` + server tests | pass |

#### Task G22 — Wire autonomy into broker

| Subtask | Action | Files / symbols | Gate |
|---------|--------|-----------------|------|
| G22.S1 | before_model on autopilot | `broker_runtime.py`, `autopilot_run` | tools injected |
| G22.S2 | after_model gate | same + HallucinationGate | no OOS execution |
| G22.S3 | Smoke task_run | MCP | end-to-end |

#### Task G18 — MCP tool surface

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G18.S1 | Core tool allowlist in client config | `.mcp.json` / IDE | core only |
| G18.S2 | top_k 8–12 | TA / unified | config |

#### Task G10 — Chunk linking

| Subtask | Action | Files | Gate |
|---------|--------|-------|------|
| G10.S1 | Measure link rate | SQL | baseline |
| G10.S2 | Ingest gate resource_id | `server/ingest/*` | reject null |
| G10.S3 | Expand sources | scripts | rate ↑ |

**Phase 6 exit:** autonomy smoke + gate tests; MCP surface documented.

---

### Phase 7 — Architecture hygiene (G16, G17, G19, G20, G24)

| Task | Subtasks | Files | Gate |
|------|----------|-------|------|
| **G16** | S1 document three surfaces; S2 deprecate unified merge or fold; S3 keep :8080 ops | `core/server.py`, `nexus_http_daemon.py`, `unified.py` | README SoT |
| **G17** | S1 break cycle engine↔learning↔router; S2 ports for record_outcome | harness + learning + router | GitNexus cycles=0 |
| **G19** | Tag ops routes | daemon OpenAPI | docs |
| **G20** | lifespan handlers | unified + daemon | no on_event warn |
| **G24** | default sources nexus+memory | `context_harness/engine.py` | doctor OK |

---

### Phase 8 — Hardening & CI (G23, G27, G28, G31, G32)

| Task | Subtasks | Files | Gate |
|------|----------|-------|------|
| **G32** | Implement `scripts/brain_health_gate.py` (resources, efc cols, embeddings, sample route); wire CI | new script + CI | exit 0 on prod host |
| **G27** | design_plan → execute → verify smoke | orchestrator, plan executor | plan_id created |
| **G23** | 2 IDE sessions | ide_manager + `/ide/*` | independent metrics |
| **G28** | coach notes only | `server/coach/*` | boundary doc |
| **G31** | skip inventory; `-m "not network"` | tests | offline green |

---

## 4. Production runbooks (copy-paste)

### 4.1 Out-of-session restore (Phase 1)

```bash
# 1) Stop fleet
venv/bin/supervisorctl stop all 2>/dev/null || true
pkill -f 'nexus-mcp-enhanced.py' || true
pkill -f 'nexus_http_daemon.py' || true

# 2) Snapshot empty/live
cp -a db/nexus.db "db/nexus.db.pre-exec-$(date +%s)"

# 3) Restore preferred brain (pre-cutover)
cp -a db/nexus.db.pre-cutover-1782612021 db/nexus.db
rm -f db/nexus.db-wal db/nexus.db-shm  # if clean restart

# 4) Verify
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("db/nexus.db")
print("resources", c.execute("select count(*) from resources").fetchone()[0])
print("edges", c.execute("select count(*) from resource_edges").fetchone()[0])
cols=[r[1] for r in c.execute("pragma table_info(outcome_log)")]
print("efc", [x for x in cols if "efc" in x])
PY

# 5) Start fleet (venv)
venv/bin/supervisord -c supervisord.conf
venv/bin/supervisorctl status
curl -s localhost:8080/stats | head -c 400
```

### 4.2 Canary unified router (Phase 4)

```bash
# On canary MCP only:
export USE_UNIFIED_ROUTER=1   # then 2 after A/B
# Restart MCP via supervisorctl restart nexus-mcp
# Run route_ab_compare on 20 tasks; record JSON
```

### 4.3 Brain health gate (Phase 8 target)

```bash
venv/bin/python scripts/brain_health_gate.py --min-resources 2000 --require-efc
# exit 0 only if production SLOs hold
```

---

## 5. Test matrix (production grade)

| When | Command | Expect |
|------|---------|--------|
| After Phase 1 | SQL counts + `/stats` | multi-thousand resources |
| After Phase 3 | `pytest tests/ -q` | green (skip network OK) |
| After Phase 4 | `route_ab_compare` suite | agreement / documented deltas |
| After Phase 5 | SQL coverage + EFC not null rate | ≥80% window |
| After Phase 6 | gate unit tests + autopilot smoke | 100% OOS reject |
| Continuous | `brain_health_gate.py` | exit 0 |

---

## 6. RACI (suggested)

| Phase | Ops (you/out-of-session) | Agent (in-session code) |
|-------|--------------------------|-------------------------|
| 0–2 | **Owner** restore/supervisord | Assist scripts/docs only |
| 3 | Review | **Owner** G13/G5/G21/tests |
| 4 | Env flag restart | **Owner** A/B harness, flag docs |
| 5–6 | Approve cost flags | **Owner** learning/gate/autonomy |
| 7–8 | CI secrets | **Owner** architecture + CI script |

---

## 7. Definition of Done (production)

- [ ] Live DB ≥ 2000 resources, EFC columns, embeddings load  
- [ ] Fleet under supervisord, **venv** HTTP + MCP  
- [ ] `USE_UNIFIED_ROUTER` ≥ 1 (target 2) with A/B record  
- [ ] Outcome coverage ≥ 80% rolling; EFC on new outcomes  
- [ ] Active-set hallucination gate enforced on autonomous path  
- [ ] `pytest tests/` green offline; `brain_health_gate` in CI/ops  
- [ ] ADR-002 + this plan + gap map consistent  

---

## 8. Explicit non-goals (this plan)

- Full GraphSAGE training (optional after densified restore)  
- Perplexity outcomes ON by default  
- Merging skill and model bandits into one table  
- Mid-session pkill of the MCP the agent is using without reconnect  

---

## 9. Related artifacts

| Artifact | Role |
|----------|------|
| `docs/research/gap-paper-map.yaml` | Gap → paper → algorithm → best_fit scores |
| `docs/research/ADR-002-research-authority-second-brain-v2.md` | Research SoT |
| `docs/second-brain-execution-plan.md` | Prior historical DAG |
| GitNexus `query` / `context` / `impact` | Live blast radius |

---

**Next operator action:** Approve Phase 1 restore (CRITICAL) or request Phase 3 in-session code start with **G13 fail-loud** only.
