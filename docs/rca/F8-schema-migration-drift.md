# F8 — Schema Migration Drift & Broken Migration Registry · Release Engineering / Data Systems

**Severity:** HIGH (blocks correct execution of F7a/F7d/F7e) · **Effort:** MEDIUM · **Blast radius:** Every schema change · **Status:** OPEN

---

## 1. Symptom (observed)

The temporal-fact (F7a), provenance (F7d), and atomic-write (F7e) remediations all assume a single,
authoritative schema surface (`db/schema.sql`) that can be edited to evolve the `resource_edges`
table. That surface does not exist. The live schema has silently diverged from every checked-in
definition, and the tooling meant to track applied migrations records almost nothing.

## 2. Root cause (mechanism)

Three compounding data-systems defects, none captured by the existing F-docs.

**(N1) Migrations are split across two disjoint directories.** There are two migration homes with
**zero** overlapping filenames and no unifying ordering:

- `migrations/` → `008, 010, 011, 012, 013, 014, 015, 016`
- `db/migrations/` → `001, 006, 007*, 017, 020, 021`

Core KG tables are scattered between them: `resource_edges` lives in
`db/migrations/007_graph_topology.sql:2` (indexes in `007_a_resource_edges_indexes.sql`), while
`kg_communities` / `kg_community_membership` live in `migrations/012_kg_communities.sql`. Neither is
in `db/schema.sql` (16 base tables only, 256 lines). There is no single "apply in order" home.

**(N2) The `schema_migrations` registry is effectively dead.** The bookkeeping table exists but
holds exactly **one** row (`010_fts5_outcome_log`), despite ~20 migration files and live tables from
007, 012, etc. Migrations are applied by **hardcoded one-off scripts** — `run_migration_007.py`
hardcodes a single path; `run_migrations_007.py` hardcodes `007_a`–`007_e` — rather than a generic
runner that scans a directory, diffs against `schema_migrations`, and applies the pending set in
order. Applied state is therefore unknowable from the registry: you cannot tell which migrations ran.

**(N3) The live `resource_edges` table has drifted from every tracked definition.** The live table
(21,792 rows) carries `co_trigger_count INTEGER NOT NULL DEFAULT 0` and `edge_type TEXT NOT NULL
DEFAULT 'related_to'` (plus FKs to `resources(id) ON DELETE CASCADE` and
`UNIQUE(source_id,target_id,edge_type)`) — columns/defaults that appear in **no** `.sql` migration and
**no** `CREATE`/`ALTER` in the Python tree. The characterized live DDL is now pinned in
`db/migrations/BASELINE.sql` (F8 §6 step 1, done). Reads/inserts of `co_trigger_count` exist in
`server/learning.py`, `server/kg_service.py:107,120,128`, and `scripts/densify_kg.py`, but nothing
in-repo *creates* the column. It was introduced by an **untracked, out-of-band ALTER**. Three
definitions now disagree: migration 007, the test fixtures, and the live table — and the live table
matches none of them.

## 3. Scientific grounding

- **Ambler, S.; Sadalage, P. *Refactoring Databases: Evolutionary Database Design* (2006)** — the
  canonical treatment of schema evolution: every change is a small, ordered, versioned migration
  applied through one mechanism against a recorded change log. N1/N2 violate the single-mechanism and
  recorded-log invariants directly.
- **Fowler, M. "Evolutionary Database Design" (martinfowler.com, rev. 2016)** — establishes that the
  database schema must be under the same version control discipline as code, with an automated
  migration runner and a schema-version table as the source of truth for "what has been applied."
  N2 has the table but abandons it.
- **Humble, J.; Farley, D. *Continuous Delivery* (2010), ch. 12 "Managing Data"** — argues DB changes
  must be scripted, idempotent, and applied identically across environments; ad-hoc/manual ALTERs
  (N3) are the named anti-pattern that produces environment drift no artifact can reproduce.
- **Feathers, M. *Working Effectively with Legacy Code* (2004)** — "characterization" of an
  undocumented live schema (dumping the real DDL and pinning it in a test) is the prescribed first
  step before any safe change; F7a/F7d cannot proceed until the live shape is characterized.

## 4. Engineering context

F7a and F7d both proposed editing `db/schema.sql` to add columns to `resource_edges`. That edit would
have been a no-op against the live table (which schema.sql never creates) and would have deepened the
drift. The correct target is the migration lineage plus an idempotent ALTER of the live table. But
even that is unsafe while N1/N2 stand: with no reliable applied-state record and two migration dirs,
a new `022_*` cannot know its predecessors ran. This doc is therefore a **prerequisite** for F7a,
F7d, and F7e — they should cross-reference it rather than touch schema directly.

Recommended sequencing:

- **Characterize first:** dump the live DDL for `resource_edges` (and other KG tables) and commit it
  as the authoritative baseline, closing N3's "which definition is real?" ambiguity.
- **Consolidate:** pick one migration home (`db/migrations/`), renumber/move the `migrations/` files
  into a single monotonic sequence, and backfill `schema_migrations` to reflect what is actually live.
- **One runner:** replace the `run_migration_00N.py` one-offs with a single idempotent runner that
  scans the dir, diffs `schema_migrations`, and applies pending migrations transactionally (ties into
  F7e's atomic-write work).

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `migrations/` (008–016) + `db/migrations/` (001–021) | Two disjoint dirs, no shared ordering | Consolidate into one monotonic sequence under `db/migrations/` | `RCA-F8` marker |
| `schema_migrations` (live) | 1 row (`010_fts5_outcome_log`) | Backfill to reflect all applied migrations; make it the applied-state SoT | migration + doc |
| `run_migration_007.py`, `run_migrations_007.py` | Hardcoded one-off appliers | Replace with one dir-scanning idempotent runner (diffs `schema_migrations`, applies in order, one txn) | `RCA-F8` marker |
| `db/migrations/007_graph_topology.sql` (`resource_edges`) | Missing `co_trigger_count`; `edge_type` has no default | Reconcile with live DDL; new `022_*` codifies the drift as tracked columns | `RCA-F8` marker |
| `db/schema.sql` | 16 base tables; no KG/edge tables | Add a header note that KG/edge tables live in `db/migrations/`, not here | inline |
| `docs/rca/F7a`, `F7d`, `F7e` | Assume clean `schema.sql` edit | Cross-reference F8 as prerequisite | doc note |

## 6. Execution steps

1. ✅ **DONE** — Characterized live DDL committed to `db/migrations/BASELINE.sql` (via Python
   `sqlite3`; the `sqlite3` CLI is **not installed** on this host — use the Python module). Confirms
   `resource_edges` has `co_trigger_count NOT NULL DEFAULT 0`, `edge_type … DEFAULT 'related_to'`,
   FKs, `UNIQUE(source_id,target_id,edge_type)`, and index `idx_edges_created_at`.
2. Author `022_reconcile_resource_edges.sql` that codifies the live columns (`co_trigger_count`,
   `edge_type DEFAULT 'related_to'`) as tracked, using `ADD COLUMN`-guarded idempotent statements.
3. Consolidate the two migration dirs into one monotonic sequence; record the mapping.
4. Write a single idempotent runner: scan dir → read `schema_migrations` → apply pending in order in
   one transaction → insert a row per applied file. Delete the `run_migration_00N.py` one-offs.
5. Backfill `schema_migrations` with every migration already reflected in the live DB.
6. Add `RCA-F8` markers; add prerequisite cross-refs in F7a/F7d/F7e.

## 7. Verification (pass/fail)

- **PASS** iff a fresh DB built by running the consolidated migrations in order produces a
  `resource_edges` schema byte-identical to the committed live baseline (N3 closed).
- **PASS** iff `schema_migrations` row count equals the number of migration files applied to build
  that fresh DB (N2 closed).
- **PASS** iff re-running the runner against an up-to-date DB applies **zero** migrations and mutates
  nothing (idempotence).
- **FAIL** if any `resource_edges` column exists live that no committed migration creates.

## 8. Risk & rollback

- MEDIUM risk: renumbering/moving migrations can break environments that keyed off old paths. Mitigate
  by keeping the runner backward-compatible (treat old + new filenames as already-applied via the
  backfill) and by taking a DB backup before the first consolidated run. Rollback = restore backup +
  revert the migration-dir move. The characterization baseline (step 1) is non-destructive and can
  land independently as an immediate risk-reducer.

## 9. References

1. Ambler, S. W.; Sadalage, P. J. *Refactoring Databases: Evolutionary Database Design*. Addison-Wesley, 2006.
2. Fowler, M. "Evolutionary Database Design." martinfowler.com, 2003 (rev. 2016).
3. Humble, J.; Farley, D. *Continuous Delivery: Reliable Software Releases through Build, Test, and Deployment Automation*. Addison-Wesley, 2010 (ch. 12, "Managing Data").
4. Feathers, M. *Working Effectively with Legacy Code*. Prentice Hall, 2004.
