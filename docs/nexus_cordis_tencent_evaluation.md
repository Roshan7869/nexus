# NEXUS Architecture Evaluation: Cordis (arXiv:2608.25512) vs Tencent Research Family

**Date:** 2026-09-13
**Goal (user):** How to apply to NEXUS repo
**Sources:**
- Cordis paper: https://arxiv.org/abs/2608.25512 — *A Programming Paradigm for Spatiotemporal Composability* (Shi, Zhang, Cui — Peking Univ + DeepSeek-AI, 26 Aug 2026, 92pp)
- Companion: https://github.com/cordiverse/paper + https://github.com/deepseek-ai/deepseek-harness/blob/.../docs/cordis-tutorial/02-lifecycle-and-effects.md
- Tencent family (via Exa + GitHub):
  - Hunyuan-Large (arXiv:2411.02265) — 389B total / 52B active MoE, 256K ctx, 7T tokens — https://arxiv.org/pdf/2411.02265
  - Hunyuan-A13B — 80B total / 13B active fine-grained MoE — https://github.com/Tencent-Hunyuan/Hunyuan-A13B
  - Hunyuan3D 2.5 (arXiv:2506.16504, 2025-06-19) — LATTICE 10B shape + PBR texture — https://doi.org/10.48550/arxiv.2506.16504
  - Hunyuan-7B / Hunyuan-Turbo / GradLoc RLVR repo — https://github.com/Tencent-Hunyuan/
- NEXUS local: `server/router.py` (v4), `server/router_v2/v5.py` (production, USE_UNIFIED_ROUTER=2), `core/config.py` (LearningConfig), `nexus.yaml`, `server/context_harness/`, `core/learner.py`

> Note: user asked for "all research paper released by Tencent" — unbounded. This report scopes to the architecturally relevant open subset (Hunyuan LLM + 3D + training/infra) and maps each to NEXUS.

---

## 1. Cordis in one page

**Problem:** plugin systems → self-evolving agent harnesses need *dynamic composition*, no formal foundation.

**Two orthogonal dimensions:**
- **Temporal composability:** completely revert a component's side-effects on removal. Mechanism: *revertible effects* — every context transformation carries an inverse the runtime holds. Local to one component.
- **Spatial composability:** declare + reactively manage inter-component deps. Mechanism: *reactive coeffects* — every context change classified against component's coeffect spec → drives activation/deactivation. Local to one component.

**Unification — context paradigm:** effect ctx + coeffect ctx unified into single context type; all effects/coeffects mediated through it → observational equivalence where distinct components interleave without disturbing each other. Calculus lifts single-component guarantees to whole interleaved system (metatheory).

**Implementation — Cordis meta-framework:**
- core lib: effect tracking (`ctx.effect(() => disposer)`), coeffect resolution
- declarative component loader: config reconciliation + hot module replacement (HMR)
- lifecycle: unload via config edit / hot reload / explicit dispose / loss of required service undoes all Cordis-API registrations automatically; outside resources must be wrapped.

Example (from DeepSeek-Harness tutorial):
```ts
export function apply(ctx: Context) {
  const fiber = ctx.plugin(heartbeat)
  ctx.effect(() => {
    const timer = setTimeout(async () => { await fiber.dispose() }, 700)
    return () => clearTimeout(timer)
  })
}
```

**Why it matters for NEXUS:** NEXUS today is a *router + learning* system with ad-hoc lifecycle (supervisord, systemd, MCP stdio per-session, `task_start`/`task_complete` ephemeral tool sessions). No revertible-effect discipline, no coeffect specs, no HMR. Exactly the gap Cordis formalizes.

---

## 2. Tencent family in one page

### 2.1 Hunyuan-Large (2411.02265) — the flagship MoE lesson
- 389B total / 52B active, 64L, 80 Q-heads / 8 KV-heads, hidden 6400, vocab 128K, SwiGLU + RoPE, 7T trained tokens, 256K long-ctx.
- **KV-cache compression:** GQA (8 groups) + CLA (share every 2 layers) → ~95% saving vs MHA. Directly relevant to NEXUS long-context routing + L0-L3 cache (`server/context_harness/cache.py`).
- **Routing:** 1 shared expert (common knowledge) + 16 specialized (top-1) — mixed routing + recycle routing for load balance. Maps to NEXUS L1 intent → L2 topic → L3 rerank: NEXUS uses fixed 0.45/0.25/0.20/0.10 blend; Hunyuan shows learned + shared-expert pattern wins.
- **Training:** large-scale synthetic data (orders larger than prior), expert-specific LR, MoE scaling-law study.

### 2.2 Hunyuan-TurboS (2505.15431, May 2025) — adaptive reasoning
- 560B total / 56B active hybrid: 128 layers (57×Mamba2 + 7×Attention + 64×MoE FFN, AMF/MF pattern); linear-complexity Mamba2 + GQA; 16T tokens, 256K ctx; AngelHCF inference 1.8× speedup; Arena 1356 top-7 — https://arxiv.org/abs/2505.15431
- **Adaptive long-short CoT fusion:** auto "no-thinking" vs "thinking" mode. Exact policy NEXUS router needs: cheap fast-path for trivial tasks, deep mode only for hard debugging/planning. Maps to v5 L1 short-circuit (conf>θ → return) — TurboS shows how to learn the threshold.

### 2.3 Hunyuan-A13B (2025-06-27) — efficiency point
- 80B total / 13B active fine-grained MoE, FP8 + GPTQ-Int4 variants, pretrain/instruct/manual, 256K native ctx, dual fast/slow-thinking, agent-optimized (BFCL-v3, τ-Bench, C3-Bench). Pitch: SOTA perf at minimal compute for resource-constrained envs. Lesson for NEXUS: hot/cold tier split (ToolkenGPT heritage in v5) should follow fine-grained expert logic — small active path for fast tier, large total for coverage.
- Repos: `Tencent-Hunyuan/Hunyuan-A13B` (report: https://github.com/Tencent-Hunyuan/Hunyuan-A13B/blob/main/report/Hunyuan_A13B_Technical_Report.pdf), `Tencent-Hunyuan-Large` (code: https://github.com/Tencent/Tencent-Hunyuan-Large), `Hunyuan-7B`, `GradLoc` (token-level gradient diagnosis for RLVR stabilization).

### 2.4 HunyuanVideo (2412.03603, Dec 2024) + Hunyuan-DiT (2405.08748)
- Video: 13B DiT largest open video at release, Causal 3D VAE (4× temporal, 8× spatial), flow-matching DiT-T2X scaling laws (5× compute saving), progressive low→high-res training; beat Runway Gen-3/Luma 1.6 — https://arxiv.org/abs/2412.03603, code https://github.com/Tencent/HunyuanVideo
- DiT: cross-attention text fusion (not AdaLN), bilingual CLIP + multilingual T5, MLLM-refined captions, "data convoy" pipeline, multi-turn edit — https://arxiv.org/abs/2405.08748, code https://github.com/Tencent/HunyuanDiT
- NEXUS takeaway: scaling-law-first methodology (fit Nopt/Dopt before committing compute) for sizing router tiers + FAISS budgets; dual-encoder + caption-refine loop as model for multi-signal routing (keyword + semantic + LLM judge).

### 2.5 Hunyuan3D 2.0 (2501.12202) → 2.5 (2506.16504) — two-stage discipline
- 2.0: Hunyuan3D-DiT shape diffusion (ShapeVAE importance sampling + variational token length) + Hunyuan3D-Paint mesh-conditioned multi-view texture; beats Trellis on 300-case study — https://arxiv.org/abs/2501.12202, code https://github.com/Tencent/Hunyuan3D-2
- 2.5: LATTICE 10B shape foundation + PBR multi-view texture, guidance + step distillation — https://doi.org/10.48550/arxiv.2506.16504
- Analog for NEXUS: keep L1/L2/L3 stage-pure, distill L1 embedder for p95 <300ms (v5 target); plan-skeleton-first, fill-details-second for agent pipelines.

### 2.6 Hunyuan-MT Chimera (2509.05209, Sep 2025) — weak-to-strong fusion
- 7B multilingual (33 langs) + Chimera: base emits diverse candidates under varied decoding, trained fusion synthesizes into one strong output (beats CoT slow-thinking); SFT→RL→weak-to-strong RL; WMT2025 1st in 30/31 — https://arxiv.org/html/2509.05209v1, code https://github.com/Tencent-Hunyuan/Hunyuan-MT
- Direct rerank pattern for NEXUS L3: sample K cheap skill candidates, fuse with learned synthesizer instead of single-path argmax.

### 2.7 What "all Tencent papers" means in practice
Full Tencent output is hundreds of papers/year. Architecturally transferable core = MoE routing + KV compression + synthetic-data scaling + stage-distillation + open-weight ops (HunyuanAPI, ModelScope, HuggingFace). This report evaluates those; domain papers (video/3D) noted but not ported.

---

## 3. NEXUS current architecture (ground truth)

- **Entry:** `server/unified.py` (:8000), `server/nexus_http_daemon.py` (:8080, systemd), `server/nexus-mcp-enhanced.py` (stdio, IDE-owned), dashboard :8765 (supervisord), context-harness in-process bridge.
- **Routing:**
  - v4 `server/router.py`: cluster → ISO (tool_attention_v4) → preconditions → hallucination gate → Thompson (`core/learner.py`) → keyword fallback. Papers cited: ToolkenGPT, Reflexion, SWE-agent.
  - v5 `server/router_v2/v5.py`: HGMF intent→cluster→retrieve→rerank refiner; L1 short-circuit (conf>θ + pool≥N); L2 constrained to top-2 clusters; L3 `0.45·topic+0.25·intent+0.20·skill_match+0.10·listwise`; L4 autocut 0.5·max. Reflexion consultation (downweight failed *0.7, upweight fixes +0.1·n). Read-only vs MCP/Thompson. Targets: agreement >60%, p95 <300ms warm.
- **Learning:** Thompson Sampling beliefs (`ModelBelief`, `NexusLearner`), `outcome_log`/`routing_log`, `reflection_failures/fixes`, cluster affinity, `report_outcome` mandatory. Flags via `core/config.py: LearningConfig` (YAML > env > default).
- **Context:** `server/context_harness/` L0-L3 cache, `BackendProtocol`/`BackendRegistry`, `ContentRouter`, classifier/scorer, workflow graph.
- **Resilience:** `server/resilience/circuit_breaker.py` (CircuitBreaker, Bulkhead, Redis backend).

**Strengths:** principled staged routing, explicit learning loop, split ownership (no single parent), feature-flag discipline.
**Gaps vs Cordis/Tencent lens:** (1) no revertible effects — tool sessions leak, (2) no coeffect specs — preconditions are boolean gates not reactive subscriptions, (3) no HMR/config reconciliation — restart MCP/daemon to pick up skills, (4) fixed fusion weights — no shared-expert / learned routing, (5) KV/cache not compressed — L0-L3 naive TTL-LRU, (6) no synthetic-data flywheel for router eval.

---

## 4. Comparative matrix

| Dim | Cordis (2608.25512) | Tencent Hunyuan | NEXUS today | Gap |
|---|---|---|---|---|
| Composition | spatiotemporal, formally proven | MoE expert composition (shared+routed) | staged pipeline, no proof | adopt effect/coeffect discipline |
| Lifecycle | revertible effects + dispose | checkpoint + FP8/GPTQ ops | task_start/complete, manual | missing inverse tracking |
| Deps | reactive coeffects (activate/deactivate on ctx change) | expert load-balance, recycle routing | StateGates boolean | upgrade to subscriptions |
| Loading | declarative loader + reconcile + HMR | ModelScope/HF + API | restart required | add HMR |
| Retrieval | — | GQA+CLA 95% KV save, 256K ctx | L0-L3 TTL-LRU, FAISS | compress + distill |
| Routing math | observational equivalence (non-interference) | learned router + shared expert | fixed 0.45/0.25/0.20/0.10 | learn weights, add shared prior |
| Eval | calculus metatheory | benchmarks + scaling laws + synthetic data | agreement>60%, p95<300ms, ci_eval.sh | add synthetic eval gen |

---

## 5. How to apply to NEXUS — concrete proposals

Ordered by ROI / risk. Each maps to files.

### P0 — Cordis-style effect wrapper for tool sessions (temporal)
**File:** `server/task_session.py` (new) wrapping `task_start`/`task_complete`.
- Introduce `ctx.effect(fn → disposer)` analog: every resource acquired in a session (DB conn, FAISS handle, MCP tool load, cache fill) registers inverse.
- On `task_complete` / failure / timeout, run disposers LIFO. Prevents leak that today requires manual `reset`/`clear`.
- Acceptance: chaos test — 100 sessions with injected failures → zero open handles (mirror Cordis "unload reverts all").

### P1 — Coeffect specs replace boolean preconditions (spatial)
**Files:** `server/precondition.py` (`StateGates`, `StateVariable`), `server/tool_attention_v4.py`.
- Extend `StateVariable` with `subscribe: list[ContextPattern]`; components declare required services (DB, FAISS, embedder). On ctx change, auto-activate/deactivate instead of just filtering at route time.
- This is direct port of reactive coeffects: classification of ctx change vs spec drives lifecycle.
- Acceptance: kill FAISS → dependent skills auto-deactivate + re-activate on rebuild, no restart.

### P2 — Declarative loader + HMR for skills/resources
**Files:** `server/db.py`, `scripts/rebuild_faiss.py`, MCP sync path.
- Loader reads `resources` + YAML, reconciles diff (add/update/remove), hot-swaps without daemon restart. Borrow Cordis config-reconciliation algorithm (three-way: desired vs running vs last-applied).
- Acceptance: `INSERT` new skill → routable in <2s with no restart; `DELETE` → reverted via P0.

### P3 — Shared-expert prior + adaptive CoT + Chimera fusion in v5 (Tencent routing lesson)
**Files:** `server/router_v2/fusion.py` (`v5_get_weights`, `v5_fusion_scores`), `server/router_v2/v5.py`.
- Add 5th term: `w_shared · shared_prior` where shared_prior = global skill prior (like Hunyuan shared expert for common knowledge). Start `w=[0.40,0.22,0.18,0.10,0.10]`, tune via `NEXUS_V5_W_*` overrides + A/B (`route_ab_compare`, 20-query suite).
- Add recycle-routing analog: cap per-cluster load, spill to second cluster (already top-2 constrained — enforce capacity factor). Source: Hunyuan-Large recycle routing.
- Adaptive depth (TurboS 2505.15431): learn L1 short-circuit threshold as long-short CoT policy — fast-path no-think for high-conf trivial tasks, full L2+L3 think only when needed, instead of fixed θ.
- Chimera weak-to-strong (2509.05209): sample K cheap L2 candidates under varied decoding/weights, fuse with learned synthesizer for L3 instead of single argmax.
- Acceptance: cross-router agreement +3-5pp, no p95 regression.

### P4 — KV/cache compression (GQA+CLA analog for context harness)
**Files:** `server/context_harness/cache.py` (L0-L3), `server/iso_embedder.py`, `server/numpy_router.py`.
- GQA analog: share embeddings across head-groups (quantize L1/L2 entries to int8, share across clusters); CLA analog: share L2 cache lines across adjacent layers (L1↔L2 dedup).
- Target: -80% cache RAM (Hunyuan achieves -95% KV), p95 warm -20%.
- Distill L1 embedder (Hunyuan3D distillation lesson): small fast model for L1 short-circuit, large for L2/L3.

### P5 — Synthetic-data flywheel for router eval (Hunyuan data lesson)
**Files:** `scripts/benchmark_nexus.py`, `scripts/run_ops_eval.py`, `tests/`.
- Generate synthetic tasks orders larger than current hand-written set (Hunyuan-Large: synthetic >> prior lit). Use existing `route_ab_compare` as judge. Feed wins back to Thompson (`report_outcome`) + cluster affinity.
- Acceptance: eval set 10×, agreement gate stable, beliefs converge faster.

### P6 — Unified context type (context paradigm)
**Files:** `core/config.py`, `server/precondition.py`, `server/context_harness/content_router.py`.
- Unify effect ctx (what session mutates) + coeffect ctx (what session requires) into single `NexusContext` dataclass mediated for every tool call. This yields Cordis-style non-interference: interleaved tool sessions don't disturb each other (today Bulkhead/CircuitBreaker approximate this ad-hoc).
- Long-term; start as type + mediation function, prove equivalence on 2-component case.

**Non-goals:** porting Hunyuan3D/video weights; replacing Thompson with pure learned router (keep Thompson as L4, add learned prior as in P3).

---

## 6. Risks / limits
- Cordis paper is 3 weeks old (2026-08-26), 92pp, single version — metatheory not yet replicated; adopt mechanisms (P0-P2), not proof claims.
- "All Tencent papers" unbounded — this eval covers open Hunyuan core; closed trillion-param model (mentioned in Hunyuan-Large report, training from Nov 2023) not evaluable.
- NEXUS harness research backends returned 0 artifacts for these queries (orchestrator + synthesis empty 2026-09-14) — findings rest on Exa + GitHub + local code; re-run after index rebuild.
- HMR + auto-dispose risk: must warn on HIGH/CRITICAL blast radius per GitNexus policy — run `impact` before editing `router.py`, `v5.py`, `precondition.py`.

## 7. Next steps
1. `impact({target:"route_task"})` + `impact({target:"unified_route_v5"})` → confirm P0-P3 blast radius.
2. Prototype P0 in branch, chaos-test sessions.
3. A/B P3 weights via `router_ab_suite` (20-query).
4. Profile L0-L3 RAM → spec P4 compression.
5. Schedule: P0+P1 (1 wk), P2 (1 wk), P3 (3 d), P4 spike (3 d), P5 ongoing.

---

## Appendix A — Cordis formalism deep dive (from subagent PDF extraction, pp.9-71)

**Biblio:** v1 2026-08-26, 92pp 1 fig 2 tables, cs.PL/cs.SE D.2.11/D.3.1/D.3.3, DOI 10.48550/arXiv.2608.25512. Impl: github.com/cordisjs/cordis (active, unstable), paper repo github.com/cordiverse/paper, case study Koishi chatbot (4000+ plugins, Cordis v3 in prod; paper describes v4).

**Temporal (Sec 3.1):** effect Γ→Γ×(Γ→Γ) + inverse; ∂Γ=Γ×(Γ→Γ) with (γ,φ), φ composite inverses; track(f,g), Thm4 forward-preservation, Thm5 monoid hom from twisted 𝔗Γ, recoverΓ (γ,φ)→(φ(γ),id) Thm7 invariant φ(γ)=γ0; 𝔈Γ + witness 𝔈*Γ, ⋄ composition Thm10/11; effectΓ lifts to ∂²Γ, undo inverse = re-do, Thm13-15; iterators ℑΓ=μℑ.Γ→Γ×(Γ→Γ)×Maybe(ℑ) fold LIFO, loading=run iterator, unloading=apply φ reverse (Thm16).

**Spatial (Sec 3.2):** Σ=(k:K)⇀V_k, get/set (set:𝔈*_Σ so auto-tracked); spec 𝔇Σ=Set(K), σ⊧d iff ∀k∈d bound, notify trichotomy activating/deactivating/neutral; isolation Σ_iso realms ρ+store σ, isolate derived; interception Σ_inter provider fn + metadata monoid (context wins ⊕) for policy without touching component.

**Context paradigm (Sec 3.3-3.4):** Γ∞=μΓ.Γ×(Γ→Γ)×Σ self-similar; coeffect=(V_k,A_k) ops lift a_Σ key-confined; mediated iterators ℑ^A_Σ(S,P); observational ≃ via test-words (forward+inverses agree), ≃_k coarsest respected (Lemma32), ≃_S same-keys-related, Lemma38/39 lift equalities; independence = commutativity of transformation monoids 𝔐(i) → per-coeffect check.

**Calculus (Sec 4):** component ℭ=(d,p,e:ℑ^{d∪p}); fiber ⟨d,p,e,π,σ,τ,θ⟩ θ∈Inactive|Reloading|Active|Unloading + ω committed view, π parent tree (fresh atoms), σ own table dom⊆p, disjoint provisions (O-Insert admits one provider/key); registry F_γ, σ_γ=⋃ Active; rules O-Insert/Retire/Remove, L-Begin/Iter/Finish/Divert/Leave/Unload + confinement, inertia+failure ext; Preservation, Temporal Cor69 (departing contributes nothing), Spatial Thm70 (dependents down before provider withdraws, bindings stable during activation) + Thm71 staleness, Progress, Confluence/Quiescence Thm73 terminates, Thm80 quiescent = f(final config) order-independent; revision=retire+deactivate+remove+reinsert.

**Impl (Sec 5, Table2):** `ctx.effect(callback)` sole primitive (𝔈 or ℑ, armed at-most-once + parent compose Alg1 LIFO); coeffects via @@store/@@isolate/@@intercept slots, set=effect+notify Alg3 realm-aware refresh idempotent, ACTIVE-only availability; `ctx.use(comp,config)` fiber Alg4 + inertial machine Alg5 (provider-uid targets, fresh never-reused uid, LOADING/UNLOADING tasks, L-Leave out-of-service first, await dependents INACTIVE before own inverses, dual staleness guards); access via Proxy resolve up fiber chain (INACTIVE_ACCESS/UNDECLARED_ACCESS); loader entries {id,url,isolate,intercept,config,disabled} keyed reconcile, @cordisjs/group keyed diff + @cordisjs/include graft, per-field least-disruptive (id/url rebuild, isolate realm-move Alg7 δ-tags, intercept in-place, config diff, disabled unload/reload=fresh fiber), Thm80+73+Cor69+70 → concurrent fetch no load order; HMR Alg8-10 classify accept/decline, stale-entry fold, transactional invalidate+backup+swap with rollback; Koishi validation (console-disable reverts, HMR preserves caches, provider-switch reactivates only changed dependents). Avoid VSCode pitfalls: 87/100 top extensions need restart on removal, 7/100 declare deps.

**Harness patterns (a–o):** single mediated ctx; tracked-effect sole mutation path LIFO; provision-as-effect; declare-then-react never read absent; provider-uid not value equality; provider out-of-service before inverses + await dependents + hold commit view + read-view-not-store; realms local/global + move protocol; interception monoids for perms/quotas; inertial async + per-iteration guard; declarative loader quiescence-as-function; transactional HMR; broker for rolling updates; test-words equivalence; inside/outside boundary (acquire-record vs emission withhold/compensate).

---
*Teams: using agent-reach, platform Exa via backend mcporter + GitHub via gh; NEXUS harness orchestrator empty — fell back to primary sources per research skill.*
