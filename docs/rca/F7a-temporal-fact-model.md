# F7a — Temporal Fact Model · Knowledge-Graph Architecture

**Severity:** HIGH (design gap, not a defect) · **Effort:** HIGH · **Blast radius:** KG memory quality · **Status:** OPEN

---

## 1. Symptom (observed)

The knowledge graph stores facts as **timeless** rows. When a fact changes (a resource's cluster is
reassigned, a belief supersedes an earlier one, a project fact is updated) the old assertion is
either overwritten (history lost) or left alongside the new one (contradiction, no way to know which
is current). There is no `valid_at` / `invalid_at` bitemporal envelope. The audit's "temporal:
OPEN" verdict is accurate.

## 2. Root cause (mechanism)

Facts and edges (`resource_edges`, belief rows) carry only `created_at`/`updated_at` timestamps —
*transaction* time — but no **validity interval** (when the fact is true in the world). Consequences:

- Queries cannot ask "what did we believe *as of* date X" (no time-travel).
- Superseded facts cannot be retired without deletion, so retrieval mixes stale + current.
- Recency cannot weight ranking, because "recency of assertion" ≠ "currently valid."

## 3. Scientific grounding

- **Rasmussen, P. et al. "Zep: A Temporal Knowledge Graph Architecture for Agent Memory." arXiv,
  2025 (Graphiti).** The reference architecture for exactly this problem: every edge carries
  `valid_at` / `invalid_at`, superseding an old edge sets its `invalid_at` rather than deleting it,
  and queries are time-scoped. NEXUS should adopt the bitemporal edge envelope directly.
- **Snodgrass, R. *Developing Time-Oriented Database Applications in SQL* (1999).** Foundational
  bitemporal modeling: separate **valid time** (when true in reality) from **transaction time**
  (when recorded). Prescribes the two-interval schema and the "close-the-old, open-the-new" update.
- **Allen, J. F. "Maintaining Knowledge about Temporal Intervals." *CACM* 26(11), 1983.** Allen's
  interval algebra (before/overlaps/during/…) gives the query semantics for reasoning over the
  validity intervals once they exist.
- **Jensen, C.; Snodgrass, R. "Temporal Data Management." *IEEE TKDE* 11(1), 1999** — consolidates
  the bitemporal query model used for time-travel queries.

## 4. Engineering context

Adopt Graphiti's minimal envelope: add `valid_at TIMESTAMP` and `invalid_at TIMESTAMP NULL` to the
edge/fact tables; treat `invalid_at IS NULL` as "currently valid." Fact updates become
close-then-insert (set old `invalid_at = now`, insert new with `valid_at = now`). Retrieval filters
on `invalid_at IS NULL` by default and can time-travel with `valid_at <= T AND (invalid_at IS NULL OR invalid_at > T)`.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `db/migrations/007_graph_topology.sql` (`resource_edges`) + new migration `022_temporal_edges.sql` | `created_at`, `updated_at`, `co_trigger_count` — no validity interval (**not** in `db/schema.sql`; live table drifted from 007 — see F8) | Add `valid_at`, `invalid_at` via new migration + idempotent ALTER; index `(source_id, invalid_at)` | `RCA-F7a` marker |
| `server/db.py` (`ensure_*_schema`) | — | Idempotent ALTER to backfill columns (`invalid_at NULL`, `valid_at = created_at`) | `RCA-F7a` marker |
| Edge/fact writers (KG ingest paths) | Insert/overwrite | Close-then-insert on supersede | `RCA-F7a` marker |
| `server/router_v2/fusion.py` | No recency weight | Optional recency decay using `valid_at` | `RCA-F7a` marker |

## 6. Execution steps

1. Design the bitemporal envelope; write an ADR in `docs/` referencing Graphiti + Snodgrass.
2. Add idempotent schema migration (backfill `valid_at = created_at`, `invalid_at = NULL`).
3. Convert fact-update code paths to close-then-insert.
4. Default all reads to `invalid_at IS NULL`; add a time-travel query helper.
5. (Optional) wire recency decay into fusion ranking.

## 7. Verification (pass/fail)

- **PASS** iff superseding a fact leaves the old row with `invalid_at = now` and a new current row;
  a default query returns only the new one, and an as-of query at an earlier T returns the old one.
- **PASS** iff existing reads are unchanged for facts that never changed (backfill correctness).

## 8. Risk & rollback

- Schema migration on a 624 MB DB — take a DB backup first (`cp nexus.db nexus.db.bak`). Additive
  columns are low-risk; rollback = drop the two columns / restore backup.

## 9. References

1. Rasmussen, P. et al. "Zep: A Temporal Knowledge Graph Architecture for Agent Memory." *arXiv:2501.13956*, 2025.
2. Snodgrass, R. T. *Developing Time-Oriented Database Applications in SQL.* Morgan Kaufmann, 1999.
3. Allen, J. F. "Maintaining Knowledge about Temporal Intervals." *Communications of the ACM* 26(11):832–843, 1983.
4. Jensen, C. S.; Snodgrass, R. T. "Temporal Data Management." *IEEE Transactions on Knowledge and Data Engineering* 11(1), 1999.
