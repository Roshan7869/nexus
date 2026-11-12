# NEXUS — Execution Level (molecular)

Status: DRAFT v1 · 2026-09-13 · HEAD `c8782ad`
Parent: `docs/design/README.md` · Siblings: `00-HLD.md` … `05-SITES-REGISTRY.md`

Each parent design doc gets one execution doc here. The parent says **what** and **why**; this set says
**which command, in which order, with what expected output, and how to undo it**.

| execution doc | elaborates | produces |
|---|---|---|
| [`exec/README.md`](README.md) | this file | the step grammar + the protocol |
| [`exec/00-EXEC-HLD.md`](00-EXEC-HLD.md) | `00-HLD.md` | every architectural decision → a falsifiable gate |
| [`exec/01-EXEC-LLD.md`](01-EXEC-LLD.md) | `01-LLD.md` | every code block → file, insert point, test |
| [`exec/02-EXEC-MACRO.md`](02-EXEC-MACRO.md) | `02-PLAN-MACRO.md` | every phase exit gate → a runnable script |
| [`exec/03-EXEC-TICKETS.md`](03-EXEC-TICKETS.md) | `03-PLAN-MICRO.md` | all 24 tickets → atomic steps |
| [`exec/04-EXEC-FEASIBILITY.md`](04-EXEC-FEASIBILITY.md) | `04-FEASIBILITY.md` | every measured claim → its re-measurement |
| [`exec/05-EXEC-SITES.md`](05-EXEC-SITES.md) | `05-SITES-REGISTRY.md` | site onboarding → end-to-end steps |
| [`exec/06-CORRECTIONS.md`](06-CORRECTIONS.md) | all of the above | **13 citation errors found on re-verification** |
| [`exec/07-PLAN-GRAPH.md`](07-PLAN-GRAPH.md) | all of the above | the plan as a **queryable graph**, joined to the code graph |

> **Read `06-CORRECTIONS.md` before any step below.** Three premises in the parent set are false, and one
> of them (I3) inverted a headline "CRITICAL" finding.

---

## 1. The atomic step grammar

Every step is written in exactly this six-field form. A step without all six is not a step.

```
### <TICKET>.<N> — <imperative title>
WHERE   the exact file and line/insert-point of the change
CHECK   a command that MUST pass before you change anything
DO      the exact edit, or the exact command
RUN     how the change is activated (restart, migrate, re-sync)
ASSERT  a command whose LITERAL expected output is given
UNDO    the exact reversal
```

**`CHECK` is the point.** Every step begins by proving its own precondition on the machine it is about
to change. This is what makes the set safe against drift: the parent docs cite line numbers as of
`c8782ad`, and citations drift — this pass found **13** of them wrong (`06-CORRECTIONS.md`). A `CHECK`
that fails means the step is stale, so **fix the check, not the symptom**.

**`ASSERT` gives literal expected output, not a description.** `expect: 200`, `expect: 0`,
`expect: 13`. If the output differs, stop.

---

## 2. Environment contract

```
repo        /home/roshan/nexus
interpreter ./venv/bin/python           (never system python3 — no deps, no pip)
db          db/nexus.db   = 538,640,384 B   (538.6 MB) · single writer
daemon      ONE process on 127.0.0.1:8000  (supervisord-managed; :8080 and :9999 are dead)
migrations  TWO dirs exist — migrations/ (8) and db/migrations/ (11) — see 06-CORRECTIONS C9
tests       ./venv/bin/python -m pytest   (baseline: 2 failed / 374 passed — A4-1 fixes this)
```

### S0 — Discover the restart mechanism (do this once, before any step)

```bash
CHECK   ps -o pid,cmd -C python3 | grep -c nexus_http_daemon
DO      supervisorctl status 2>/dev/null || ps aux | grep -E "supervisor|nexus_http_daemon" | grep -v grep
ASSERT  you can NAME the exact restart command (e.g. `supervisorctl restart nexus`)
UNDO    n/a — read-only
```

Do **not** assume a restart command. Record the one this step returns and use it as `RUN` from then on.
Every "restart the daemon" below is a placeholder for that value.

### S1 — Establish the green baseline (mandatory before A2, A3, B, C)

```bash
CHECK   ./venv/bin/python -m pytest -q 2>&1 | tail -3
DO      ./venv/bin/python -m pytest -q > /tmp/baseline_before.txt 2>&1; echo "exit=$?"
ASSERT  exit=1 and `tail -1` shows `2 failed, 374 passed` (until A4-1 lands, then exit=0)
UNDO    n/a — record only
```

Save `/tmp/baseline_before.txt`. Every later ticket compares against it. A4-1 changes this baseline to
green; after A4-1, `exit=0` is the new expectation.

---

## 3. Dependency DAG (execution order, not ticket order)

```
S0 ─ S1 ─┬─ A4-1 ─┬─ A2-0 ─[gate]─ A2-1        (A2-1 only if pool_size is the blocker)
         │        └─ A3-1 ─────────────┐
         ├─ A1-1 ── A1-2               │
         ├─ A1-3 (RE-SCOPED: not a bug — see C3)
         └─ B3-1, B1-1                 │
                                       │
B2-1 (batched, independent) ───────────┤
                                       │
P3 DECISION ──YES──> C0-1 ── C1-1 ──┬─ C2-1 ──┐
                                    └─ C3-1 ──┴─ C4-1 ── I5-1 ── I5-2
D1-1 ─ D2-1 ─ D3-1   (parallel from S1)
I2-1, I3-1 (RE-SCOPED: see C7/C8), I4-1   (parallel)
```

**Rules the DAG encodes:**

1. **A4-1 first, always.** It is the baseline. Without it, A2/A3/B/C changes are unmeasurable.
2. **A2-0 gates A2-1.** If `confidence` is the blocker, A2-1 must not be implemented — the path is
   dead and deletion is the fix (a B-track action).
3. **A3-1 gates C3-1.** The Fiber's DB projection cannot be built on two disagreeing columns.
4. **C0-1 gates C1-1 gates C2-1/C3-1 gates C4-1.** Strictly serial — each consumes the previous
   primitive.
5. **I5-1 requires C1-1.** Site disposal needs the registry.

---

## 4. Session batching

The 300 s tool cap killed earlier work (`04-FEASIBILITY.md` §5.3). Rules:

| work | batch size | why |
|---|---|---|
| `pytest` full suite | 1 | ~2-4 min; do not chain two suites in one call |
| B2 quarantine | **20 files / run** | the prior single-shot attempt died at the cap |
| graph queries | always `LIMIT` + start node | two subagents were killed at 600 s on unbounded scans |
| migrations | 1 per run, against a **copy** first | single-writer DB |

**Every step's `ASSERT` must complete inside 300 s.** If it cannot, split the step.

---

## 5. Change discipline (non-negotiable)

1. One ticket per commit. Commit message contains the `UNDO` line verbatim.
2. `git status --short` clean before starting a ticket; `git diff --stat` reviewed before committing.
3. Full `pytest`, not a single file, before and after.
4. No new `# type: ignore`. No new `except Exception: pass`.
5. Routing-path change → record `router_ab_suite` top-1 agreement before **and** after.
6. Live daemon or DB → `OPS` path: stop the writer, work on a copy, verify, then swap.
7. **A step whose `CHECK` fails is a stop.** Report the drift; do not "adjust" the step to match
   reality without recording it here.

---

## 6. What this set deliberately does not do

- **No new design.** Every step traces to a parent ticket. If a step needs a design decision that is
  not in the parent set, the step is not ready — the parent set is incomplete.
- **No in-flight swap semantics for C4.** `dsh.md` §4 marks it PARTIALLY SPECIFIED; `04-FEASIBILITY.md`
  §5.4 classifies it undefined. **It must be decided and documented before C4-1**, not inherited
  (see `02-EXEC-MACRO.md` §P4-G3).
- **No `G_MODULE`, no CORDIS proof obligations.** Verified infeasible (`04-FEASIBILITY.md` §5.1, §5.2).
