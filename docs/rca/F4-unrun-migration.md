# F4 — Migration Never Run · Data / ETL

**Severity:** LOW · **Effort:** LOW · **Blast radius:** None (WIP, never wired) · **Status:** OPEN

---

## 1. Symptom (observed)

`okf_migrate.py` exists in the quarantined `theater_backup/` directory but has **never executed**
against any database. The audit flagged it as an incomplete migration. It is a correctly-abandoned
work-in-progress for the dead OKF graph engine (F2), not a pending/blocked migration on live data.

## 2. Root cause (mechanism)

```python
# Downloads/nexus/theater_backup/okf_migrate.py:10  (untracked, orphaned)
#   intended to backfill the OKF graph engine's tables
```

- The migration targets the **dead** graph engine (F2), so it was never scheduled.
- It lives beside the orphaned engine in `theater_backup/` — no runner, no cron, no call site.
- No live table depends on it: the working OKF section path (`server/okf_sections.py`) uses the
  existing schema and needs no migration.

The correct disposition is **removal with F2**, not execution — running a migration for a
subsystem that is being deleted would be actively wrong.

## 3. Scientific grounding

- **Ambler, S.; Sadalage, P. *Refactoring Databases: Evolutionary Database Design* (2006)** —
  migrations should be **idempotent, versioned, and tied to a live schema need**. A migration with
  no consuming schema and no runner is not a migration; it is dead code. The book's "database
  refactoring" catalog assumes a change is *applied and verified*; an unapplied, unowned script
  fails that test.
- **Fowler, M. & Sato, P. "Evolutionary Database Design" (martinfowler.com, 2016)** — every schema
  change flows through an automated migration pipeline; ad-hoc scripts outside the pipeline are a
  smell.
- **Extreme Programming — YAGNI ("You Aren't Gonna Need It"; Beck, *Extreme Programming Explained*,
  1999)** — speculative infrastructure for a feature that was abandoned should be deleted, not
  carried. This migration is the canonical YAGNI artifact.

## 4. Engineering context

There is a **real, working** migration surface in the live tree (idempotent `ensure_*_schema`
helpers, e.g. `server/db.py`'s three-way `ensure_routing_log_schema`). `okf_migrate.py` is *not*
part of that surface. Do not resurrect it into the live migration path; remove it with the engine.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `Downloads/nexus/theater_backup/okf_migrate.py` | Orphaned WIP | Remove with F2 (archived by F1 first) | N/A |
| `server/db.py` (idempotent `ensure_*_schema`) | **Working** | No change — confirm this is the real migration surface | doc note |

## 6. Execution steps

1. Confirm no live code imports it: `grep -rn "okf_migrate" /home/roshan/nexus/server` → expect empty.
2. Confirm it is inside F1's archive tarball (it is, being under `Downloads/nexus`).
3. Remove with the F2 engine block (single deletion PR).

## 7. Verification (pass/fail)

- **PASS** iff `grep -rn okf_migrate /home/roshan/nexus` returns nothing after removal.
- **PASS** iff the live schema helpers still run idempotently (unit test for `ensure_routing_log_schema`
  green — see `tests/test_p0_p1_learning_contract.py`).

## 8. Risk & rollback

- None material — the script never ran, owns no live table. Rollback via F1 archive.

## 9. References

1. Ambler, S.; Sadalage, P. *Refactoring Databases: Evolutionary Database Design.* Addison-Wesley, 2006.
2. Fowler, M.; Sato, P. "Evolutionary Database Design." martinfowler.com, 2016.
3. Beck, K. *Extreme Programming Explained: Embrace Change.* Addison-Wesley, 1999 (YAGNI).
