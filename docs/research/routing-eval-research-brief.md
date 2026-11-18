# Routing remediation — research brief

Date: 2026-11-12
Status: research only. No code changed. Nothing here is approved for implementation.
Subject: `docs/plans/remediation/A1-single-router.md` and its review
`docs/plans/remediation/routing_goldfish_review_2026-11-12.md`.
Repo pinned: `/home/roshan/nexus`, HEAD `302fb92`, remote `Roshan7869/nexus`.

This brief answers one question: is there research or an existing repository that
solves the four blockers, and which one should be adopted. It deliberately does not
restate the blockers at length — the review already does that. It attacks the
assumptions behind them.

---

## 1. The four blockers, as recorded

1. The proposed equality test is vacuous — both sides can be empty and compare equal.
   `A1-single-router.md:80` reads `result["results"]`; `route_for_task` returns `skills`
   (`server/router_cutover.py:147`).
2. A production endpoint bypasses the cutover router — `/find_skills` in
   `server/nexus_http_daemon.py:633-675` goes through `router.route_task` and logs
   separately. A1 only changes `/nexus/search`.
3. The quality gate is unresolved — `gate_pass: false` at 15% against a 95% target;
   a separate 105-case run reports 33/105.
4. The `/nexus/search` response contract is undecided — `{query, method, results}` vs
   the route shape `{task, skills, ...}`, plus an unintended session write.

Blockers 1 and 2 are enforcement problems. Blockers 3 and 4 are measurement problems.
The research literature is mature on all four; none of this requires invention.

---

## 2. Diagnosis: the 95% gate is not a quality gate

Read `evals/results/ab_benchmark_receipt.json`. The metric is `top1_agreement` — string
equality between v4's top-1 skill and v5's top-1 skill. Three of the twenty recorded
queries:

| query | v4 top-1 | v5 top-1 | counted as |
|---|---|---|---|
| write PostgreSQL query for user analytics | `article-writing` | `clickhouse-io` | disagreement |
| build a React component for data table | `frontend-ui-engineering` | `clickhouse-io` | disagreement |
| design a landing page for a SaaS product | `plan-design-review` | `open-design-landing` | disagreement |

Row 1: v4's answer is plainly wrong — `article-writing` for a SQL query. Row 3: v5's answer
is arguably better than v4's. Both are scored as failures of v5, because the metric asks
"does v5 reproduce v4", not "is v5 right".

The review already says this in one line: *"Agreement with v4 is a divergence measure, not
proof that either version is right."* That line is the whole finding. A 95% target on
agreement with a baseline that is demonstrably wrong on the same receipt is unsatisfiable
by construction, and it is not a release criterion. **Retire it. Do not chase it.**

The replacement has a name in the literature: relevance-based evaluation. Judgements are
made per (query, resource) pair, independently of any system, and systems are scored
against those judgements — not against each other.

---

## 3. Diagnosis: 31.4% is partly an evaluation artefact

`evals/scorer.py:34-37`:

```python
expected = case.get("expect_skills")
if expected:
    top = skills[: int(case.get("top_k", default_top_k))]
    result["skill_match"] = any(s in expected for s in top)
...
result["passed"] = result["healthy"] and all(checks)
```

`evals/golden/routing_cases.yaml` declares **two** acceptable skills for most cases out of a
catalog of **3,254**. Everything not on that two-item list is scored as wrong.

Worked case, `coding-frontend-001`:

- prompt: "Build a responsive React dashboard component with a sidebar and charts"
- `expect_skills`: `[frontend-ui-engineering, design-html]`
- router returned `social-media-dashboard`, cluster `frontend_ui`
- scored `skill_match: false`, `cluster_ok: true`, `passed: false`

`social-media-dashboard` is a dashboard-building skill. It was never judged — it is
*unjudged*, which the scorer treats as *not relevant*. That is the classic pooling bias in
IR evaluation: relevance judgements exist only for the pool that was examined, and a system
is penalised for returning a good document nobody happened to judge.

Contrast `qa-security-002`: expected `[qa, guard, qa-only]`, got `authentication` — a genuine
miss. Reviewing an auth module is not the same skill as implementing auth.

So the 31.4% mixes real errors with unjudged-but-plausible hits, and nobody can say in what
proportion. **That is the actual blocker behind blocker 3**: not a threshold to argue about,
but an eval whose labels cannot support a threshold.

### What the repo already has

`evals/run_eval.py:96` scores retrieval arms with **recall@k + MRR**. The chunk layer already
uses IR metrics. The routing layer uses binary exact-match. The inconsistency is internal —
the right machinery exists two files away and was not applied to routing.

This matters for cost: adopting an evaluation standard here is largely *convergence on
machinery the repo already contains*, not a new dependency.

---

## 4. Diagnosis: latency has no acceptance rule and the harnesses disagree

- `ab_benchmark_receipt.json`: avg v5 8,749.8 ms, avg v4 7,678.2 ms, worst single v4 query
  33,965.7 ms.
- `20261110_221931_swarm-fixes-head.jsonl`: avg 2,334.15 ms, worst case 15,938.1 ms.

Same system, same week, 3.7x apart. No warm/cold distinction, no concurrency, no percentile.
A1 has no latency gate at all.

For context: a hybrid retrieve + cross-encoder rerank over 3,254 short documents is a
single-digit-millisecond to low-tens-of-milliseconds operation on CPU once the model is
resident. Measured cold embedder load on this box is 51.28 s (`:8000/health`,
`embedding_load_time_s`), against a 12-15 s comment in `iso_embedder.py`. Eight separate
`SentenceTransformer(` instantiation sites across six files. Multi-second per-query routing
is therefore most likely model reload and/or an LLM call inside the hot path — which is a
design property, not a tuning knob.

---

## 5. Diagnosis: blockers 1 and 2 have a known, boring answer

A vacuous test is not fixed by writing a better assertion by hand — it is fixed by proving
the assertion *can fail*. That is mutation testing, and it is the standard answer to the
"test that passes on two empty lists" problem: mutate the production code, confirm the test
goes red. A test that survives its mutants is not a test.

A bypassed endpoint is not fixed by editing the endpoint — it is fixed by making the bypass
impossible to merge. That is an architecture fitness function: a declared contract that
fails CI when a module outside the chokepoint calls the ranking function directly. There are
eleven such call sites today (`router.py`, `router_cutover.py`, `orchestrator.py`,
`rl_feedback_loop.py`, `tool_session_broker.py`, `learning.py`, `tool_attention_v4.py`,
`coach/mcp_handlers.py`, `unified.py`, `broker_runtime.py`, plus v5 internals).

Both have off-the-shelf implementations (see matrix). Neither is research-active; they are
solved engineering.

---

## 6. Candidate matrix

Metadata pulled from the GitHub API on 2026-11-12. Stars, licence and last push are as
returned; they are the only claims here I did not read the source to confirm.

| repo | stars | licence | last push | role | verdict for this box |
|---|---|---|---|---|---|
| `AmenRa/ranx` | 705 | MIT | 2025-08-07 | IR eval, rank fusion, significance tests | **adopt** — nDCG/MRR + bootstrap CIs + RRF in one dep |
| `terrierteam/ir_measures` | 104 | Apache-2.0 | 2026-10-27 | canonical measure definitions | optional; ranx covers it |
| `beir-cellar/beir` | 2334 | Apache-2.0 | 2025-10-16 | 15+ dataset benchmark harness | reference only — datasets are wrong shape |
| `dorianbrown/rank_bm25` | 1418 | Apache-2.0 | 2026-05-02 | lexical arm | **adopt** — hybrid needs a lexical leg |
| `PrithivirajDamodaran/FlashRank` | 1014 | Apache-2.0 | 2026-07-11 | ONNX cross-encoder rerank, CPU | **adopt** — only reranker sane on no-GPU |
| `AnswerDotAI/RAGatouille` | 3976 | Apache-2.0 | 2025-05-17 | ColBERT late interaction | reject — too heavy for 2c/4t |
| `stanford-futuredata/ColBERT` | 3955 | MIT | 2025-10-14 | ColBERT reference impl | reject — same |
| `schemathesis/schemathesis` | 3771 | MIT | 2026-11-12 | property-based API testing from OpenAPI | **adopt** — kills vacuous + cross-surface drift |
| `HypothesisWorks/hypothesis` | 9176 | NOASSERTION | 2026-11-08 | property engine under schemathesis | transitive |
| `boxed/mutmut` | 1536 | BSD-3-Clause | 2026-11-07 | mutation testing | **adopt** — proves tests can fail |
| `sixty-north/cosmic-ray` | 666 | MIT | 2026-11-10 | mutation testing, distributed | alternative to mutmut |
| `seddonym/import-linter` | 1259 | BSD-2-Clause | 2026-11-10 | architecture contracts | **adopt** — forbids router bypass |
| `lm-sys/RouteLLM` | 5698 | Apache-2.0 | 2024-08-10 | LLM cost routing framework | reject — stale 2+ yrs, different problem |
| `aurelio-labs/semantic-router` | 4035 | MIT | 2026-11-09 | embedding utterance router | reject — competing router, not a fix |

Research without a usable repository, cited as method only:

- **RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection** (arXiv 2505.03275). Retrieve a
  small candidate set of tools, then let the model choose. No official repo exists — a GitHub
  search returns only 7-star and 0-star third-party reimplementations. Cite the paper.
- **Reciprocal Rank Fusion** (Cormack, Clarke, Büttcher, SIGIR 2009). The fusion rule behind
  the `rrf_score` column already present in `evals/scorer.py`. Free to adopt; no dependency.
- **Architecture fitness functions** — Ford, Parsons, Kua, *Building Evolutionary
  Architectures*, 2nd ed., O'Reilly 2022. The conceptual basis for the import-linter contract.
- **Pooling and unjudged documents** — TREC methodology. The formal statement of the bias
  diagnosed in §3.

---

## 7. Options

### O0 — Do nothing
Keep A1 blocked, keep the 15% receipt as-is. Cost: the router stays split across eleven call
sites, `/find_skills` and `/nexus/search` keep disagreeing, and the self-learning loop keeps
using rewards that §3 shows are partly noise. This is the default and it is not free.

### O1 — Build on the incumbent (recommended core)
No new framework for the evaluator. Extend `evals/scorer.py` with graded relevance and
nDCG@k/MRR, reuse the recall/MRR path already in `run_eval.py:96`, and add a bootstrap CI.
Then add `rank_bm25` for a lexical leg and RRF (the column already exists) for fusion.
Cost: days, no licence risk, no new runtime.
Risk: hand-rolled significance testing is easy to get wrong — which is the argument for O2.

### O2 — Adopt `ranx` as the evaluation standard (recommended, small)
`ranx` replaces the homegrown aggregation with nDCG/MRR/MAP, adds paired significance tests
and bootstrap CIs, and ships rank fusion. It is MIT, 705 stars, one dependency, pure Python.
Its value is not features — it is that a reviewer cannot argue with `compare()` output the
way they can argue with a homegrown pass rate.

### O3 — Adopt the rerank stack (`FlashRank` + `rank_bm25`)
Two-stage: hybrid retrieve top-50, cross-encoder rerank to top-5. This is the standard recipe
and the only one that plausibly moves 31% toward 60%+ on CPU. `sentence-transformers 5.4.1`
is already installed, so a `CrossEncoder` is available with no new dependency — `FlashRank`
is the faster ONNX alternative if latency proves to be the binding constraint.

### O4 — Adopt an LLM router (`RouteLLM`, `semantic-router`)
Rejected. `RouteLLM` solves cost/quality tradeoff between models, not skill retrieval, and
has not been pushed since 2024-08. `semantic-router` is a competing router implementation,
so adopting it re-opens exactly the multi-path problem A1 exists to close.

---

## 8. Recommended sequence

Phase 0 — make the eval able to fail (blockers 3, and the preconditions of 1)
1. Retire `top1_agreement` as a gate. Record the receipt as history.
2. Re-judge the 105 golden cases with graded relevance: `2` = correct skill, `1` = usable,
   `0` = wrong. Two judgements per case, record disagreements.
3. Add every unjudged top-5 result from the current router to the pool and adjudicate it.
   This alone will change the 31.4% and it is the honest number.
4. Score with nDCG@5 + MRR@5 + bootstrap 95% CI. Acceptance is a CI lower bound, never a
   point estimate.

Phase 1 — make the tests able to fail (blocker 1)
5. `mutmut` over `router_cutover.py` and `unified.py`; a surviving mutant on the ranking
   branch is a release blocker.
6. Replace the equality test with non-vacuity assertions: HTTP 200, `len(skills) > 0`,
   identical ordered names, expected `router_mode` — and run it against the live surface via
   `schemathesis` so an empty `skills` array cannot pass as equality.

Phase 2 — make the bypass impossible (blocker 2)
7. `import-linter` contract: only `router_cutover` may import `find_matching_skills` and
   `route_task`. Every other call site must go through `route_for_task` or carry an explicit
   `# architecture-exception:` marker with a reason.
8. Give `/find_skills`, `/nexus/search`, MCP `route_v3`, broker fallback and chat
   classification an explicit disposition in one table, committed.

Phase 3 — make the numbers move (blocker 3, quality)
9. Hybrid BM25 + dense with RRF, then cross-encoder rerank, measured against the Phase 0
   eval. Adopt only if the CI lower bound improves.

Phase 4 — contract (blocker 4)
10. `/nexus/search` becomes a versioned alias of the route shape (`/nexus/search` reads,
    `/nexus/v2/search` returns route shape) and `route_for_task(..., open_session=False)`
    for the read path. Documented migration, not a silent shape change.

---

## 9. Assumptions a reviewer should attack

- That graded relevance can be produced honestly by two judgements per case on a 105-case
  set. A 105-case set with 2 judges gives a CI roughly ±9 points at 50%; it cannot certify a
  60% floor. If the floor matters, the set needs to be larger or the floor needs to be a CI.
- That `social-media-dashboard` really is a defensible answer to the React dashboard prompt.
  §3 asserts it is plausible; that is a judgement, not a measurement, and it is exactly the
  kind of judgement the Phase 0 re-judging must make explicitly.
- That the 8.7 s latency is model reload and not an LLM call. Not measured. Measure before
  choosing between `CrossEncoder` and `FlashRank`.
- That `route_for_task` is the right chokepoint. If v5's L1 hardcodes `project="default"`
  (`server/router_v2/v5.py:203-211`) as the review reports, the chokepoint returns
  project-blind results and making it universal spreads a known defect to every surface.
- That any of this fixes the catalog problem. 2,055 of 3,254 resources have never been
  triggered; a better router over a catalog that is two-thirds dead stock may improve the
  metric without improving the product.

---

## 10. Sources checked

Local, read directly: `A1-single-router.md`, `routing_goldfish_review_2026-11-12.md`,
`evals/scorer.py`, `evals/golden/routing_cases.yaml`, `evals/results/ab_benchmark_receipt.json`,
`evals/results/20261110_221931_swarm-fixes-head.jsonl`, `evals/run_eval.py` (signature),
`:8000/health` (live).

GitHub API, 2026-11-12: stars, licence, `pushed_at`, `archived` for all fourteen repos above.
All fourteen returned `archived: false`.

Not verified: README contents of each candidate beyond the API description field; actual
runtime performance of `ranx`/`FlashRank` on this host; whether `mutmut` runs cleanly against
a codebase with eight module-level `SentenceTransformer` loads.
