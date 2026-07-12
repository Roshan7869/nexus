# ADR-002 — Research Authority for NEXUS Second Brain v2

| Field | Value |
|-------|--------|
| **Status** | Accepted (design authority) |
| **Date** | 2026-07-11 |
| **Branch** | `second-brain-v2` |
| **Companion** | [`gap-paper-map.yaml`](./gap-paper-map.yaml) |
| **Evidence** | GitNexus graph + live DB audit + arXiv/SSRN map |

---

## 1. Context

NEXUS is a **resource intelligence layer** (skills/tools/agents → task) with optional LLM gateway routing. Second-brain v2 adds unified hybrid retrieval, EFC-weighted learning, context harness, and autonomous tool calling.

A full GitNexus-backed investigation showed:

- **Code for research-backed algorithms largely exists** (`router_v2`, `efc.py`, `tool_attention_v4`, `autonomous_pipeline`, `HallucinationGate`).
- **Live system is brain-dead**: `db/nexus.db` has ~1 resource; full corpus (~2.8k resources, EFC columns, edges) lives in backups.
- **Production path still defaults to legacy v4** (`USE_UNIFIED_ROUTER=0`).
- **Feedback path can silently fail** and EFC bridge cannot write if schema/data are missing.

This ADR freezes the **research authority** (which papers win) and the **best-fit solution** per gap so implementation does not reinvent algorithms or re-open settled design choices.

---

## 2. Decision summary

| Layer | Authority paper(s) | NEXUS SoT implementation |
|-------|--------------------|---------------------------|
| Tool selection / schema tax | Tool Attention [arXiv:2604.21816](https://arxiv.org/html/2604.21816v1); Lost in the Middle [arXiv:2307.03172](https://arxiv.org/abs/2307.03172) | ISO cosine gate, top-k, lazy schemas; never dump full catalog |
| Hierarchical / active MCP discovery | MCP-Zero [arXiv:2506.01056](https://arxiv.org/abs/2506.01056); AnyTool [arXiv:2402.04253](https://arxiv.org/abs/2402.04253) | Cluster prefilter + on-demand schema; `AutonomousToolCaller` |
| List fusion | RRF Cormack SIGIR 2009 | `router_v2/fusion.py` k=**60** |
| Multi-objective rank | 4-term blend (plan P2/P6); multi-channel LTR [arXiv:2602.23530](https://arxiv.org/html/2602.23530v1) | **0.40 ISO + 0.15 Thompson + 0.25 EFC + 0.20 UCB** |
| Feedback quality | EFC [arXiv:2605.29682](https://arxiv.org/abs/2605.29682) (in `efc.py`) | `EFC_t = 10·I·V·R·M`; bridge in `learning._bridge_outcome_log` |
| Explore cold tools | UCB / cold-start bandits; SSRN cold-start / KG-TS | Two-tier 70/30; UCB=1.0 if never triggered |
| Verbal learning | Reflexion [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) | Pluggable on wrong/partial only |
| Model cost routing | FrugalGPT [arXiv:2305.05176](https://arxiv.org/abs/2305.05176); RouteLLM [arXiv:2406.18665](https://arxiv.org/html/2406.18665v3) | **Separate** from skill bandit (dual arm spaces) |
| Tool hallucination | Reliability alignment [arXiv:2412.04141](https://arxiv.org/html/2412.04141v1) | Active-set `HallucinationGate` + registry checks |
| KG densify | GraphSAGE [arXiv:1706.02216](https://arxiv.org/abs/1706.02216) | Prefer restore densified edges; FAISS NN not O(n²) |
| Serve / reliability | Agentic architecture [arXiv:2602.10479](https://arxiv.org/html/2602.10479v1) | supervisord + venv + health gates |
| Data integrity | Data-centric ML [arXiv:2506.16051](https://arxiv.org/abs/2506.16051); SmartDiff [arXiv:2509.00293](https://arxiv.org/abs/2509.00293) | Versioned restore + count/schema gates |

**Rejected as production defaults:** dumping all MCP tools; silent `except: pass` on learning; unified skill path hardcoding a single model name without a cascade module; O(n²) pairwise KG; flipping `USE_UNIFIED_ROUTER=2` without A/B.

---

## 3. First principles (irreducible atoms)

1. **Registry** — one authoritative resource set (embeddings, clusters, edges).  
2. **Retrieve** — task → candidate list (hybrid sparse+dense).  
3. **Rank** — multi-signal score (semantic + quality + explore).  
4. **Gate** — only active, precondition-ok, in-set tools execute.  
5. **Feedback** — outcomes update beliefs with quality weights.  
6. **Serve** — one warm process fleet; tests never share prod locks.

Every gap maps to a failure of one of these atoms. See `gap-paper-map.yaml` → `first_principles`.

---

## 4. Algorithm extract (components)

### 4.1 RRF (Cormack 2009)

```
score(d) = Σ_i  1 / (k + rank_i(d))    # k = 60
```

**NEXUS:** fuse FTS5 list + dense/bitvec list in `reciprocal_rank_fusion`.  
**Invariant:** inputs best-first; missing list contributes 0.

### 4.2 ISO + Tool Attention gate (arXiv:2604.21816)

```
ISO(q, t) = cos(e_q, e_t)
A_t = { t | ISO(q,t) ≥ θ }[:top_k]   # paper θ≈0.28, top_k∈[8,12]
inject summaries for pool; full schemas only for A_t
```

**NEXUS:** `tool_attention_v4` + adaptive θ in `unified_route`; lazy load.

### 4.3 Four-term blend (Second Brain plan)

```
combined = 0.40·iso_n + 0.15·thompson + 0.25·efc_n + 0.20·ucb
```

**NEXUS:** `DEFAULT_BLEND_WEIGHTS` in `router_v2/fusion.py`.  
**Cold:** if n_i=0 → high UCB so never-triggered can surface.

### 4.4 Two-tier hot/cold

```
hot_k, cold_k = split(top_k, cold_fraction=0.3)
merged = rank(hot)[:hot_k] + rank(cold; iso+ucb only)[:cold_k]
```

### 4.5 EFC (arXiv:2605.29682, approximate)

```
EFC_t = κ · I_t · V_t · R_t · M_t     # κ=10
# proxies: I/V from outcome label; R from 1h repeats; M from first-use
```

**NEXUS:** `server/efc.compute_efc` → columns on `outcome_log` via `_bridge_outcome_log`.

### 4.6 Thompson Sampling (skill beliefs)

```
θ ~ Beta(α, β); pick argmax; update α,β from reward
```

**NEXUS:** skill-level in blend + `learning._update_thompson_belief`; model-level stays in `core/learner` / gateway until cascade module.

### 4.7 Hallucination gate

```
allow iff exists ∧ status=active ∧ score≥0.45 ∧ preconditions ∧ (optional) name ∈ active_set
```

**NEXUS:** `HallucinationGate` + `AutonomousToolCaller.after_model`.

### 4.8 Reflexion (arXiv:2303.11366)

```
on failure → verbal reflection → episodic memory → retry policy
```

**NEXUS:** pluggable; default offline `routed` (no fake LLM).

---

## 5. Multidimensional best-fit (by priority)

Scores in YAML: `fit_codebase`, `research_strength`, `perf_upside`, `cost_latency`, `blast_radius`.

### P0 — Must fix before any “performance” claim

| Gap | Root cause (first principle) | Best fit | Why |
|-----|------------------------------|----------|-----|
| **G1** Empty live DB | Atom Registry failed | **Restore `pre-cutover` backup** | Max edges+EFC+resources; lowest hours |
| **G2** No EFC cols | Feedback store contract broken | **Restore inherits cols + fix `schema.sql`** | ALTER alone doesn’t restore brain |
| **G3** FAISS desync | Retrieve index ≠ registry | **Restore embeddings then verify load** | Avoid full re-embed if checksum OK |
| **G4** System Python HTTP | Serve skew | **supervisord + venv** | Conf already correct |
| **G5** DB locks | No bulkhead | **Fixture DBs for tests** | Prod isolation |
| **G6** MCP flaky | No single owner | **Supervisord-owned MCP + reconnect** | Control plane |

### P1 — Cutover to research stack

| Gap | Best fit | Gate |
|-----|----------|------|
| **G7** Flag default 0 | Canary `USE_UNIFIED_ROUTER=1` then `=2` | top-1 ≥95% vs v4 on 20 queries |
| **G8** Triple routers | v5 SoT, v4 fallback only | Deprecation after metrics |
| **G9** Cold-start stuck | Restore + unified + `cold_warm` | never_triggered% drops in 7d |
| **G10** Chunk links | Expand ingest + enforce `resource_id` | chunk hit rate in A/B |
| **G11** Supervisord unused | Apply with G1 out-of-session | `supervisorctl status` |

### P2 — Learning quality

| Gap | Best fit | Gate |
|-----|----------|------|
| **G12** Outcome coverage | route_id lifecycle + auto-close + log_outcome policy | coverage >80% |
| **G13** Silent pass | structured error log | zero silent swallows |
| **G14** Reflexion off | cheap model on wrong/partial only | cost flag |
| **G15** Model TS vs skills | **dual bandits** (not merge) | skill blend + cascade module |

### P3–P5 — Hygiene, product, eval

| Gap | Best fit |
|-----|----------|
| G16 three HTTP | Daemon ops + MCP agent primary |
| G17 import cycle | Invert deps; CI cycle=0 |
| G18 tool overload | Core MCP toolset + top-k only |
| G19 orphan routes | Document as ops |
| G20 lifespan | FastAPI lifespan |
| G21 root tests | Move under `tests/` |
| G22 autonomy | Wire `AutonomousToolCaller` into broker/autopilot |
| G23 multi-IDE | Defer until P0–P1 green; then 2-session test |
| G24 harness | Default nexus+memory |
| G25 KG | Prefer densified restore; GraphSAGE optional |
| G26 hallucination | **Active-set intersection** (not only registry) |
| G27 plans empty | Smoke after restore |
| G28 coach | Soft notes only |
| G29–G32 tests/CI | Fixtures + relative gates + `brain_health_gate` |

Full candidate matrices and paper IDs: **`gap-paper-map.yaml`**.

---

## 6. Critical path (execution)

```
G1 restore brain
  → G2/G3 schema+embeddings
  → G4/G11/G6 supervisord fleet
  → G13 fail-loud
  → G7 canary unified 1→2
  → G12 feedback coverage
  → G9 cold-start KPI
  → G26 active-set gate + G22 autonomy
  → G8/G16/G17 consolidate
  → G32 CI brain gate
```

**Do not** optimize blend weights or GraphSAGE until G1–G3 are green. Empty registry makes ranking research moot.

---

## 7. Consequences

### Positive

- Single research spine; no competing “best practice” claims.  
- Reuses code already on `second-brain-v2` instead of third router.  
- Clear out-of-session ops for high blast-radius restore/supervisord.  
- Dual bandit design avoids conflating skills with models.

### Negative / trade-offs

- Restore is high blast radius (must stop daemons).  
- Canary delays full v5 default by one measurement cycle.  
- Reflexion costs tokens if enabled.  
- Deprecating HTTP surfaces may break undocumented clients (document first).

### Invariants (must not violate)

1. RRF **k=60** unless A/B shows otherwise.  
2. Blend weights **0.40/0.15/0.25/0.20** unless locked A/B.  
3. Never invent outcomes without a scorer.  
4. Never present full tool catalog as default context.  
5. `schema.sql` must match live learning columns after restore.

---

## 8. Verification gates

| Gate | Pass criteria |
|------|----------------|
| Brain | `resources ≥ 2000`, EFC columns present, embeddings load |
| Health | `/health` + `/stats` coherent; not “healthy” with 1 skill only |
| Route A/B | `route_ab_compare` top-1 agreement ≥95% before flag=2 |
| Feedback | `outcome_log / routing_log ≥ 0.8` over rolling window |
| Gate | 100% reject of out-of-active-set tool calls in unit tests |
| Cycles | GitNexus import cycle count = 0 (target after G17) |
| Unit | `pytest tests/` green on fixtures |

---

## 9. References (canonical)

1. Sadani & Kumar — Tool Attention — arXiv:2604.21816  
2. Fei et al. — MCP-Zero — arXiv:2506.01056  
3. Du et al. — AnyTool — arXiv:2402.04253  
4. Cormack et al. — RRF — SIGIR 2009  
5. Zhang et al. — EFC / harness feedback compute — arXiv:2605.29682 (as cited in `efc.py`)  
6. Russo et al. — Thompson Sampling tutorial — arXiv:1707.02038  
7. Shinn et al. — Reflexion — arXiv:2303.11366  
8. Liu et al. — Lost in the Middle — arXiv:2307.03172  
9. Hamilton et al. — GraphSAGE — arXiv:1706.02216  
10. Chen et al. — FrugalGPT — arXiv:2305.05176  
11. Ong et al. — RouteLLM — arXiv:2406.18665  
12. Tool hallucination reliability — arXiv:2412.04141  
13. Data-centric reproducible ML — arXiv:2506.16051  
14. Agentic software architecture — arXiv:2602.10479  
15. Tool-use survey — arXiv:2603.22862  
16. SSRN: TS production / multicriteria / KG-TS — e.g. 4107976, 4502483, 4807690  

---

## 10. Related docs

- [`gap-paper-map.yaml`](./gap-paper-map.yaml) — gap → papers → algorithms → scores → best_fit  
- [`../second-brain-execution-plan.md`](../second-brain-execution-plan.md) — phase DAG  
- `server/efc.py`, `server/router_v2/fusion.py`, `server/autonomous_pipeline.py` — code SoT  

---

## 11. Approval note

This ADR does **not** perform restore or supervisord cutover (live MCP safety). Implementation of G1/G4/G11 must be run **out-of-session** with explicit operator approval.
