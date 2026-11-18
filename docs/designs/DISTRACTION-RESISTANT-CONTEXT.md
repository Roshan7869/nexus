# Design: Distraction-Resistant Context Injection for NEXUS

**Status:** v2 (elephant revision after goldfish review — all 16 findings addressed)
**Reviews:** v1 rejected by goldfish (see GOLDFISH-REVIEW-DISTRACTION-CONTEXT.md)
**Parent plan:** `RESEARCH-EXECUTION-PLAN-2026-11-19.md` P5

## 1. Problem

NEXUS injects top-5 resource bodies + experience notes into a hosted LLM's context.
Three properties create a measured distraction hazard:

1. **Staleness.** Content comes from ~109 repos updated by swarm-pull; a note can say
   "use X, never Y" after Y became correct. Cosine cannot separate stale from current
   (AUROC 0.59; contradictions MORE similar than duplicates — 2606.26511), so the
   embedding stack cannot fix this by design.
2. **Cost, measured.** Google 913k-session study: unprompted action on stale inline
   references costs **+70% steps / +77% tokens / +105% wait** (fixed-effects,
   p<0.0001); 83% unrequested; 41% chased closed tickets; smarter models distract more.
3. **Competition, not length.** Effective context is bounded by competing statements,
   not tokens (2611.04169); a trailing distractor collapses accuracy below random
   (2411.12828, p≤1.6e-05); length alone costs 13.9–85% (2510.05381).

## 2. Design goals (G5 and G1 restated per review findings 4, 14)

- G1: **No content known-superseded at pack time** enters packed context. (Content
  that is stale but not yet observed upstream still flows — inherent to pull-based
  ingestion, bounded by pull cadence, not denied.)
- G2: Everything the model sees carries provenance the system actually has (no
  verification claims without a verifying stage).
- G3: Bounded competition per route; goal restated at the decision point.
- G4: Distraction telemetry observable (shadow mode in v1; belief-writes deferred).
- G5: Pack adds **≤5 ms p50** over the measured pre-change baseline (current pack-time
  ~0 ms; route p50 measured 198–216 ms). Hot-path delta vs baseline must stay within
  the P0 noise floor. Not "zero."
- G6: Every element tested through the P0 harness with bounded deltas.

## 3. Architecture: five pack-time stages + one shadow monitor

```
top-5 resources + notes
   [S1 SUPERSEDE]   drop rows with closed validity intervals (deterministic)
   [S2 FLAG-FILTER] drop hygiene-flagged rows (unresolved stale markers, stubs)
   [S3 BUDGET]      top-3 (+1 slot per extra detected intent, max 5); 1-line note excerpts
   [S4 STAMP]       per-passage header: repo @shortSHA · absorbed <date>  (no "verified")
   [S5 CHANNEL+GOAL] channel tags; caller goal restated AFTER all content
   hosted LLM
   [S6 WATCHDOG]    shadow-mode anomaly logging only — no belief writes in v1
```

### S1 — Supersession ledger

**Keys.** `resources`: (repo_name, file_path) — both already recorded at absorb time.
`experience_notes`: (resource_name, claim_key) where claim_key is the lesson's
subject line, extracted once at note-write time by the curation pass and stored. **No
cluster-level fallback keys in v1** — cross-file refactors are an accepted limitation
(§8), measured quarterly by the refactor test in §5.

**Semantics.** Absorb records `absorbed_at_commit` + `absorbed_at_date` on the row.
Re-absorb of a *changed* file: stage the replacement row first, then in ONE SQLite
transaction set `invalidated_at = now` on the old row and insert the new. Pack selects
`invalidated_at IS NULL`.

**Recovery.** Post-pull consistency check after every swarm-pull: any row with
`invalidated_at IS NOT NULL` whose (repo, file_path) has no valid replacement is
re-opened (`invalidated_at = NULL`, `suspect_reopen` logged). A killed pull leaves a
re-openable hole, not silent deletion. All invalidation writes go to an append-only
`invalidation_audit` table (reversible, inspectable).

**Policy honesty (finding 7):** auto-invalidation on upstream change is
trusted-by-default — that is the point of tracking upstream. The trace-verify-delete
guard (Google pattern, >95% acceptance) applies only to curation DELETE ops on
*experience notes*, never to upstream-driven invalidation. Stated as such.

### S2 — Stale-marker linter at ingestion

Scan changed bodies/notes for `TODO|FIXME(bug-id)`, issue URLs, `as of <date>` claims.
**Resolution sources split** (finding 6): date-claims resolve against git log at the
recorded SHA (local, free); bug-ids and issue URLs resolve via tracker API with a
**per-pull budget of 200 calls, exponential backoff, flag-only fallback** when budget
or network fails (a flagged note is excluded from packing, never deleted). Cost
capped; scanner offline-safe by construction.

### S3 — Competition budget (numbers, per finding 9)

- Pack **top-3** resource bodies by default.
- **+1 slot per additional detected intent** (from the router's existing L1 intent
  set), max 5 total.
- Notes excerpted to **1 line (~15 tokens)**; total packed statements hard-capped at
  **12** (resources + note lines combined).
- Goal restated after content (S5). Justification: 2611.04169 (competition is the
  budget); measured trims: −23–38% tokens at +1.2–1.4pp (2601.16746); 71→91.6% at
  −62.7% tokens (2606.10209).

### S4 — Provenance stamps (honest version)

Per-passage header (~20 tokens): `repo @shortSHA · absorbed <YYYY-MM-DD>`.
**No "verified" field** — no stage verifies body correctness, so none is claimed
(finding 5). **No recency re-ranking in v1** (finding 16): λ unspecified and
untunable without labeled data; stamps measured first; reordering is a v2 candidate
if conflict-probe tests show stamps insufficient.

### S5 — Channel tags + goal restatement

`<user_goal>`, `<retrieved_resource name=…>`, `<experience_note origin=…>`. Caller
goal restated in the final block after all content (recency advantage to the goal,
not the distractor). Standing prompt prose = exactly ONE paragraph, the
Anthropic-measured scope-guard adaptation; every other line must trace to an observed
distraction run_id or it doesn't ship (ETH 2602.11988: unearned context files reduce
success and add >20% cost).

### S6 — Watchdog (shadow mode, per finding 11)

EWMA + Mahalanobis over logged route telemetry (steps vs cluster median, token burn,
repeat-tool signature, CPU-embedding drift from intent). **v1: logs anomalies to
`watchdog_events` only — writes nothing to Thompson beliefs.** Belief-writes require
a separate design proving attribution and FP handling. Coverage honesty: abrupt
drift 0.66–0.86, loops 0.48–1.00 (2608.02464); slow drift is S1/S2's job.

## 4. Schema (complete, per findings 1, 8)

```sql
-- resources: additive columns (SQLite ALTER TABLE ADD COLUMN = metadata-only,
-- instant, no lock on the serving DB)
ALTER TABLE resources ADD COLUMN absorbed_at_commit TEXT;
ALTER TABLE resources ADD COLUMN absorbed_at_date   TEXT;
ALTER TABLE resources ADD COLUMN invalidated_at    TEXT;
CREATE INDEX idx_resources_validity ON resources(repo_name, file_path, invalidated_at);

-- experience-note ledger (claim_key extracted at write time)
CREATE TABLE IF NOT EXISTS note_ledger (
    note_id        INTEGER PRIMARY KEY,
    resource_name  TEXT NOT NULL,
    claim_key      TEXT NOT NULL,
    body_hash      TEXT NOT NULL,
    valid_from     TEXT NOT NULL,
    invalidated_at TEXT
);
CREATE INDEX idx_note_ledger_key ON note_ledger(resource_name, claim_key, invalidated_at);

-- hygiene flags: composite PK, multi-flag safe
CREATE TABLE IF NOT EXISTS hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag        TEXT NOT NULL,   -- stale_marker | stub | unresolved_claim | suspect_reopen
    detail      TEXT,
    found_at    TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);

-- append-only invalidation audit (reversible, inspectable)
CREATE TABLE IF NOT EXISTS invalidation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    reason TEXT NOT NULL,        -- upstream_change | curation_op | suspect_reopen
    invalidated_at TEXT NOT NULL,
    reopened_at TEXT
);

CREATE TABLE IF NOT EXISTS watchdog_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL,
    anomaly TEXT NOT NULL,
    ts TEXT NOT NULL,
    detail TEXT
);
```

**Re-absorb merge rule for flags:** re-absorb deletes + reinserts changed rows;
surviving flags are copied to the new row id inside the same transaction as the
validity flip. Flag rows for deleted resources are removed in the absorb path (no
FK cascade — handled manually).

## 5. Test plan (judgeable, per findings 10, 12)

**Conflict-set definition:** the frozen 30-probe set of "use X, never Y" note pairs
(stale claim vs current correction) — same set for S1/S4/S5 tests.
**Noise floor:** P0's ≥3-seed baseline σ; "within noise floor" = |delta| < σ at 95% CI.

| Test | Construction | Accept |
|---|---|---|
| Supersession | Inject 20 notes with known-closed intervals; route the 50-query golden set | 0 closed-interval notes in any pack (asserted programmatically); golden-set recall within noise floor |
| Refactor miss (accepted-limitation measure) | Rename the backing file of 10 known notes without updating them; route their 10 gold queries | Report miss rate; v1 records the number; quarterly gate ≤30% |
| Stamps | 30-probe conflict set, A/B headers on/off | ≥90% follow current version; binomial CI reported |
| Budget | 100 replayed queries, A/B top-3-trim vs top-5-full, ≥3 seeds | ≥25% fewer steps/tokens (95% CI excluding 0); conflict-set accuracy within noise floor |
| Multi-intent | 20 two-intent golden queries | slots = 4 (3+1) detected on ≥80%; both intents' gold resources packed ≥90% |
| Scope-guard | 50 tasks seeded with adjacent-unrequested lesson, A/B paragraph | unrequested expansions −50% (95% CI excluding 0); task success within noise floor |
| Pack latency | 500 routes, pack-time delta | ≤5 ms p50 vs baseline; route p50 within P0 noise floor |
| Watchdog (shadow) | 100 constructed detour replays + 300 healthy | report detection/FPR only, no pass/fail in v1; n=100 for a reportable CI |

**Detour construction:** take a healthy logged route; after step 2 inject a scripted
4-step sequence invoking an unrelated resource's tool (different cluster); watchdog
must flag it. Healthy = unmodified replays.

## 6. Ops (per finding 13)

- DB: WAL mode (already on); all writers serialize through the existing single-writer
  absorb/curation paths; the pack-time read path is read-only.
- Migration: additive columns + new tables only — SQLite `ADD COLUMN` is
  metadata-only; backfill `absorbed_at_*` for existing rows from vault git history in
  one offline pass at deploy.
- Failure semantics: S1→S2 are filters — a stage failure passes the pack through
  unfiltered with a logged warning (fail-open: pre-change behavior was exactly "no
  filtering"); S4/S5 fail soft (stamp/channel omitted).

## 7. Rollout order (per finding 15)

**S1 (ledger + atomic re-absorb) FIRST**, then S4 (stamps — absorbed-date only),
then S2 (linter), S3 (budget), S5 (channels), S6 (watchdog, shadow). Enforcement
before advice.

## 8. Risks (accepted-limitation register, no vapor)

- Cross-file refactors defeat path-keyed supersession — **accepted limitation in
  v1**, measured quarterly by the refactor test; content-hash fallback keys deferred
  to v2 unless miss rate >30%.
- Stamps add ~100 tokens/route — bounded by S3's measured cuts; S3 ships after S1.
- Watchdog FP risk — neutralized in v1 by shadow mode (no belief writes).
- Multi-intent slot inflation on mis-detected intents — bounded by the hard
  12-statement cap regardless of detected intent count.
