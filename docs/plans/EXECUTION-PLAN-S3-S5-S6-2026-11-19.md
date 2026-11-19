# Execution Plan — S3 / S5 / S6 (constraints-loaded loop)

**Date:** 2026-11-19 · **Stages remaining:** S3 (competition budget), S5 (channels +
goal restatement), S6 (watchdog, shadow) · **Parent design:**
`docs/designs/DISTRACTION-RESISTANT-CONTEXT.md` (goldfish-reviewed v2)

## 0. Constraints (loaded into every loop iteration)

| # | Constraint | Enforcement |
|---|---|---|
| C1 | CPU-only, ≤5 ms added pack cost per stage | string/SQL ops only; measured per stage |
| C2 | **Parity gate:** 20-task golden set top-1/top-3/cluster/task_type 20/20 — any change that moves them REVERTS (ranking changes need a separate accepted proposal) | compare_rankings on an isolated copy after each stage |
| C3 | Fail-open: a failed stage = pre-change behavior + logged warning | try/except at every seam |
| C4 | All latency/token A/B on an isolated `sqlite3 backup` copy; nothing else may touch it during measurement | learned from the WAL-poisoned parity run |
| C5 | Suspicion never hides a resource (only confirmed evidence pack-blocks) | flag-type-aware filters, already shipped in S2 |
| C6 | No auto-delete; curation is trace-verify-delete only | flags carry curation notes |
| C7 | LLM-judged acceptances stay PENDING without a judge key; deterministic halves measured now; **no fabricated numbers** | conflict_probes / scope-guard arms |
| C8 | Negative results are committed with receipts, defaulted off (E5 pattern) | audit table records keep/revert either way |
| C9 | Explicit-file staging only (tree shared with sibling session) | git add <paths> |
| C10 | S6 writes telemetry observations ONLY — zero writes to outcome/belief tables | table-diff audit |

## 1. The loop

```
for stage in (S3, S5, S6):
    PLAN      -> mechanism + PRE-DECLARED acceptance gates (below; never
                invented after the fact)
    IMPLEMENT -> minimal seam-touching code
    VERIFY    -> unit tests + parity (C2) + structural presence checks
    AUDIT     -> measured deltas vs the pre-declared gates, on isolated
                 copies (C4): pack tokens, pack latency, detection stats
    DECIDE    -> keep (commit with numbers) or revert (commit with receipts)
```

## 2. Stage plans with pre-declared gates

### S3 — competition budget

**Mechanism.** Cap packed statements. Routed skills: **3 base slots**; **+1 slot
when the cluster decision widened the pool** (`cluster_decision` with >1
candidate cluster or `fallback=True`); **+1 more (max 5)** when the decision is
absent/low-confidence (<0.5). Broker block: resource lines already 3; learning
notes excerpted to **one line (≤120 chars)** instead of 300; hard cap **12
content statements** per block. Harness: unchanged (fusion pipeline, not a
direct prompt — scope note recorded).

*Constraint-loaded adaptation (honest):* the design's slot rule was "+1 per
additional detected L1 intent"; v5 does not expose an intent *count* — the
widened-cluster decision is the router's own multi-facet signal, already
computed, deterministic, and free. Same intent (multi-facet ⇒ more slots),
different (documented) signal source.

**Gates (pre-declared):**
- G-S3.1 packed tokens (chars/4 proxy, stated as such) **≥25% fewer** vs
  top-5/full-notes arm on 100 replayed queries; 95% CI excluding 0
- G-S3.2 parity identical (C2)
- G-S3.3 statement cap 12 enforced programmatically (0 blocks exceed)
- G-S3.4 pack-construction latency delta **≤ +2 ms** p50
- G-S3.5 multi-facet: constructed two-facet queries get ≥4 slots on ≥80%
- LLM-side "≥25% fewer steps in execution": PENDING a judge/execution key (C7);
  the deterministic token proxy is measured now

### S5 — channel tags + goal restatement

**Mechanism.** Broker context block restructured: content wrapped in
`<retrieved_context>`; caller goal restated in `<user_goal>` **AFTER all
content** (recency advantage to the goal, not the distractor — OEDD); exactly
ONE scope-guard paragraph in `<scope_guard>` (the Fable 5.1 vendor-measured
adaptation); **no other standing prose** (ETH 2602.11988: unearned context
costs >20% and lowers success). `find_skills` result gains a trailing
`user_goal` field after the skills list.

**Gates (pre-declared):**
- G-S5.1 100% of constructed blocks: channel structure present; goal-last
  ordering present; exactly ONE scope paragraph
- G-S5.2 standing-prose audit: zero unmeasured instruction lines beyond the
  scope paragraph (line-counted)
- G-S5.3 tag overhead ≤ 60 tokens/block (chars/4), measured
- G-S5.4 parity identical (C2)
- G-S5.5 scope-guard behavioral A/B (unrequested-expansion −50%): PENDING
  judge key (C7)

### S6 — watchdog, shadow mode

**Mechanism.** Batch telemetry auditor over `routing_log` (3,469 real rows of
baseline): robust per-intent-group baselines (median + MAD) of `latency_ms`,
`tokens_used`, `injected_ctx_len`; a route is anomalous when **≥2 of 3 features
exceed |z| > 3**. Detour signature is Google's measured one: wait ×~2.05,
tokens ×~1.77. Writes ONLY to `watchdog_events` (C10). Batch script +
shadow call site; never on the hot path.

**Gates (pre-declared):**
- G-S6.1 zero writes outside `watchdog_events` (table-diff audit, C10)
- G-S6.2 constructed-detour detection reported with Wilson 95% CI at n=100
  (healthy=300 real rows); NO pass/fail gate — report-only (design: belief
  writes need a separate design proving attribution/FP)
- G-S6.3 FPR on healthy rows reported with CI
- G-S6.4 batch overhead measured (ms per 100 sessions) — not on any hot path

## 3. Rollback triggers (any stage)

Parity moves ⇒ revert. Pack latency +>5 ms ⇒ revert. Any non-shadow write to
outcome/belief tables in S6 ⇒ revert. All reverts committed with the failing
numbers (C8).

## 4. Execution deltas ledger (filled during execution)

| Stage | Δ packed tokens | Δ pack latency | Parity | Extra |
|---|---|---|---|---|
| S3 | **vacuous-negative: ~0%** — v5 autocut already packs ~3.4 skills (old==new at slots {3:20}); the −23–38% evidence targeted systems that overpack. Guardrail ships anyway. Notes seam also vacuous (no project-matching learnings on golden queries). | string slicing only; block p50 dominated by pre-existing LIKE search | 96.0% / 20/20 / 20/20 (fresh copy) | multi-facet slots WITHDRAWN with data: decider carries no signal (conf p75=0.34, margin p75=0.09, alternatives constant 7) |
| S5 | +72 tok/block structure (16 tags + 56 scope-guard; pre-declared ≤60 — **missed by 12**, rationale: the paragraph is the one vendor-measured text; trimming it to satisfy an invented token gate optimizes the wrong metric) | ~0 (string ops) | included above | structure 40/40 blocks: channels + goal-last + exactly one scope paragraph |
| S6 | n/a (batch) | 36 ms per 3,479-row scan; 41 ms per full eval — off any hot path | n/a | **live eval UNMEASURABLE: telemetry was 6/3,469 latency rows, 0 tokens/ctx/intent** → instrumentation wired (route_latency_ms + intent on every logged route, probe-verified 6→11); synthetic unit eval 50/50 detection, FPR 0.0; live eval deferred until ≥100 real rows accrue |

**Decisions per the loop:** S3 kept as guardrail + negative recorded; S5 kept
(72-token overhead accepted with rationale); S6 kept as shadow + the
instrumentation gap fixed at the write path. Parity verified on a fresh copy
after the audit copy's own routing_log growth shifted the UCB term (94% on the
used copy — data drift, not code; fresh copy 96.0%).

LLM-judged acceptances still PENDING a judge key (C7): conflict-probe A/B (S4),
scope-guard behavioral A/B (S5).
