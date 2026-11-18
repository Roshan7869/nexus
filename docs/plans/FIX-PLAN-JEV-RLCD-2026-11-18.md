# Fix plan — remaining defects under the JEV + RLCD architecture

**Date:** 2026-11-18 · Aligns with `docs/plans/REVISED-PLAN-2026-11-11.md` §5 (which already
adopted RLCD as a *data shape only*) and supersedes the JEV phase in
`EXECUTION-PLAN-2026-11-18.md`.

---

## 0. The architecture in one picture

Two loops, deliberately asymmetric:

```
        FAST INNER LOOP (per request, cheap, constrained)
   ┌──────────────────────────────────────────────────────┐
   │  JEV / TypeSafe System One                           │
   │  typed single-pass classification:                   │
   │    cluster ∈ 8 · intent ∈ N · skill_fit ∈ {0,1}      │
   │    outcome ∈ {correct, wrong, partial}               │
   └───────────────┬──────────────────────────────────────┘
                   │ labels every decision it can
                   ▼
        SLOW OUTER LOOP (offline, no GPU, paired data)
   ┌──────────────────────────────────────────────────────┐
   │  RLCD — contrastive preference *data shape*          │
   │  pair = (task, preferred_skill, dispreferred_skill)  │
   │  source: reflection_fixes.alternative_resource_id    │
   │          + explicit-credit session outcomes          │
   │  → distilled into a pairwise ranker (numpy, CPU)     │
   └───────────────┬──────────────────────────────────────┘
                   │ corrected ranking
                   └────────────► back into JEV's inputs
```

**Why this split is the right one here:** JEV is a hosted single-pass classifier — perfect
for constrained per-request labels, useless for accumulating evidence. RLCD is the reverse —
slow, evidence-hungry, but it corrects systematic bias over time. The two bugs I measured are
one of each kind (see §2).

**What RLCD is *not* in this design.** Per the repo's own decision: no preference-model
training, no PPO, no `outcome_pairs` table. Those need ~40k examples per task and an
A100-class GPU; this host has no GPU. **RLCD contributes the pair schema and the distillation
target, nothing more.**

---

## 1. What blocks what

| Dependency | State | Consequence |
|---|---|---|
| TypeSafe API key | **absent** (`~/.config/jev/api_key`, `TYPESAFE_API_KEY` both unset) | JEV path cannot be called; needs the local fallback to ship first |
| ≥100 clean pairs | **0** `reflection_fixes` rows, 6 outcomes | RLCD distillation **must not start** (repo gate) — collection only |
| GPU | none | PPO / preference-model training permanently out of scope |
| A/B receipt | `run_ab_suite` exists, unused | No default weight change without it (AGENTS.md) |

**Rule I will hold to:** the JEV interface and the pair collector ship now; the *learner* stays
off until its gate is satisfied. Shipping a learner on 6 samples is how the current bias
happened.

---

## 2. Bug → fix mapping

### Bug A — cluster collapse *(a JEV-shaped problem)*

**Measured:** 5/5 tasks routed to `knowledge_wiki`, including "debug a failing login session"
and "write end-to-end tests". `knowledge_wiki` holds 13 resources vs 43 in `analyzer_planner`,
so the cluster filter starves good candidates before scoring.

**Fix — `server/cluster_decider.py` (new):**

```python
class ClusterDecider(Protocol):
    def decide(self, task: str, candidates: list[dict]) -> tuple[str, float]: ...

class LocalClusterDecider:      # ships now, no key needed
    # cosine(task_embedding, cluster_centroid) with a MARGIN rule:
    #   top1 - top2 < 0.04  -> return ("general", low_conf) instead of guessing
class TypeSafeClusterDecider:   # activates when TYPESAFE_API_KEY is set
    # typed question: {"cluster": {"type": "enum", "values": [...8...],
    #                              "instructions": "<task text>"}}
```

Wiring: `server/router.py::detect_cluster` calls the decider; `NEXUS_CLUSTER_DECIDER=local|jev`.
**The margin rule is the actual fix** — a low-confidence decision must widen the pool, not
silently narrow it to 13 resources.

### Bug B — top-1 skill absorbs all credit *(an RLCD-shaped problem)*

**Measured:** `gsd-doc-writer` ("Writes and updates project documentation") ranked #1 for
*write tests*, *debug a login session*, and *review a design document*; α rose 2.0 → 3.55
across those sessions. Because `report_session` credits rank #1 by default.

**Fix — three parts, in order:**

**B1 · Deterministic (ship immediately, no ML).** `server/execution_plane.py::report_session`
must refuse to guess:

```python
skill_name: str | None = None
if skill_name is None:
    # credit only when the session's own record identifies the skill actually used
    skill_name = session.used_skill or _single_candidate(session.skills)
    if skill_name is None:
        # ambiguous: record telemetry, credit nothing
        outcome_source = "derived"; skip belief update
```

I verified explicit crediting works: passing `skill_name='gsd-debug-session-manager'`
moved α on that skill, not the doc-writer.

**B2 · JEV as verifier (when keyed).** `skill_fit` typed question per candidate:

```json
{"state": "<task text>",
 "questions": {"fit": {"type": "bool",
   "instructions": "Is this resource the correct tool for this task? Resource: <name> — <description>"}}}
```

Result: credit goes to the skill JEV confirms, and `outcome` becomes a real verdict rather
than an assertion.

**B3 · RLCD pair accumulation (data shape).** Every session emits a pair when rank #1 ≠
credited skill:

```sql
-- reuses the existing table; no migration needed
INSERT INTO reflection_fixes(resource_id, alternative_resource_id, failure_summary,
                             fix_description, times_applied, times_successful)
VALUES (<dispreferred_rank1_id>, <credited_id>, <task[:200]>, 'explicit credit disagreed with rank');
```

Gate: `SELECT count(*) FROM reflection_fixes WHERE alternative_resource_id IS NOT NULL`
must reach **100** before any distillation runs.

### Bug C — learning from a mis-route *(integrity)*

Already partly handled in my session driver (report `wrong` when no routed skill matches).
Make it structural: when `route.confidence < threshold` **or** no candidate matches the
inferred intent, persist `outcome_source='derived'` and **do not move belief counters**.
`server/learning.py::_UNOBSERVED_LABELS` already gives the mechanism; it needs the routing
confidence passed through.

---

## 3. Implementation phases

| Phase | Work | Files | Gate to exit |
|---|---|---|---|
| **P0** | Credit attribution; no-guess rule | `execution_plane.py`, `learning.py` | re-run 6 sessions: belief moves land on the credited skill, not rank #1 |
| **P1** | Cluster decider + margin rule + JEV hook | `cluster_decider.py` (new), `router.py` | 5 mixed tasks → ≥4 distinct clusters, none starved below 20 candidates |
| **P2** | `DecisionClassifier` interface + `LocalHeuristicClassifier` | `decision_classifier.py` (new) | works with no key; `NEXUS_DECISION_CLASSIFIER=local` default |
| **P3** | `TypeSafeJevClassifier` behind the same interface | `decision_classifier.py` | ⛔ blocked — needs key; must not change defaults when absent |
| **P4** | RLCD pair collector (writes `reflection_fixes`) | `learning.py`, `execution_plane.py` | pairs accumulate; **distillation stays off** until ≥100 |
| **P5** | Pairwise ranker (numpy logistic on embeddings) + 6th L3 term | `server/router_v2/rlcd_ranker.py` (new), `fusion.py` | `NEXUS_V5_W_RLCD=0` default; `run_ab_suite` receipt required to raise it |

**Explicit non-goals:** PPO, preference-model training, new `outcome_pairs` table (the pair
data already fits `reflection_fixes`), any GPU dependency, any default weight change without
an A/B receipt.

---

## 4. How each fix is proven (not asserted)

| Claim | Evidence required |
|---|---|
| Cluster collapse fixed | 10 heterogeneous tasks → ≥5 distinct clusters; no cluster <20 candidates chosen |
| Credit attribution fixed | same task run twice with different credited skills → α moves differ accordingly |
| JEV absent ≠ degraded | full suite with `NEXUS_DECISION_CLASSIFIER=local` and no key: identical pass count |
| RLCD gate holds | attempt distillation at <100 pairs → **raises**, does not silently train |
| Ranker helps | `run_ab_suite` top-1 delta on `evals/golden/routing_cases.yaml` (105 cases), receipt archived |
| No fabrication | `task_complete` without explicit outcome → `unverified`, counters untouched |

---

## 5. Order of work and why

```
P0 (attribution)  →  P1 (cluster)  →  P2/P3 (JEV)  →  P4 (pairs)  →  P5 (ranker, gated)
   stops the          stops the         replaces        starts         only after
   wrong credit       starvation        heuristics      collecting     100 pairs + A/B
```

**P0 and P1 are deterministic and need neither JEV nor RLCD** — they remove the two defects
I measured and are safe to ship today. P2/P3 make the classifier swappable so JEV lands
without a rewrite. P4/P5 are the RLCD half and stay *dormant by design* until the data gate
opens.

---

## 6. Honest risk statement

- **The RLCD half cannot help for weeks.** Six sessions and zero `reflection_fixes` rows is
  nowhere near the 100-pair gate; anything claiming improved ranking before then is noise.
- **JEV is unverifiable until a key exists.** I can write the interface and the local
  fallback; I cannot validate that System One answers NEXUS's questions well.
- **The best available signal is explicit credit**, not JEV and not RLCD. P0 is therefore the
  single highest-value change in this document.
- Cluster count is a small-corpus artefact: with 127 resources, margin-based `general`
  fallbacks will look like "no decision". That is correct behaviour, not a regression.

---

## 7. Execution status (2026-11-18)

| Phase | Status | Evidence |
|---|---|---|
| **P0** credit attribution | ✅ **done** | `NEXUS_CREDIT_POLICY=strict` (default): ambiguous close → `learning: skipped / ambiguous_primary_skill`; explicit credit moves only the named skill (α 2.9→3.8). `NEXUS_CREDIT_POLICY=top1` restores old behaviour. |
| **P1** cluster decider | ✅ **done** | `server/cluster_decider.py`: member-centroid match + margin rule. Re-seeded centroids from members (were keyword means; top-1 cosine was 0.14–0.34 against a 0.35 floor, so **every** decision declined). Calibrated `ACCEPT=0.20`, `MARGIN=0.03` from measured data. |
| **P1b** starvation fix | ✅ **done** | When the decider declines, `detect_cluster` now returns **None → full active pool**, instead of falling back to keyword signals that collapsed onto one 13-resource cluster. Old path available via `NEXUS_CLUSTER_SIGNAL_FALLBACK=1`. |
| **P1c** observability | ✅ **done** | `cluster` (top-ranked skill's cluster) and `cluster_decision` (decided cluster + confidence + margin + reason) are now separate payload keys. They were conflated, which made the fix look like it wasn't working. |
| **P2** classifier interface | ✅ **done** | `server/decision_classifier.py`: `DecisionClassifier` protocol, `LocalHeuristicClassifier` (declines on weak signal), `get_classifier()`. |
| **P3** JEV backend | ✅ **done — dormant** | `TypeSafeJevClassifier` reads `TYPESAFE_API_KEY` or `~/.config/jev/api_key`, validates answers against the permitted set, declines (never guesses) on any failure. `auto` selects it only when a key exists. **Unverified against the live API — no key yet.** |
| **P4** RLCD pair collector | ✅ **done** | On explicit-credit disagreement, writes `reflection_fixes(resource_id=dispreferred, alternative_resource_id=preferred)`. Verified: `gsd-doc-writer → egm-codebase-details` recorded. No schema change. |
| **P5** ranker | ✅ **built, gated off** | `server/router_v2/rlcd_ranker.py`: numpy pairwise logistic (Bradley–Terry), `MIN_PAIRS=100`, `train()` **raises** `InsufficientPairs` below the gate, `active_weight()` returns 0.0. Verified refusal at 0 pairs. |

### Measured behaviour after P0–P4

```
deploy k8s canary      decision=devops_infra   conf=0.209   top_skill_cluster=knowledge_wiki
REST API schema        decision=backend_api    conf=0.236   top_skill_cluster=knowledge_wiki
postgres slow joins    decision=None           reason=below_accept:0.146
audit docs vs code     decision=general        reason=ambiguous_margin:0.030
```

**Decision quality is plausible; the reported top-skill cluster is still biased**
(`gsd-doc-writer`, cluster `knowledge_wiki`, dominates scoring). That bias is now *visible*
rather than hidden, and it is addressed by P4/P5 once ≥100 pairs accumulate — not by more
threshold tuning.

### Tests

`tests/test_decision_layer.py` — **18 tests, all passing**: margin rule, accept/decline paths,
no-centroid and empty-task behaviour, calibration guard (fails if the floor is re-tightened),
local classifier accept/decline, JEV absence, answer-shape parsing, RLCD gate refusal,
training above the gate, `active_weight()==0`.

Regression: 92 routing/learning tests + 39 focused tests still pass.

### Still blocked

- **JEV live validation** — needs a TypeSafe key. Interface ready; behaviour unproven.
- **RLCD distillation** — needs ≥100 pairs. 1 pair collected so far. This is the intended
  state, not a failure.
- **P5 fusion integration** — `NEXUS_V5_W_RLCD` must stay 0.0 until `run_ab_suite` produces a
  passing receipt on `evals/golden/routing_cases.yaml`.
