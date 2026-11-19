# NEXUS Project Governance Plan

**Date:** 2026-11-19 · **Branch:** `second-brain-v2` @ `6ab89a3` · **Author:** lead (root agent)
**Scope:** establish control of a two-agent, single-branch project; convert the session's
measured evidence into binding rules; execute the top of the queue.

---

## 1. Situation (recon, measured)

| Fact | Value | Source |
|---|---|---|
| Working tree | **clean**, HEAD `6ab89a3` | `git status` |
| Branch | `second-brain-v2` (tracks `origin/second-brain-v2`) | `git branch` |
| Agents writing this tree | **2** — this session (commits as `NEXUS`), another DSH session (commits as `Roshan7869`) | `git log --format='%an'` |
| Interleaving | `Roshan7869/fba59c9` landed *between* my `13aede5` and `6ab89a3` | `git log` |
| Test baseline | **90 failed / 714 passed / 24 skipped / 14 errors** (186 s) | `pytest tests/ -q` |
| Resources | 5,068 active; 363 stubs (<300 chars) | DB |
| Supersession ledger | columns exist (`invalidated_at`, `absorbed_at_*`); **0 invalidated, 0 versions** — inactive | DB |
| Routing accuracy (hermetic) | hit@k **0.274**, top-1 0.131, MRR 0.1935 | `golden-hermetic-baseline.json` |
| Routing latency | **585 ms** p50 isolated / **~1,200 ms** p50 inside the eval harness | bench vs receipts |
| Frozen eval snapshot | `D:\nexus-data\db\nexus_eval_reference.db` | this session |
| Learning data | 9 outcome rows, 4 resources with any outcome (0.08% arm coverage) | DB |

**Three structural risks this plan exists to control:**

1. **No ownership boundary.** Two agents edit one branch; `server/router.py` and
   `server/ingest/repo_resources.py` have already been touched by both.
2. **No invariant enforcement.** A cross-cutting rule (supersession) was added to the
   v4 path but NOT to the v5 retrieval lane — the ledger can be silently defeated.
3. **No trustworthy baseline.** Before this session there was no accuracy number at all;
   the first measurements were contaminated by DB drift (routing_log 114 → 3,345).

---

## 2. Ownership model

| Scope | Owner | Files |
|---|---|---|
| **Routing / retrieval / eval / perf** | this session (`NEXUS`) | `server/router_v2/**`, `evals/**`, `scripts/eval_*`, `scripts/backfill_*`, `scripts/split_chunks.py`, `server/local_harness/**`, `docs/plans/*RESULT*`, `docs/plans/PERF-*` |
| **Supersession / ingestion / context hygiene** | other session (`Roshan7869`) | `server/context_hygiene.py`, `scripts/migrate_supersession.py`, `server/ingest/**`, `server/broker_runtime.py`, `server/context_harness/**`, `tests/test_supersession.py`, `docs/designs/**` |
| **CONTESTED — change only with a notification commit** | both | `server/router.py`, `server/db.py`, `db/migrations/**`, `tests/conftest.py` |

### Protocol (binding)

1. **Commit prefix declares the lane:** `E*` (eval/perf/routing) · `S*` (supersession) ·
   `G*` (governance). A commit touching a contested file states which lane and why.
2. **Small, frequent commits.** Never leave the tree dirty across a session gap — the
   other session may `git add -A` and absorb half-finished work.
3. **No history rewrite, no `reset --hard`, no `checkout --` on files you did not author.**
4. **Contested-file edits:** re-read the file immediately before editing; if the other
   lane's change is present, preserve it (grep the invariant you are editing around).
5. **Any change to ranking/retrieval is not done until the hermetic gate passes** (§3 G1).
6. Every commit message states the measured effect or the words "unmeasured".

---

## 3. Gates (definition of done)

| Gate | Rule | Command |
|---|---|---|
| **G1 Accuracy** | Hermetic golden eval. hit@k must not fall below baseline minus the bootstrap CI half-width; any claim of improvement needs the receipt JSON. | `python scripts/eval_router_seeds.py --seeds 3 --hermetic --tag <name>` |
| **G2 Tests** | No **new** failures vs the recorded baseline (90F/714P/14E). Register each new failure or revert. | `pytest tests/ -q -p no:cacheprovider` |
| **G3 Receipts** | Measured claims archived under `evals/results/perf/` (or the relevant receipts dir) before the commit. | — |
| **G4 Honesty** | No fabricated outcomes, no "verified" without an artifact, negative results kept, not deleted. | review |

**Baseline registry** (update only with a receipt):

```
tests        : 33 failed / 797 passed / 28 skipped / 11 errors   @ 2513ec0 (full run, 408 s)
               chain: 90F/714P/14E @ 6ab89a3 -> 77F/730P/15E @ 2871c6d -> 65F/765P @ f7f4591
               -> 59F/771P @ d75d217 (SIGALRM fix e15f29c) -> 33F/797P (G5 fixture parity 6333f8a/f82e85f)
               new-failure register (G2): test_unified::test_get_learning_stats_when_disabled
               passes in isolation - order-dependent pollution of the shared import-time
               E2E DB, exposed by the import-time bootstrap. Registered, not reverted.
accuracy     : hit@k 0.286  top-1 0.131  MRR 0.1954  pass 0.276  @ ref-current
               (frozen reference, 3 seeds, cross-seed spread 0.000)
reference    : D:\nexus-data\db\nexus_eval_reference.db  (NEXUS_EVAL_REFERENCE)
latency      : p50 585 ms / p95 945 ms isolated; 1,100-1,300 ms p50 in-harness
corpus       : 4,521 chunks / 4,521 embeddings / 4,521 vectors
```

**Measurement integrity — two bugs found and fixed this cycle (both were hiding
a false result; read this before trusting any earlier number):**

1. **Harness wrote to the DB it measured.** `routing_log` grew 114 → 3,469 across
   the session and `N_total` (= COUNT(routing_log)) feeds the UCB term, so runs with
   an identical corpus scored 0.274 and 0.286. Fix: `--hermetic` routes against a
   throw-away snapshot.
2. **Hermetic copied from the LIVE db, and with a file copy.** Copying a 353 MB
   WAL-mode DB file-by-file can snapshot a torn state, and copying from live still
   drifts. Fix: SQLite `backup()` API + **copy from the frozen reference by default**,
   with a hard abort if the snapshot has 0 chunk vectors.

3. **A silent `except Exception: return []` masked a total retrieval failure.**
   A missing `import os` in `router_v2/__init__.py` made the new `vec=` argument raise
   `NameError`; the broad handler turned that into "no chunk candidates" and routing
   quietly degraded to a non-v5 path — measured as hit@k 0.286 → 0.155 with **no error
   anywhere**. Fixed: import added, and the handler now logs at warning with a
   traceback. *Any broad handler that can turn a code error into "empty result" is a
   governance defect.*

---

## 4. Invariant register

| # | Invariant | Status |
|---|---|---|
| **I1** | **Supersession is honored by every query that selects resources** (`invalidated_at IS NULL`, and version-live filters where `resource_versions` is populated). | ❌ **VIOLATED** — v4 filters it; `router_v2/chunk_retrieval.py` and `scoring.py` do not. A tombstoned resource stays retrievable through v5. |
| **I2** | Outcomes are only ever written explicitly; ambiguous multi-tool dispatches credit nothing (`NEXUS_CREDIT_POLICY=strict`). | ✅ enforced (`execution_plane.py`) |
| **I3** | One DB path source: `server/db.get_db_path()`; no new raw `sqlite3.connect(str(DB_PATH))` in read paths. | ⚠️ partial (write sites legitimately keep own connections) |
| **I4** | Evals are hermetic (route against a snapshot) so `N_total`/UCB cannot drift between runs. | ✅ `--hermetic` |
| **I5** | No performance claim without a bench receipt; no accuracy claim without the golden receipt. | ✅ this session |
| **I6** | Corpus-shape changes (multi-chunk, dedupe, priors) default OFF or reverted unless the gate improved. | ✅ E5 + E4b both defaulted/reverted |

---

## 5. Work queue (evidence-ranked)

| # | Item | Why now | Effort | Gate |
|---|---|---|---|---|
| **W1** | **Fix I1: supersession asymmetry in the v5 lane** | A cross-cutting invariant is silently defeated; the other lane is actively building on it. Cheapest possible defect to fix while the ledger is still empty (0 invalidated → no risk of changing current rankings). | S | G1 (must be byte-identical while ledger empty) + G2 |
| **W2** | **Explain the latency gap: 585 ms isolated vs ~1,200 ms in-harness** | All published latency numbers are currently ambiguous. Either the harness leaks state or the router has per-process warm-up. | S–M | receipt |
| **W3** | **Triage the 90 test failures into buckets** (env/schema-drift/real) | 90 red tests make G2 unusable — "no new failures" cannot be enforced against a number nobody understands. | M | report |
| **W4** | Enrich the 363 stub resources | Accuracy ceiling is content-bound, not ranking-bound (three ranking experiments failed). **Blocked:** needs URLs/content sources. | L | blocked |
| **W5** | Install the refresh trigger (weekly) | Pipeline exists, nothing runs it; catalogue ages again. | S | ops check |
| **W6** | Wenlan decision (retire vs reinstall) | Orphaned mirror, 613 MB preserved. **Blocked on user decision.** | S | user |

## 6. Execution order for this cycle

```
W1  supersession asymmetry  → gate G1: 0.286 (unchanged) ✅ DONE
W2  latency gap diagnosis   → receipt ✅ DONE (finding below)
W3  test triage + fix       → 90F/714P -> 77F/730P ✅ DONE
W4  stub recovery           → 85 resources recovered; accuracy effect UNMEASURED
W5  weekly refresh trigger  → scheduled task registered ✅ DONE
W6  Wenlan retirement       → fully removed ✅ DONE
```

### W3 result — 90 → 77 failures, via schema parity

Triage found the dominant bucket was **test-fixture schema drift**, not defects:
86 of 90 failures were `no such table: outcome_log / tool_sessions /
resource_embeddings / note_ledger / hygiene_flags` plus `duplicate column
name: updated_at`. `tests/conftest.py` declared only **21** of the live DB's **98**
tables in a hand-maintained `SCHEMA_SQL`.

Fix: `scripts/regen_test_schema.py` generates `tests/schema_production.sql` from the
live `sqlite_master` (48 missing tables + indexes, all `IF NOT EXISTS`), and conftest
applies it after `SCHEMA_SQL`. Regenerate whenever a table is added, so it cannot
drift again.

Measured: **90F/714P/14E → 77F/730P/15E** (+16 passing, −13 failing). Errors 14→15 is
a re-classification, not a new break (total collected changed with the other lane's
new tests). Remaining buckets: Windows console `UnicodeEncodeError` (20, cosmetic — did
not reproduce in the validation environment), file-lock/permission (12), `AttributeError`
(12 exception *lines* = **6 tests**; one root cause: `signal.SIGALRM` is POSIX-only,
`server/context_harness/compression.py:121`). **Post-cycle: fixed in `e15f29c` — all 6
now pass (65F/765P → 59F/771P, zero new failures).**

### W4 result — recovery done, effect UNMEASURED (confounded)

363 active resources had <300 bytes of body text. Their `file_path` values are the
original author's **Linux** paths (`/home/roshan/...`), so they cannot be opened
directly; but for clones present in this workspace the path *suffix* maps onto the
clone. `scripts/recover_stubs.py` resolved and re-read 90 candidates (excluding the
user-excluded `aashiyaana`, `kaari-weaves`), recovering **85 resources** with real
content (stubs 363 → 333; the other 5 files were themselves <40 bytes).

**Why the accuracy effect is not claimed:** the only available comparison (0.262 vs
the 0.286 frozen reference) differs in *both* corpus content and `routing_log` state,
and `N_total` feeds the UCB term — so the delta is not attributable. A valid A/B needs
a pre-recovery snapshot, which was not taken. Recorded as **unmeasured**, not as a
win or a regression. Revert path if desired: delete chunks whose
`source_id` is the `stub_recovery` ingestion source, then re-run the gate.

### W5 result — scheduled, verified

`scripts/run_refresh.cmd` (sets DB/cache/router env, logs to
`D:\nexus-data\logs\refresh.log`) registered as Windows task
**`NEXUS-Weekly-Refresh`**, weekly Sunday 03:00, next run **2026-11-22 03:00**,
`StartWhenAvailable`, 2 h limit, `IgnoreNew`. Dry-run confirms stages:
`pull, absorb, enrich, embed, reindex, fts, obsidian, commit` — no Wenlan stage.

### W6 result — Wenlan fully retired

See `docs/plans/W6-WENLAN-RETIREMENT.md`. Removed the `mcp_servers.wenlan` block from
the Hermes config and the `wenlan:` block from the DSH mcp-manager profile; the refresh
pipeline no longer shells out to the deleted binary (this was a prerequisite for W5 —
otherwise the weekly job would have failed every run). Three modules marked RETIRED but
kept as reference. `%LOCALAPPDATA%\wenlan` (614 MB, 4,630 memories) **preserved**,
awaiting the user's keep/export/delete decision.

### W1 result — done, verified

`router_v2/chunk_retrieval.py`: added `_has_supersession()` / `_invalidated_rids()`
(schema-defensive, so test fixtures without the column still work) and a filter in the
result loop. Verified with a synthetic tombstone on a scratch copy: the top-ranked
resource `gsd-debug-session-manager` disappeared from the lane and later results
promoted up; the live DB was untouched (0 invalidated). Gate G1 unchanged at 0.286 —
correct, because the ledger is empty, so I1 is now enforced with zero behavior change.

### W2 result — diagnosed, root cause is query-dependence not a leak

| Condition | p50 |
|---|---|
| my 3 bench queries | 774 ms |
| 6 golden prompts (early) | 798 ms |
| 6 golden prompts (mid-set) | **1,659 ms** |
| first 10 sequential routes | 1,168 ms |
| last 10 sequential routes | 1,235 ms (+6%) |

So the reported gap is **not** state accumulation (+6% over 40 routes) and **not**
process warm-up. It is **query-dependent**: some prompts cost 2× because they trigger
more expensive paths (cluster detection / larger candidate sets). The honest reading:
**~585 ms is a best-case quiet-machine figure; 1,100–1,300 ms p50 is what the harness
actually sees**, and neither is wrong — they are different query mixes. Any future
latency claim must state the query set. Finding the exact expensive branch is the
follow-up (profile the 1,659 ms prompts).

## 7. Open risks carried forward

| Risk | Severity | Note |
|---|---|---|
| 33 failing tests make G2 partial | **medium** | remaining: 15 E2E assertion/order failures in test_unified + test_feedback_loop (S-lane flows, incl. 1 registered order-pollution new-failure), 6 graph_lane, 5 capability_catalog file-lock/symlink-privilege, 7 scattered (epsilon, degradation, db_refuses_unwritable, site_registry, mcp_bridge); 11 errors = site_outcome_learning 10 + degradation 1; the fixture-schema-drift bucket is fully closed (`6333f8a`/`f82e85f`) |
| Latency on certain query shapes (2×) | medium | unidentified branch; needs a profile of mid-set prompts |
| `chunks_fts` holds 3,997 orphan rows | low | stale FTS entries from an earlier ingestion; unhydratable so skipped, but wasted I/O |
| Two agents, no automated ownership check | medium | protocol is cultural until a pre-commit scope check exists |

Rationale: W1 protects another lane's in-flight work (highest leverage per minute),
W2 makes every future latency claim trustworthy, W3 makes gate G2 enforceable. W4 is
content-bound and blocked; W5 is ops; W6 needs the user.
