# STATUS — OKF-native retrieval + Cordis local harness

Update this file as each check clears. `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked.

## Phase 0 — golden eval baseline
- [x] ≥40 curated cases in `evals/golden/routing_cases.yaml` (105 merged: 5 seed + 4×25 drafts)
- [x] drafts promoted (coding/general/reasoning/ood) → archived under `drafts/archived-20260925/`
- [x] `baseline_chunks_hybrid.json` written (recall@k + MRR)
      → recall@k=0.1905, mrr=0.1373, p50=108ms, scored=84, health_only=21
- [x] OOD cases score near-zero → scored OOD (multi-*) recall=0.0; bare-OOD = health-only
      (no expectation → recall undefined, excluded by design)
- Curation fix: `reasoning-sec-cve-blast-radius-014` accept_clusters omitted all
  expect_skills' clusters → added analyzer_planner/general.
- Note: 17 scored cases return 0 results (cluster gate + unlinked chunks, gap G10);
  same gate applied to all arms in Phase 2, so the A/B stays internally consistent.

## Phase 1 — OKF sections corpus arm
- [x] `_fts_search_sections` added (uses new `_prepare_fts_any` OR-join — AND-match
      returned 0 rows on every multi-token query against short sections; chunks
      path keeps `_prepare_fts` unchanged)
- [x] `_hydrate_sections` added (mirrors `_hydrate`; resource fields via shared
      `_hydrate_resources` → one corpus-agnostic assembly path)
- [x] `retrieve_chunks(corpus=...)` branch + default `"chunks"` (section rows
      normalized to `chunk_id` → `_rrf` untouched)
- [x] `router_v2/__init__.py:277` plumbs `RETRIEVAL_CORPUS` (module const = "chunks")
- [x] section coverage backfill run (`fail == 0`) — cold pass: 0 candidates/0 fail;
      full-coverage pass synthesized 385/385 (126 active + 259 archived chunk-linked
      resources) → 0 lacking; `sections_fts` triggers keep FTS in sync (23700=23700)
- [x] `corpus="chunks"` output regression-identical — final re-run after ALL edits:
      0/105 cases differ (`p1_regress_chunks_final.json`, 0.1905/0.1373)
- [x] section vec arm built in-Phase-1 (prep for Phase 2 arm B): `SectionIndex`
      (`chunk_index.py` sibling class) + `backfill_section_embeddings()`
      (`okf_sections.py`); vec0 self-repair of legacy plain `vec_sections_content`;
      floats in `section_embeddings`; coverage floats=vec=non-empty=21336
      (2364 empty-content sections skipped, by construction)
- Arm dev results (105 golden cases, scored=84):
  `sections+lexical` recall@k=0.119, mrr=0.0556, p50=27ms, sem=False
  `sections+hybrid`  recall@k=0.1905, mrr=0.0873, p50=74ms, sem=True
  (recall parity with chunks at k; MRR behind — Phase 2's A/B adjudicates)

## Phase 2 — A/B + conditional embedder drop
- [x] `ab_okf_*.json` has arms A/B/C (recall@k, MRR, p50) → `ab_okf_20260925.json`
      A=0.1905/0.1373/56ms · B=0.1905/0.0873/66ms · C=0.1071/0.0476/30ms
- [x] written verdict (A|B|C) + numbers → **A** (B: mrr −36%; C: fails both
      EPS bounds) — appended to `docs/decisions/0002-okf-retrieval.md` as RESULT
- [x] default flipped per verdict → verdict A = no flip
      (`RETRIEVAL_CORPUS="chunks"`, `vec=True` unchanged)
- [~] if C: `used_semantic==False`, latency delta logged, FAISS flagged legacy
      → N/A (C rejected); C's 30ms p50 vs A's 56ms logged as embedder cost note
- [x] decision 0002 appended with RESULT

## Phase 3 — harness OKF context block
- [x] `_adapter_okf_sections` added + registered first (context_assembly.py;
      self-catches → `okf_sections(error)`; hybrid sections corpus, top_k=5)
- [x] `assemble().sources` includes `okf_sections` →
      `['okf_sections', 'repo_map(skipped: no git_root)', 'project_memory(empty)', 'notes']`,
      block contains `## Relevant OKF sections` with resource names
- [x] budget_ms respected — budget=0 → all adapters `(budget)`;
      default 800ms: okf adapter ~40-70ms, elapsed 265ms total
- [x] `nexus_local_harness_open` context reflects OKF sections (live MCP check,
      session 671d0d32398e4db7 closed with outcome=correct)
- Impact gate note: `assemble` upstream = **HIGH** (d=1
  `ToolSessionBroker._build_context_block` → 4 processes); change is additive +
  never-raises by design, reported per AGENTS.md. `open_local_session` = LOW.

## Phase 4 — M9 spatial coeffects
- [x] `providers.py` `liveness_snapshot` (new file; probes db (SELECT 1), faiss
      (real ClusterNumpySearch read — `index_ready` from the sketch does not
      exist), git (rev-parse), internet (TCP 1.1.1.1:443), embedder (import-only);
      never raises)
- [x] `refresh()` live activate/deactivate (LOADED→ACTIVE commits bindings +
      satisfied=True; deactivation clears bindings, satisfied=False, LIFO
      `ctx.dispose()`; recommit uses the stored resolver; `current=None` →
      live snapshot; `open_local_session` now sets state=ACTIVE at open since
      providers commit there)
- [x] provider-loss → LIFO dispose exactly once (test: accumulator drained,
      `_armed=False`, `calls==["disposed"]`)
- [x] flap test: no double-dispose (True→False→True→False → single dispose)
- [x] `CLUSTER_KEYS` drops `faiss` iff arm C → verdict A: faiss KEPT (N/A;
      existing `test_keys_for_cluster` still asserts faiss present)
- [x] live DB-loss deactivation clean (monkeypatched get_connection →
      snapshot db=False → ACTIVE→INACTIVE, no leaked bindings, dispose idempotent)
- Tests: `tests/test_local_harness.py` 41/41 pass (6 new M9 tests)

## Phase 5 — HMR loader live-apply
- [x] `apply_db.make_apply` callback (new file: preimage capture + archive-never-delete
      + disabled-toggle reload + `record_reconcile` audit helper; NEVER commits —
      caller owns the txn)
- [x] MCP reconcile `applied: true` + mutates resources (live handler: txn + commit;
      **`apply=false` dry-run valve added** after a live trial showed a partial
      desired tree mass-archives every omitted active resource — docstring now
      states desired_json = complete desired active set)
- [x] mid-batch failure → `rolled_back: true`, DB unchanged (loader LIFO reverse +
      txn rollback; tests assert statuses AND row counts equal pre-state)
- [x] disabled toggle archives (no hard delete / no un-retire in place)
- [x] `local_harness_reconciles` row per call (success counts from report; failure
      counts from pre-diff + `rolled_back:true` in report_json)
- Tests: `tests/test_local_harness.py` 49/49 (8 new HMR tests); full suite
  442 passed / 0 failed / 24 skipped
- Live verify (temp copy of real DB, prod untouched): preview no-mutation;
  live 1-add/1-remove → applied=true, 3254→3255 rows (+1 created, 0 deleted),
  active 1861→1861, resource_edges + resource_sections byte-count unchanged,
  audit row counts 1/1
- Fix along the way: Phase 3's okf adapter now passes `db_path=get_db_path()`
  (module-const DB_PATH broke the NEXUS_DB_PATH isolation that
  `tests/test_context_assembly.py` relies on → 1 pre-existing failure, now green)

## Global gates (every phase)
- [x] `impact(upstream)` run before each symbol edit; HIGH/CRITICAL reported
      (HIGH: `assemble` — reported at Phase 3; LOW: `retrieve_chunks`, `_rrf`; CRITICAL:
      `_chunk_resource_stream` — retro-run at L1-b closure, 14 upstream / 9 processes;
      edit was the additive `graph_lane=False` kwarg, validated by eval + suite, reported.)
- [x] `detect_changes()` before each commit — no commits were made this track (all work
      left uncommitted by request); `detect_changes()` run at closure for scope review.
- [x] no find-and-replace renames (used `rename`)
- [x] no new parallel retrieval flag (D4 honored)
      — `NEXUS_GRAPH_LANE*` env valves gate the *fold inside the single*
      `retrieve_chunks` module; no parallel module, no fourth router, single pipeline
      (D4 bans a parallel retrieval module/`NEXUS_RETRIEVAL` flag — not an in-module
      feature gate; second-brain 00-overview "no new parallel router" also ticked.)
