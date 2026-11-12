# F7e — Transactional KG Writes (GraphDelta) · Data Systems

**Severity:** MEDIUM (design gap) · **Effort:** MEDIUM · **Blast radius:** KG consistency under concurrent/partial writes · **Status:** OPEN

---

## 1. Symptom (observed)

Multi-fact knowledge-graph updates are not applied atomically. A single logical change that touches
several edges/nodes can partially apply if an error occurs mid-way, leaving the graph in an
inconsistent intermediate state. The audit marked GraphDelta "PARTIAL." (Note: the *resource
reconcile* path already has a proper single-txn + LIFO-reverse model in
`server/local_harness/loader.py` — that pattern should be extended to KG fact writes.)

## 2. Root cause (mechanism)

KG fact writes are issued as independent statements/commits rather than as one bounded transaction
with validate-then-commit semantics. There is no staging area where a batch of edge/node deltas is
validated as a unit before being committed, and no LIFO rollback of already-applied deltas on
failure. Contrast the reconcile path (`loader.py:49`, one txn per call, reverses applied ops LIFO on
failure) — that discipline is not applied to graph fact mutations.

## 3. Scientific grounding

- **Gray, J.; Reuter, A. *Transaction Processing: Concepts and Techniques* (1993).** The canonical
  ACID treatment: a logical change must be **Atomic** (all-or-nothing) and leave the store
  **Consistent**. Multi-edge KG updates are the textbook case for a single transaction boundary.
- **Härder, T.; Reuter, A. "Principles of Transaction-Oriented Database Recovery." *ACM Computing
  Surveys* 15(4), 1983** — coined ACID; specifies write-ahead logging and the commit/abort protocol
  that a staging→validate→commit design should mirror.
- **Mohan, C. et al. "ARIES: A Transaction Recovery Method..." *ACM TODS* 17(1), 1992.** The
  write-ahead-log + LIFO undo model — precisely the "reverse applied ops LIFO on failure" pattern
  already proven in NEXUS's reconcile loader; generalize it to KG deltas.
- **SQLite WAL mode documentation** — the live DB already runs an active WAL; wrapping a delta batch
  in a single `BEGIN … COMMIT` gives atomicity for free, with `ROLLBACK` on any validation failure.

## 4. Engineering context

Introduce a **GraphDelta** abstraction: collect the set of node/edge mutations for one logical change,
open a single transaction, apply + validate (schema/temporal/provenance invariants from F7a/F7d), then
`COMMIT`; on any failure `ROLLBACK` (or LIFO-reverse if cross-store). This reuses the reconcile
loader's proven shape (`loader.py:49`) rather than inventing a new mechanism.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| New `server/graph_delta.py` | — | `GraphDelta.apply(conn)`: one txn, validate, commit/rollback | file header |
| KG edge/node writers | Independent statements | Route batched changes through `GraphDelta` | `RCA-F7e` marker |
| `server/local_harness/loader.py:49` | Reconcile txn (reference) | No change — cite as the pattern source | doc note |
| `db/schema.sql` | — | Ensure WAL + constraints support atomic batch | schema note |

## 6. Execution steps

1. ADR: adopt ACID single-txn KG writes; reference ARIES + the reconcile loader precedent.
2. Implement `GraphDelta` (accumulate → `BEGIN` → apply → validate invariants → `COMMIT`/`ROLLBACK`).
3. Migrate KG fact writers to build a `GraphDelta` per logical change.
4. Add a fault-injection test that forces a mid-batch error and asserts full rollback.

## 7. Verification (pass/fail)

- **PASS** iff injecting an error on the Nth mutation of an M-mutation delta leaves the graph exactly
  as before the delta (row counts + checksums unchanged) — atomicity proven.
- **PASS** iff a valid delta commits all M mutations and passes the F7a/F7d invariant checks.

## 8. Risk & rollback

- Low risk when built additively; the transaction boundary only *strengthens* consistency. Backup DB
  before rollout. Rollback = route writers back to direct statements (keep the old path until proven).

## 9. References

1. Gray, J.; Reuter, A. *Transaction Processing: Concepts and Techniques.* Morgan Kaufmann, 1993.
2. Härder, T.; Reuter, A. "Principles of Transaction-Oriented Database Recovery." *ACM Computing Surveys* 15(4):287–317, 1983.
3. Mohan, C.; Haderle, D.; Lindsay, B.; Pirahesh, H.; Schwarz, P. "ARIES: A Transaction Recovery Method Supporting Fine-Granularity Locking and Partial Rollbacks Using Write-Ahead Logging." *ACM Transactions on Database Systems* 17(1):94–162, 1992.
4. SQLite. "Write-Ahead Logging (WAL)." sqlite.org documentation.
