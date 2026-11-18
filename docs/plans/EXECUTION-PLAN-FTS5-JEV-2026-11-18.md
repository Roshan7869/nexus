# EXECUTION PLAN — FTS5 Hot-Path Fix + JEV Teacher→Student Bake-In

**Date:** 2026-11-18 · **Target:** `default-workspace/nexus` + `D:\nexus-data\db\nexus.db`
**Author:** Hermes (measured on the live DB, not estimated)
**Scope:** two independent tracks. Track A is pure speed with zero accuracy risk. Track B activates
TypeSafe/JEV as a *bounded teacher* and converts its labels into a local classifier so the API can
be switched off. Track C is the bake-in that makes B's exit real.

---

## 0. Corrected findings — what the code actually says

Three premises needed correcting before a plan could be written honestly.

### 0.1 JEV is already implemented — it is dormant, not missing

| File | Lines | State |
|---|---|---|
| `server/decision_classifier.py` | 225 | `TypeSafeJevClassifier` fully written — payload, auth, timeout, answer extraction, out-of-set refusal |
| `server/cluster_decider.py` | 219 | `JevClusterDecider` wired behind a `Protocol`, auto-selected when a key exists |

Both read the key from `TYPESAFE_API_KEY` **or** `~/.config/jev/api_key`. Auto-resolution is already
`auto` → JEV if keyed, else local. **No integration code needs to be written for cluster detection.**

What is actually missing:

| Gap | Evidence |
|---|---|
| Key is blank | `.env:140` → `TYPESAFE_API_KEY=` with **value_len 0**; `~/.config/jev/api_key` **missing** |
| `load_dotenv()` is CWD-dependent | From `C:\Users\khatr` it returns `False` and no key loads; only works from the nexus dir. The MCP server does not guarantee that CWD. |
| JEV reaches **only cluster**, not task_type | `router.classify_task()` (line 140) uses `NexusLearner` → keyword fallback. It never calls `get_classifier()`. |
| No budget, no cache, no ledger | Every call would be a fresh hosted request; nothing caps spend or records labels |

### 0.2 The SQL bottleneck is narrower — and 50× worse — than "Python is slow"

Two `LIKE '%token%'` scans over `body_text` (37 MB) dominate. Measured directly:

| Path | Measured | Detail |
|---|---|---|
| `find_matching_skills` Pass 1 | **1,083 ms** | 5× `UNION ALL`, **no LIMIT** → 3,215 rows, **40.9 MB pulled into Python** |
| `_resource_search` (`router_v2`) | **1,030–1,264 ms** | 5 LIKE columns × tokens, `GROUP BY`, `LIMIT 50` |
| `yaml.safe_load` per call | **62 ms** | `nexus.yaml` re-parsed on every route |
| Full warm route | **3,676 ms** | of which ≈2.2 s is the two scans |
| Full cold route | **10,021 ms** | + ~6.3 s one-time ONNX model load |

FTS5 prototype against the real DB (300-candidate bounded retrieval + hydrate):

```
run1: FTS5 7 ms (300 ids) | hydrate 14 ms | total 21 ms
run2: FTS5 6 ms (300 ids) | hydrate  9 ms | total 15 ms
run3: FTS5 6 ms (300 ids) | hydrate 12 ms | total 18 ms
```

**15–21 ms vs 1,083 ms → 52–72×** on the worst query. Everything after that is C code
(`sqlite3`, ONNX Runtime) that Rust would not touch — see §7.

### 0.3 `resource_sections` is an empty table being JOINed on every query

`resource_sections` = **0 rows**; `sections_fts` is empty. Yet `_resource_search` builds 2 extra LIKE
predicates plus a `LEFT JOIN` against it. Dropping the join measured **1,162 ms — no change**, which
proves the cost is the `body_text` scan, not the join. The fix is FTS5, not join-removal. The dead
join should still be deleted for clarity, but it is not the win.

---

## 1. Reality check on "after 10–20 iterations NEXUS learns which tools to call"

This is **half true, and the halves need different mechanisms**. Saying otherwise wastes your budget.

| Decision | Label space | 20 JEV calls enough? | Correct mechanism |
|---|---|---|---|
| **Cluster** (8) | 8 classes | **Yes** — ~2.5 labels/class moves centroids meaningfully | JEV labels → local centroid (Track C) |
| **task_type** (7) | 7 classes | **Yes** — same reasoning | JEV labels → local prototype |
| **action/intent** (~6) | 6 classes | **Yes** | JEV labels → local prototype |
| **which of 5,066 tools to call** | **5,066 classes** | **No — off by three orders of magnitude** | `routing_log` outcomes + ZoE weights + 15,392 edges |

So: **JEV buys you classification, not tool selection.** Tool selection already has a learning
signal (`routing_log`: 55 rows, 9 closed, 16.4% coverage) and the fix there is closing that loop,
not spending API calls. A 20-sample corpus cannot rank 5,066 candidates; pretending it can is how
this project gets a wrong answer with high confidence.

**Consequence for the budget:** direct every JEV call at classification only, and only where the
local decider *declines*. That is a minority of calls, so 20–40 requests cover weeks of real use
and land exactly on the hard cases a local model most needs labelled.

---

## 2. Track A — Speed (accuracy-neutral, do this first)

### A1. Build `resources_fts`

FTS5 external-content table over `resources`. Measured build: **2.0 s**, index **17.6 MB**.
External-content means no duplicated text — it points at `resources` rows.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS resources_fts USING fts5(
    name, description, body_text,
    content='resources', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
INSERT INTO resources_fts(rowid, name, description, body_text)
    SELECT id, name, coalesce(description,''), coalesce(body_text,'') FROM resources;
```

Rebuild triggers or a `scripts/refresh_nexus.py` stage keep it fresh after ingestion.

### A2. Rewrite `find_matching_skills` Pass 1

Replace the 5×`UNION ALL` (lines 556–577) with a bounded FTS5 candidate fetch:

```python
fts_query = " OR ".join(f'"{w}"*' for w in significant_words)
cand_ids = [r[0] for r in conn.execute(
    "SELECT rowid FROM resources_fts WHERE resources_fts MATCH ? "
    "ORDER BY bm25(resources_fts) LIMIT 300", (fts_query,))]
ph = ",".join("?" * len(cand_ids))
rows = conn.execute(
    f"SELECT name, description, body_text, resource_type, times_triggered, "
    f"times_correct, times_wrong, id, cluster FROM resources "
    f"WHERE id IN ({ph}) AND status='active'", cand_ids).fetchall()
```

Semantics to preserve (otherwise accuracy regresses):

- `"{w}"*` is a **prefix** query — `LIKE '%debug%'` matched `debugger`, prefix keeps that.
  Mid-word substring match is lost; measured candidate count must be compared, not assumed.
- Name-exact bonus (`name_match_bonus`) still runs in the Python scoring loop — untouched.
- `id_filter` / `filtered_ids` path must apply to both the FTS id set and the hydrate WHERE.
- 300 is a candidate cap; the existing scorer already truncates to `max_results`.

### A3. Rewrite `_resource_search`

Same shape: FTS5 for candidate ids, then a single `CASE WHEN name = ?` exact bonus for the raw
query. Delete the `resource_sections` LEFT JOIN + its 2 LIKE predicates (table is empty, adds nothing).

### A4. Kill per-call rework

| Item | Cost | Fix |
|---|---|---|
| `yaml.safe_load(nexus.yaml)` | 62 ms/route | `@lru_cache(maxsize=1)` keyed on mtime |
| `cluster_sizes` GROUP BY | every call | cache on the decider instance |
| `fixes_map` / `recent_failures` | every call | cache with a 60 s TTL |
| `N_total` COUNT(*) | every call | reuse from the same cache |

### A5. Acceptance for Track A

| Metric | Before | Target |
|---|---|---|
| `find_matching_skills` Pass 1 | 1,083 ms | **≤ 50 ms** |
| `_resource_search` | 1,030–1,264 ms | **≤ 60 ms** |
| Warm `route_for_task` | 3,676 ms | **≤ 400 ms** |
| Ranking parity | — | **≥95% top-5 overlap** on a 20-task golden set vs current output |

**Parity gate is mandatory.** If top-5 overlap drops below 95%, tune the prefix/candidate cap before
advancing. Speed that changes answers is not an optimisation.

---

## 3. Track B — JEV as a bounded teacher

### B1. Key placement — file, not chat, not only `.env`

The existing `jev_api_key()` already reads the file first. Write it there:

```bash
# you do this once, never in chat:
mkdir -p ~/.config/jev && printf '%s' 'YOUR_KEY' > ~/.config/jev/api_key
chmod 600 ~/.config/jev/api_key
```

Also fill `.env:140`. The file path is the reliable one — it is CWD-independent, which `.env` is not.

### B2. Fix the dotenv fragility

`core/config.py:8` calls bare `load_dotenv()`. Pin it:

```python
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
```

Otherwise the MCP server started from another CWD silently runs keyless and you will believe JEV is
"broken" when it was never loaded.

### B3. Extend JEV from cluster-only to three typed questions

`classify_task()` currently ignores JEV entirely. Add a `TaskTypeDecider` mirroring
`JevClusterDecider`, asking JEV for:

| Question key | Options | Replaces |
|---|---|---|
| `cluster` | 8 cluster names | already wired (leave as-is) |
| `task_type` | `coding, reasoning, agentic, architecture, devops, frontend, research, general` | keyword counter in `classify_task` |
| `action` | `search, enrich, audit, write, run, fix, sync, list` | implicit, feeds tool-set hints |

All three reuse `TypeSafeJevClassifier.classify()` — no new transport code.

### B4. Budget, cache, and — critically — call policy

New table:

```sql
CREATE TABLE IF NOT EXISTS jev_labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_hash     TEXT NOT NULL,
    task_summary  TEXT NOT NULL,
    question      TEXT NOT NULL,          -- cluster | task_type | action
    label         TEXT NOT NULL,
    confidence    REAL,
    local_label   TEXT,                   -- what the local path said (for agreement)
    local_conf    REAL,
    agreed        INTEGER,                -- 1/0, NULL if local declined
    source        TEXT DEFAULT 'jev',
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(task_hash, question)
);

CREATE TABLE IF NOT EXISTS jev_budget (
    day       TEXT PRIMARY KEY,
    calls     INTEGER NOT NULL DEFAULT 0,
    max_calls INTEGER NOT NULL DEFAULT 40
);
```

**Call policy — this is the economics of the whole track:**

```
if local decider answered with margin >= MARGIN and confidence >= ACCEPT_THRESHOLD:
        do NOT call JEV            # confident local answer; label it for free
elif task_hash in jev_labels:
        reuse cached label         # 0 API calls
elif calls_today >= max_calls:
        fall back to local         # budget exhausted
else:
        call JEV, store label + local_label + agreed
```

Only ambiguous/declined cases spend a request. On this corpus the local centroid decider declines on
`ambiguous_margin` (<0.03) and `below_accept` (<0.20) — a minority of tasks. That turns "10–20 calls"
into 10–20 *labelled hard cases* rather than 10–20 *arbitrary routes*.

### B5. Never on the hot path

JEV is a hosted round-trip (300–1,500 ms observed range for this class of endpoint; will be measured,
not assumed). Calling it synchronously inside `route_task` would make routing **slower**, cancelling
Track A.

Rules:

- **Shadow mode first** (`NEXUS_JEV_MODE=shadow`): route locally, call JEV **after** responding,
  on a worker, and record agreement only. Zero latency cost, zero behaviour change.
- **Enforce mode** (`NEXUS_JEV_MODE=enforce`): JEV's answer may override *only* when the local
  decider declined. Never overrides a confident local answer.
- Hard timeout 2 s; on timeout/refusal/budget-exhaustion fall back silently (the existing
  `TypedAnswer(None, ...)` decline path already does this).

### B6. Acceptance for Track B

| Metric | Target |
|---|---|
| Latency delta in shadow mode | **0 ms** on the user-facing path |
| Calls consumed per 100 routes | **≤ 15** (decline-only policy) |
| Hard cap enforced | yes — `jev_budget.max_calls`, verified by a 41st call being refused |
| Labels recorded | `jev_labels` grows by 1 per question per unique `task_hash` |
| Agreement baseline | measured and reported before any enforce flip |

---

## 4. Track C — Bake-in (the exit from JEV)

This is what makes "limited time" real instead of aspirational.

### C1. Corpus

`jev_labels` joined with local labels = a supervised set of (task text → label, agreed?) triples.
At 20 routes × 3 questions ≈ **60 labels across ~21 classes** — thin but sufficient for centroid
methods, which is what the local stack already uses.

### C2. Fit local prototypes

| Question | Method | Storage |
|---|---|---|
| `cluster` | refresh `cluster_definitions.centroid_embedding` from JEV-labelled tasks (weighted by agreement) | existing column, `scripts/seed_clusters.py` extension |
| `task_type` | one centroid per task_type from labelled tasks | new `intent_weights` rows (table exists, unused) |
| `action` | same | `intent_weights` |

No model training, no GPU, no new runtime dependency — centroids over the existing `IsoEmbedder`.
Consistent with the hardware ceiling.

### C3. Promotion gate

Do not turn JEV off on a feeling. Require **all**:

1. ≥ 20 distinct `task_hash` labels per question (or 3 per class, whichever is larger)
2. Holdout agreement **≥ 85%** local-vs-JEV on labels *not* used for fitting
3. `ambiguous_margin` decline rate **halved** vs the pre-JEV baseline
4. Track A parity gate already passed (so the comparison is not confounded by SQL changes)

### C4. Flip and demote JEV

```
NEXUS_DECISION_CLASSIFIER=local
NEXUS_CLUSTER_DECIDER=local
NEXUS_JEV_MODE=verify            # 5% sampled re-check, budget 5/day
```

JEV becomes a drift detector, not a dependency. If agreement falls below 80% for a week, the system
raises the budget back up automatically and re-labels — the student asks the teacher when it is
unsure. That is the self-correcting version of your original idea.

---

## 5. Phase order, effort, dependencies

| Phase | Work | Depends on | Effort | Blocking risk |
|---|---|---|---|---|
| **P1** | `resources_fts` + A2 + A3 | — | ~1 h | parity <95% → tune prefix/cap |
| **P2** | A4 caches + parity golden set | P1 | ~40 min | none |
| **P3** | Key file + B2 dotenv pin + B4 tables + shadow mode | — | ~1 h | key must be valid |
| **P4** | B3 task_type/action questions (shadow) | P3 | ~1 h | response shape unverified → already handled by `_extract_answer` |
| **P5** | Enforce mode on declines only | P3, P4, and ≥20 routes of shadow data | ~30 min | — |
| **P6** | C2 prototype fit + C3 gate eval | P5 | ~1.5 h | corpus may be too thin → keep labelling |
| **P7** | C4 flip + weekly drift cron | P6 gate passes | ~30 min | — |

Total ≈ **6 h** of focused work, split across sessions. P1–P2 need no API key at all, so speed ships
independently of JEV availability.

---

## 6. Data contracts (single source of truth)

**Env**

| Var | Values | Default | Effect |
|---|---|---|---|
| `NEXUS_DECISION_CLASSIFIER` | `auto\|jev\|local` | `auto` | classifier backend |
| `NEXUS_CLUSTER_DECIDER` | `auto\|jev\|local` | `auto` | cluster backend |
| `NEXUS_JEV_MODE` | `off\|shadow\|enforce\|verify` | `off` | call policy (**default off** until P5) |
| `NEXUS_JEV_BUDGET` | int | `40` | calls/day hard cap |
| `NEXUS_JEV_QUESTION_TYPE` | str | `noul` | unverified schema knob, already exists |
| `NEXUS_CLUSTER_ACCEPT` / `_MARGIN` | float | `0.20` / `0.03` | local decider thresholds |

**Files**

| Path | Purpose |
|---|---|
| `~/.config/jev/api_key` | key, 0600, CWD-independent (read by both `jev.py` and `decision_classifier.py`) |
| `state/index.sqlite` | `nexus` CLI FTS5 cache (already live) |
| `D:\nexus-data\db\nexus.db` | `resources_fts`, `jev_labels`, `jev_budget` |

---

## 7. Non-goals, and why (including Rust)

| Proposal | Verdict | Reason |
|---|---|---|
| **Rewrite NEXUS in Rust** | **No** | 83.6% of the 3.4 s is `sqlite3` C code inside a `LIKE` scan. `rusqlite` calls the same engine → ~1.0×. Embedding is already Rust (`tokenizers`) + C++ (ONNX). Rust would buy ~200 ms of CLI startup and 0% accuracy, at 3–6 weeks and 2–4 GB `cargo build` RAM on a thermally limited 8-vCPU laptop. |
| **JEV on the hot path** | No | Adds 300–1,500 ms per route. Shadow-only until Track C lands. |
| **Vector search to replace FTS5** | Not yet | `chunks_fts`/`sections_fts` are empty and `resource_sections` has 0 rows; FTS5 solves the measured cost today with zero new deps. |
| **Learned tool ranking from JEV labels** | No | 5,066 classes vs ~60 labels. Use `routing_log` outcomes (already exists, 16.4% closed). |
| **Auto-rewriting vault notes** | No | External generator owns the vault; sidecars only, per the P0–P8 rules. |

---

## 8. Rollback

Every phase is independently reversible:

- **P1/P2:** keep the old SQL in a `# legacy` comment block; revert = restore function body.
  `DROP TABLE resources_fts` removes the index with no data loss (external content).
- **P3/P4:** `NEXUS_JEV_MODE=off` → identical to today's local-only behaviour, zero code revert.
- **P5:** set `NEXUS_JEV_BUDGET=0` → immediate return to local.
- **P7:** `NEXUS_DECISION_CLASSIFIER=auto` + key present → JEV resumes instantly.

`jev_labels` and `jev_budget` are append-only and safe to keep forever — they are the audit trail of
what the teacher was asked and what the student believed.

---

## 9. Definition of done

1. Warm `route_for_task` **≤ 400 ms**, with **≥95% top-5 parity** on the 20-task golden set.
2. `resources_fts` exists, is refreshed by `refresh_nexus.py`, and both LIKE scans are gone.
3. `jev_labels` holds ≥20 labelled routes per question, produced at **≤15 calls/100 routes**.
4. Shadow-mode agreement measured and reported *before* any enforce flip.
5. After bake-in: local-vs-JEV holdout agreement **≥85%**, JEV demoted to `verify` at 5%.
6. `NEXUS_JEV_MODE=off` still yields correct routing — no hard dependency on the API.

---

## 10. Execution results (2026-11-18, P1–P4 executed)

Measured on the live DB (`5,066 resources`, `37 MB` of `body_text`) with the same 20-task
golden set captured *before* any code change, so parity is a real before/after.

### Speed

| Path | Before | After | Gain |
|---|---|---|---|
| `find_matching_skills` Pass 1 | 1,083 ms | ~30 ms | **~36×** |
| `_resource_search` | 1,030–1,264 ms | 151–166 ms | **~7×** |
| `route_task` warm (20-task mean) | **1,578 ms** | **231–274 ms** | **5.8–6.8×** |
| `route_for_task` v5 warm | ~3,676 ms | 465–530 ms | **~7×** |
| `yaml.safe_load` per route | 62 ms | ~0 ms (mtime-cached) | eliminated |

### Accuracy / parity (the gate that mattered)

| Check | Result |
|---|---|
| **top-1 identical** | **20/20 (100%)** |
| **top-3 identical** | **20/20 (100%)** |
| top-5 overlap | mean 96.0%, 19/20 ≥80% |
| `cluster` unchanged | **20/20** |
| `task_type` unchanged | **20/20** |

The single 60% task differs only at ranks 4–5, and the replacements are more on-topic
(`markdown-syntax-formatter` replacing `free-tools`). The old tail was largely noise from
generic tokens ("build", "review") matching everything.

### What was actually built

| File | Change |
|---|---|
| `resources_fts` (DB) | FTS5 external-content index over name/description/body, 2.2 s build |
| `server/router.py` | `_fts_candidates()` + Pass 1 rewrite; `classify_task` split into a JEV-gated wrapper over `_classify_task_local` |
| `server/router_v2/__init__.py` | `_lexical_candidates()`; `_resource_search` candidate prefilter; dead `resource_sections` JOIN + `body_text LIKE` scoring removed |
| `core/config.py` | `.env` pinned to repo root (CWD bug); `get_shared_capability_config` mtime-cached |
| `server/jev_gate.py` | **new** — daily budget, task-hash cache, shadow/enforce/verify modes, `jev_labels` ledger |
| `scripts/reindex_fts.py` | **new** — fingerprint-based maintenance for `resources_fts` |
| `scripts/jev_status.py` | **new** — spend, label corpus, agreement, bake-in readiness |
| `scripts/refresh_nexus.py` | new `fts` stage after `enrich` |
| `.env` | `NEXUS_JEV_MODE=off`, `NEXUS_JEV_BUDGET=40`, `NEXUS_JEV_MIN_CONF=0.55`, `NEXUS_JEV_VERIFY_PCT=5` |

### Verified JEV behaviour (without a key, so no spend occurred)

| Case | Result |
|---|---|
| `mode=off` | `consult()` returns `None` in **0.01 ms** |
| `mode=shadow`, no key | returns `None` in **3.5 ms**, daemon thread exits, **budget not consumed** |
| `mode=enforce`, confident local answer | returns `None` — refuses to pay for a second opinion it does not need |
| `jev_budget` rows after all tests | **0** |
| existing test suite | **38 passed** |

### Remaining, in order

1. **You:** put the key in `~/.config/jev/api_key` (CWD-independent) or fill `.env:140`.
   `.env` alone is not sufficient if the MCP server starts from another directory — that is
   why `core/config.py` now pins the path.
2. Set `NEXUS_JEV_MODE=shadow`, run normal work, and let `jev_labels` accumulate. Nothing
   changes for you; labels accrue for free on a background thread.
3. Watch `python scripts/jev_status.py` until each question shows 20 tasks / 3-per-class.
4. Only then `enforce` on declines, measure holdout agreement, then P6 prototype fit → P7 flip.

**Honest caveat on step 4:** the local centroid stack needs ~20 labelled tasks per question
to move meaningfully. That is a *lower* bound for a first fit, not proof of quality. The
85% holdout gate exists precisely because 20 samples is thin.

