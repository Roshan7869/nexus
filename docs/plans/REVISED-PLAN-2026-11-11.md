# NEXUS revised plan (orchestrator-judge review, 2026-11-11)

I checked both manager reports against the raw facts and against the repo at `/home/roshan/nexus`, read-only. Nothing was edited, and no tests or benchmarks were run.

## 1. Verdict

- The current plan is not feasible as written: four overlapping plans, three router layers in the hot path, and a learning design that needs far more observed outcomes than exist.
- Routing is feasible on this machine. The catalog is small (1,861 active resources) and needs no GPU, no LLM call and no new index.
- Learning per skill is not feasible yet. At most 281 terminal outcomes are labelled "explicit", and that label is the function default and includes test rows.
- The main change is to fix outcome labelling first, then run one bounded re-ranker in shadow mode; Harness² and RLCD are adopted only as data shapes.
- Obsidian becomes a one-way export plus one approval field, never part of the routing path.

## 2. What was executed and its status

| Item | State (verified) | Action |
|---|---|---|
| `server/harness_sites.py`, `harness_sites_store.py`, `dashboard/sites_api.py`, `config/harness_sites.yaml` | 58 tests pass (managers' run); all untracked | Keep, commit |
| `build_backends` (`server/context_harness/channels/user_site.py:121`) | Only caller is a test; `context_harness/server.py` still has 13 hardcoded registrations | Fix: wire in, or revert |
| Partial early return (`server/learning.py:380`) | Uncommitted 4-line diff in the working tree | Keep the decision, commit with the fixes below |
| Dead partial branches (`learning.py:469` and `:723`) | Both unreachable for partial | Fix: delete |
| `skip` counted as wrong (`learning.py:176`, final `else` near `:477`) | Confirmed; I read lines 400-492 | Fix |
| `outcome = "correct"` literal (`context_harness/engine.py:299`) | Confirmed; it also inherits `outcome_source="explicit"` | Fix |
| `outcome="correct"` literal (`server/rl_feedback_loop.py:296`) | Confirmed, applied to up to 3 active sessions | Fix |
| `test_edges_and_reflection.py` (repo root) | Writes to the live DB and asserts the old partial behaviour; not run | Fix: isolate before anyone runs it |
| `docs/design/06-AGENT-ADAPTERS.md` | Misses three existing integration points (routing manager's finding, not re-checked) | Mark untrusted; rewrite only when adapter work starts |
| `docs/design/05-SITES-REGISTRY.md`, remediation docs | Untracked, as is all of `docs/design/` and `docs/plans/remediation/` | Keep, commit |
| Remediation commits (T0, T1, T2, S1, S2-a) | Known from commit subjects only | Keep |
| `core/learner.py` (model-level beliefs) | Off by default per its header | Leave off |
| `server/kg/graph_lane.py` | 16 tests, not run this session | Keep |

**Where I overruled the managers**

- **`fusion.py` cannot be cut.** The routing manager listed it for deletion, but `server/router_v2/v5.py:44` and `router_v2/__init__.py:40` both import it.
- **Legacy `server/router.py` cannot be deleted in an hour.** `v5.py:210` calls its `route_task` as layer L1 on every route, and 11 other modules import it, including `nexus_http_daemon.py:427,639`.
- **The `unified_route` location is not unknown.** It is defined at `server/router_v2/__init__.py:512`.
- **The FAISS index is stale.** It holds 1,200 vectors (built 2026-07-11) against 1,861 active resources, so 661 active resources are missing from it. The routing manager only suspected this.
- **The root test does not wipe live tables.** Its DELETEs are limited to rows named `test-edge-%`. It still writes test rows and outcomes into the live DB, and `test-edge-source` already has 10 "explicit" outcomes there, so it has been run against live data before.
- **"315 observed outcomes" is an upper bound, not a measurement.** `record_outcome` defaults `outcome_source` to `"explicit"` (`learning.py:339`), so any caller that omits it is counted as explicit.
- **The two partial update paths do not disagree.** `_update_thompson_belief` has one caller (`learning.py:539`), which sits after the early return, so partial never reaches it.
- **Contrastive pairs need no new table.** `reflection_fixes` already has `alternative_resource_id` and holds 2,798 "use X instead" rows.

## 3. Constraints

**Hardware**
- 4 cores, 15 GiB RAM, 7.7 GiB available, 5.2 GiB of swap in use, no GPU.
- The daemon (pid 1685, `127.0.0.1:8000`) had 18 MB resident after 2.5 hours. The embedder is therefore not loaded in it, or is swapped out; I did not determine which.

**Catalog**
- `resources` has 3,254 rows: 1,861 active and 1,393 archived.
- Active skills number 528; the largest other active types are topic (419), command (220) and agent (162).
- Embeddings are all-MiniLM-L6-v2, 384 dimensions, in a flat inner-product index.
- `resource_edges` has 21,955 rows, of which 9,661 are `similar_to` and 10,340 are `co_triggered`.

**Outcome volume**

| `outcome_source` | Rows | Notes |
|---|---|---|
| NULL | 9,774 | No provenance; explicit rows first appear in 2026-09 |
| explicit | 315 | 248 correct, 28 wrong, 5 partial, 34 "routed" |
| timeout | 270 | All 270 are labelled "correct" |

- The 281 terminal explicit rows are concentrated: 203 are "correct" from `claude-code`, and 207 of the 315 date from November 2026.
- They cover 46 distinct skills. The top one is `test-e2e-feedback` (27 rows), a test artifact, and 17 rows are `harness:*` self-grades.
- Only 28 explicit "wrong" outcomes exist in six months.
- Only 110 resources have `times_triggered` above 10, and those counters were built mostly from the unsourced rows.
- Thompson sampling is enabled in `nexus.yaml` (`learning.thompson_routing: true`, minimum 10 samples), so the polluted counters do affect routing today.

**Latency**
- The only measurement is from 2026-06-27 over 20 queries, for the `v2` path: p50 192 ms, p95 286 ms.
- No measurement exists for `unified_route_v5`, which is the live path and adds a legacy `route_task` call on top.
- The embedder cold load is about 12-15 s according to a code comment; it has not been measured.

**Quality**
- The June benchmark gave routing accuracy 0.901, p@k 0.26 and nDCG 0.253.
- No golden pass rate has been scored.
- `ops_eval_ci` fails on `cluster_divergence` = 254 against a target of 0; the cause is unknown.

## 4. Revised architecture

1. One entry point: `unified_route_v5`. No other router is served.
2. Inside v5, layer L1 (legacy `route_task`) stays until a golden ablation shows what it contributes; then it is kept or removed on that evidence.
3. Retrieval is MiniLM embedding plus lexical match over active `resources` rows, with fusion from `router_v2/fusion.py`. The hot path makes no LLM call.
4. Sites, skills, tools and agents are all `resources` rows distinguished by `resource_type`; sites join through the existing loader.
5. Budget: p95 under 400 ms warm, to be confirmed by a fresh benchmark, with the embedder loaded once per process.
6. Every outcome write carries a mandatory source: `explicit`, `judge`, `synthetic`, `proxy` or `timeout`.
7. Only `explicit` correct and wrong outcomes move counters. Partial, skip, timeout, synthetic and proxy outcomes are logged and learn nothing.
8. Learning is a per-resource Beta count with roughly a 60-day half-life, applied as a multiplier bounded to +/-15% once a resource has 10 explicit outcomes.
9. The re-ranker runs in shadow mode first, and is enabled only if an offline replay against the golden cases does not regress.
10. Recurring failures become human-approved lessons, each citing `routing_log` or `reflection_failures` row ids.
11. The graph is the existing `resource_edges` table read through `kg/graph_lane.py` (one hop, flag-gated). No new graph database.
12. Obsidian is a nightly one-way export of resources, clusters, edges and lessons, and reads back one field (`status`) from lesson notes.

## 5. Harness square, RLCD and Obsidian

**"Harness square"**
- The research matched it to Harness² (Google Cloud AI Research, arXiv:2610.14224). The match is inferred from the name only, so please confirm or send the link you meant.
- What it is: the model stays frozen and the harness is edited. Each task is re-run under candidate harness edits (Propose-Probe-Compose), and a library keeps each winning harness with an `EXPERIENCE.md` saying when it applies.
- Adopted: the library idea only, as a lessons table with "applies when", "do/avoid" and cited evidence rows, approved by a human.
- Not adopted: Propose-Probe-Compose. NEXUS routes tasks and cannot re-run a user's task, and each probe is a full paid re-execution.
- Sources: https://arxiv.org/html/2610.14224v1 and https://github.com/google-research/harness2
- Nearby candidates, also not adopted because they need re-runs or training: Self-Harness (https://www.emergentmind.com/papers/2606.09498) and HarnessX (https://bdtechtalks.com/2026/07/13/ai-agents-self-improving-harness/, secondary source only).

**RLCD**
- What it is: Reinforcement Learning from Contrastive Distillation (Yang et al., arXiv:2307.12950, ICLR 2024). It builds preference pairs from a positive and a negative prompt, trains a LLaMA-7B preference model, then runs PPO.
- Adopted: the preference-pair data shape, using the "use X instead" rows already in `reflection_fixes`. No pair-based learning until at least 100 clean pairs exist.
- Not adopted: preference-model training and PPO. They need about 40,000 examples per task and an A100-class GPU; this host has no GPU.
- Sources: https://arxiv.org/abs/2307.12950 and https://github.com/facebookresearch/rlcd
- If you meant a different RLCD, send the link; the research found no other expansion.

**Obsidian**
- What it is: a folder of Markdown files with YAML properties and `[[wikilinks]]`. Plain files can be written without the app running.
- Adopted: a one-way, idempotent export to a new vault, plus reading back `status: approved|rejected` on lesson notes.
- Not adopted: the Obsidian CLI, the Local REST API plugin and Dataview, which all need the app running.
- Also not adopted: two-way sync, ingesting the existing 7,922-note and 7,391-note vaults, and exporting `similar_to` edges.
- `server/ingest/obsidian_vault.py` stays unused; two audits flag a symlink escape in it.
- Sources: https://obsidian.md/help/links, https://obsidian.md/help/properties, https://community.obsidian.md/plugins/obsidian-local-rest-api, https://github.com/blacksmithgu/obsidian-dataview, https://www.dsebastien.net/the-complete-guide-to-the-obsidian-cli-everything-you-can-do-from-the-terminal/

**Model tiers.** Fable as judge, Sonnet as manager and Haiku as workers applies to how the tasks below are built and reviewed. It is not part of the routing runtime.

## 6. Cut list

| Cut | Reason |
|---|---|
| Macro/micro plan P3-P4 (plugin host, registry, waterfall, fiber, hot swap) | Gated on an unanswered decision; routing does not need it |
| Local harness M/A/B docs (28 rows) | No status on any row; their migrations 020/021 are untracked files outside the remediation numbering |
| Remediation E1, D2, D3, and C3 unless its one check passes | Already dropped or deferred in EXECUTION |
| Remediation A1 as a separate item | Same work as FIX-DESIGN S3 (one router) |
| Per-(resource, task_type) beliefs | 28 explicit wrong outcomes cannot support them |
| The 70/30 sampled-to-keyword blend | With near-prior posteriors the 70% share is mostly noise |
| `core/learner.py` model-level routing | No consumer; harness agents cannot switch models mid-session |
| Full Harness², Self-Harness and HarnessX loops | Need task re-execution or training |
| RLCD training, PPO and a new `outcome_pairs` table | No GPU; the pair data already exists in `reflection_fixes` |
| Separate offline replay script for the re-ranker | Would report "insufficient data" for months; folded into the shadow gate |
| Cold-tier surfacing as its own subsystem | 7.3% surfaced with outcome NULL; one exploration term in v5 is enough |
| Obsidian CLI, REST, Dataview, two-way sync and vault ingestion | Need the app running, or add sync risk for no routing gain |
| Cognee/ladybug graph path and any new graph DB | The probe was never re-run and its config was never loaded |
| GitNexus code graph as the knowledge graph | It is a code graph, and one commit stale |
| `graphify_nexus_bridge.py` extension | Contents unread; do not build on it |
| Adapter doc rewrite now | No adapter work is scheduled; mark the doc untrusted instead |

## 7. Ordered task list

| id | Goal | Files | Gate | h | Depends | Tier |
|---|---|---|---|---|---|---|
| T01 | Commit the untracked sites code, tests, design and remediation docs, and the `learning.py` diff, on a branch | git only | `git ls-files` lists them; the 58 tests pass | 0.5 | none | haiku |
| T02 | Move the root test under `tests/` onto a temp DB and update the partial assertion | `test_edges_and_reflection.py` | `db/nexus.db` sha256 unchanged across the run; test green | 1 | T01 | sonnet |
| T03 | Make `skip` non-learning and delete both dead partial branches | `server/learning.py:380,469,723` | Test: skip and partial leave `times_wrong` and beliefs unchanged | 1 | T02 | sonnet |
| T04 | Remove the `"explicit"` default and tag the two literal writers `synthetic` | `server/learning.py:339`, `context_harness/engine.py:299`, `rl_feedback_loop.py:296` | Test: those paths change no counter; a call with no source fails | 1 | T03 | sonnet |
| T05 | Read-only provenance audit by source, ide and test-named skills | new `scripts/outcome_audit.py` | Reproduces 9,774 / 315 / 270; prints the count of clean explicit rows | 0.75 | none | haiku |
| T06 | Source gate: only `explicit` moves counters; `judge` goes to a separate counter | `server/learning.py` | Tests: timeout, synthetic and proxy change nothing | 1 | T03, T04 | sonnet |
| T07 | Benchmark v5 warm and cold with per-layer timing and process RSS | `_research/_bench_route.py` | p50/p95 over 20+ queries recorded per layer | 1 | none | haiku |
| T08 | Score the golden cases against v5 as the baseline | `evals/golden/routing_cases.yaml`, `evals/results/` | Pass rate recorded and checked in | 1 | none | haiku |
| T09 | Compute the v5 import closure by AST and list modules in and out | `server/router_v2/v5.py`, `router_v2/__init__.py` | Committed list; every module outside the closure names its other importers | 1 | none | haiku |
| T10 | Find which path reads the FAISS index and why 661 active rows are missing | `db/faiss/`, `scripts/rebuild_faiss.py`, `server/router.py` | Cause named; rebuild done or the gap accepted in writing | 1 | T09 | sonnet |
| T11 | Golden ablation of v5 with and without L1 (legacy `route_task`) | `server/router_v2/v5.py:204-215` | Two pass rates and two p95 values; keep-or-remove decision recorded | 1 | T07, T08, T09 | sonnet, fable judges |
| T12 | Reproduce `cluster_divergence` = 254 and find the cause | `server/catalog_lifecycle.py`, `logs/ops_eval_ci.json` | Root cause named, or the gate shown to run on a different DB | 1 | none | sonnet |
| T13 | Replace the 13 hardcoded registrations with one `build_backends` call | `server/context_harness/server.py:918-1188`, `channels/user_site.py:121` | Parity test: same backend names before and after | 1 | T01 | sonnet |
| T14 | Register sites as `resources` rows of type `site` and add golden site cases | `server/harness_sites_store.py`, `evals/golden/routing_cases.yaml` | A site query returns a site row; baseline cases do not regress | 1 | T08, T13 | sonnet |
| T15 | Shadow re-ranker: decayed per-resource Beta, 10-outcome gate, +/-15% bound | `server/learning.py`, `server/router_v2/scoring.py` | Flag off gives byte-identical output; shadow log written | 1 | T06, T08 | sonnet |
| T16 | Lessons v0: table plus 20 drafts, each citing row ids, status draft | new `server/lessons.py`, schema | 20 drafts, each with at least one valid row id | 1 | T05 | haiku drafts, sonnet code |
| T17 | One-way vault export of resources, clusters and edges (no `similar_to`) | new `scripts/export_obsidian.py` | Zero unresolved wikilinks; a second run gives identical hashes | 1 | T01 | haiku |
| T18 | Export lessons and read back `status` from frontmatter | `scripts/export_obsidian.py`, `server/lessons.py` | Editing `status: approved` in a note updates the DB row on the next sync | 0.75 | T16, T17 | sonnet |
| T19 | Merge README, EXECUTION and FIX-DESIGN into one status table under FIX-DESIGN ids | `docs/plans/remediation/` | One id space; macro/micro and local harness marked frozen | 1 | T01 | haiku |
| T20 | Remove router code outside the closure, one module per change | per the T09 list | Tests and golden score unchanged after each removal | 1 per module | T09, T11, T12 | sonnet, fable reviews |

T20 is the only task that will exceed one hour in total; its size is unknown until T09 and T11 are done.

## 8. Decisions for the owner

1. **Is "harness square" Harness² (arXiv:2610.14224), and is RLCD the Yang et al. paper?** I recommend confirming both and approving the reduced scope in section 5; send links if you meant something else.
2. **What to do with the existing counters, built mostly from 9,774 unsourced rows?** I recommend leaving them in place but computing the new shadow re-ranker only from clean explicit rows, then turning off the current Thompson blend if the shadow version does not regress.
3. **Is partial telemetry-only?** I recommend yes, as both managers did.
4. **Is timeout ignored?** I recommend yes; all 270 timeout rows are labelled "correct", which is fabricated signal.
5. **Wire or revert `build_backends`?** I recommend wiring it (T13), because sites cannot become routable without it.
6. **Keep or remove L1 legacy routing inside v5?** I recommend deciding from the T11 numbers, not before.
7. **Is `cluster_divergence` a gate or a metric?** I recommend keeping it as a gate until T12 names the cause.
8. **Where does the vault live?** I recommend a new `/home/roshan/Obsidian/nexus-brain`, separate from `agent-brain`.
9. **Who approves lessons?** I recommend you, through the `status` field in the vault, with Haiku drafting.
10. **Is NEXUS a plugin host?** I recommend no for now, which keeps macro/micro P3-P4 cut.
11. **Should test rows in the live DB be purged?** I recommend excluding them in queries first (T05) and deciding on deletion after seeing the count.

## Uncertainties

- v5 latency, golden pass rate, embedder memory and the cold-load time are all unmeasured.
- I did not establish which process actually serves routes or holds the embedder.
- Completion of the remediation commits is known from subjects only, not diffs.
- I did not read the `cluster_divergence` computation, `graphify_nexus_bridge.py`, or the non-`.py` references to `harness_sites`.
- Whether migrations 020 and 021 were applied to the DB is unknown; `schema_migrations` has one row.
- The Harness² and other 2026 sources are cited from the research lane; I did not open them myself.