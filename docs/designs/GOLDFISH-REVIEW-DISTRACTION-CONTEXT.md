# Goldfish review — DISTRACTION-RESISTANT-CONTEXT v1

Reviewer saw only the v1 draft (fresh context, no conversation history).
Verdict: REJECT — rewrite and resubmit.

| # | Severity | Finding | Fix demanded |
|---|---|---|---|
| 1 | BLOCKER | Schema cannot store what S1/S4 depend on (no claim_key, no notes ledger, no fetched_at) | extend DDL to match the mechanism |
| 2 | BLOCKER | Interrupted re-absorb silently deletes knowledge (no atomicity/recovery) | staged single-transaction re-absorb + consistency check |
| 3 | BLOCKER | Path-keyed supersession miss (refactors) "mitigated" by cluster keys defined nowhere | spec the fallback or record as accepted limitation + test |
| 4 | MAJOR | G5 "zero added latency" contradicts the ≤5 ms / ≤300 ms test row | restate as measured delta vs baseline |
| 5 | MAJOR | "verified \<pass-date\>" stamp asserts verification no stage performs | rename to absorbed-as-of or add a real verification pass |
| 6 | MAJOR | S2 resolves bug-ids via git log, but closure lives in trackers | split resolution sources; cap scanner cost; offline = flag-only |
| 7 | MAJOR | Google-analogue NOOP guard is theater — auto-invalidation bypasses it | honest policy + reversible invalidation audit |
| 8 | MAJOR | hygiene_flags PK holds one flag per resource; no FK/cascade rule | PK (resource_id, flag); re-absorb merge rule |
| 9 | MAJOR | S3 budget states no numbers; multi-intent hatch unspecified | cap value, per-intent formula, test row |
| 10 | MAJOR | Watchdog test unjudgeable (detour undefined; n=20 meaningless; no attribution rule) | define construction, keying, sample, attribution |
| 11 | MAJOR | S6 doesn't earn complexity; risk of belief poisoning | demote to shadow mode until proven |
| 12 | MAJOR | Three acceptance lines have no pass/fail boundary; "conflict set" undefined | bounded deltas vs P0 noise floor; define the metric |
| 13 | MAJOR | No live-migration or writer-concurrency story | ops section: WAL, writer ordering, migration pattern |
| 14 | MINOR | G1 "ever" unfalsifiable (stale-until-observed flows by design) | weaken to "known-superseded at pack time" |
| 15 | MINOR | Rollout ships stamps (S4) before enforcement (S1), undercutting the cost argument | S1 first; stamps debut absorbed-date only |
| 16 | MINOR | `rank − λ·age` re-ranker: no λ, no tuning, no test | specify or cut for v1 |

Top-3 changes demanded: (1) make the schema real + atomic re-absorb; (2) make the test
plan judgeable (one latency number, defined replays, bounded deltas); (3) cut vapor
(fallback keys, "verified" stamp, NOOP theater) and demote S6.
