# NEXUS Design Docs — index

Adapting CORDIS and adjacent research (DSH, HY) into NEXUS, with per-section HLD/LLD and
per-integration macro/micro plans.

Status: DRAFT v1 · 2026-09-13 · HEAD `c8782ad`

| doc | what it is | read it when |
|---|---|---|
| [`00-HLD.md`](00-HLD.md) | High-level design: system context, the two architectural defects, the 8 sections, CORDIS mapped to architecture, the decision gate | first, always |
| [`01-LLD.md`](01-LLD.md) | Low-level design: per-section module design, real interfaces, data model, the ~30-LOC disposer kernel | before writing code |
| [`02-PLAN-MACRO.md`](02-PLAN-MACRO.md) | Macro phases P0–P6 with entry/exit gates, per integration (skills / agents / MCPs / harness agents / sites) | planning a phase |
| [`03-PLAN-MICRO.md`](03-PLAN-MICRO.md) | 24 atomised tickets: owner · risk · inputs · outputs · verification · rollback | picking up a ticket |
| [`04-FEASIBILITY.md`](04-FEASIBILITY.md) | Measured feasibility: latency, storage, complexity; what is verified infeasible | before committing to an item |
| [`05-SITES-REGISTRY.md`](05-SITES-REGISTRY.md) | **You edit this.** Harness sites per use case; `config/harness_sites.yaml` spec | adding a site |

## Execution level (molecular)

Each doc above has a companion in [`exec/`](exec/README.md) that turns it into commands with expected
output and an undo. Parent = what and why. `exec/` = which command, in which order, proving what.

| execution doc | elaborates |
|---|---|
| [`exec/README.md`](exec/README.md) | the step grammar, environment contract, dependency DAG, session batching |
| [`exec/00-EXEC-HLD.md`](exec/00-EXEC-HLD.md) | every architectural decision → a falsifiable gate |
| [`exec/01-EXEC-LLD.md`](exec/01-EXEC-LLD.md) | every code block → file, insert point, test |
| [`exec/02-EXEC-MACRO.md`](exec/02-EXEC-MACRO.md) | every phase exit gate → a runnable script, with kill conditions |
| [`exec/03-EXEC-TICKETS.md`](exec/03-EXEC-TICKETS.md) | all 24 tickets → atomic steps |
| [`exec/04-EXEC-FEASIBILITY.md`](exec/04-EXEC-FEASIBILITY.md) | every measured claim → its re-measurement |
| [`exec/05-EXEC-SITES.md`](exec/05-EXEC-SITES.md) | site onboarding → end-to-end steps |
| **[`exec/06-CORRECTIONS.md`](exec/06-CORRECTIONS.md)** | **13 citation errors found on re-verification — read this first** |

`config/harness_sites.yaml` now exists as a real, parse-verified file with the `getdesign` slot.

Upstream evidence and the prior analysis live in `_research/`: `IMPLEMENTATION_PLAN.md`,
`graph_design.md`, `gaps.md`, `gap_research.md`, `plan_review.md`, `dsh.md`, `hy.md`,
`cognee_integration.md`, `delete_list_recovered.*`.

---

## The three things that matter most

**1. The served surface is not the documented surface.** `:8000` serves the ops/IDE API (32 routes);
`/v1/models` and `/v1/chat/completions` return 404; `/v1/ops` is served but slow (1.35–2.07 s, **not** a
hang — re-verified); `.mcp.json:33` points
`NEXUS_DAEMON_URL` at dead `:8080`. Fix before building anything above it. → `A1-1`, `A1-2`, `A1-3`

**2. There are two parallel backend-fusion registries, and the code declares which one wins.**
`backend_protocol.py:142`: *"New code should prefer `fuse_registered()` with a `BackendRegistry`."*
13 backends still use the old `Fuser`; the new registry has zero production users. Track C finishes
the declared migration and adds disposal. It does not add a framework. → `C0-1`, `C1-1`

**3. The plan's own premise was contradicted by measurement.** `_research/IMPLEMENTATION_PLAN.md`
argued G_COLDSTART (the fast path starving the cold tier) was the critical defect. Measurement: **0 of
8 realistic queries take the fast path at all.** The defect is latent, not active — cold resources are
being selected (`cold_k` = 2–3 on every query). Two conflicting actions follow, so `A2-0` measures
which gate blocks the short-circuit **before** `A2-1` is implemented. If the gate shows the path is
dead, delete it.

**4. Re-verification overturned the "MCP crisis".** `I3` was described as CRITICAL — 8.5% MCP health,
"no removal path". The removal path (`delete_tool`, `mcp_sync.py:155`) **exists and has been working**,
which is why 301 rows are archived. The 8.5% measures success, not rot. The real gap is servers
**absent** from the sync loop never being reconciled. Full list: `exec/06-CORRECTIONS.md`.

---

## Corrections to the earlier research (all live-verified)

| research claim | verdict |
|---|---|
| "`times_triggered > 0` filters out 743 resources" | the queries **annotate** `hot`, they do not filter; the real lockout is `_short_circuit` bypassing the cold tier |
| "three copies of one API" | one served **ops** surface + two unserved forks of a **different** (OpenAI) surface |
| "659 lifecycle NULLs" | 659 is NULLs *among archived rows*; **2,521** rows need a value |
| "the model reloads per call" *(my own draft claim)* | **false** — singleton verified correct, one load per process; apparent repetition was stdout/stderr interleaving |

Full detail: `_research/IMPLEMENTATION_PLAN.md` PART 0 and `04-FEASIBILITY.md` §1.2, §2.

---

## Measured baseline (this machine, today)

```
route latency      min 1,213 ms · median 1,633 ms · mean 1,651 ms (model warm)
                   worst observed 12,568 ms
short-circuit      0 / 8 realistic queries  -> 100% full_pipeline
resources          3,255 rows · 1,862 active (57%)
  skills           529 / 1,296 active   (767 dead)
  MCPs              28 /   329 active   (301 dead)   <- CRITICAL
  agents           162 /   186 active
DB                 538.6 MB total, of which `resources` is 22.2 MB
                   ~430 MB (80%) is ingestion chunks + a 118 MB embedding cache
ports              :8000 live · :8080 dead · :9999 dead
graph              .gitnexus/lbug  8,944 nodes / 15,688 edges · Module 0 · Interface 0 · ACCESSES 2
```

---

## Reading order

1. `00-HLD.md` §2–§3 — the two defects.
2. `04-FEASIBILITY.md` §2 — the measurement that re-scoped the plan.
3. `02-PLAN-MACRO.md` P0 → P3 — and answer the decision at P3.
4. `03-PLAN-MICRO.md` — start at `A4-1`, then `A1-1`.

## Open question that gates the largest branch

> **Is NEXUS becoming a plugin host?** (`02-PLAN-MACRO.md` PHASE 3)

Yes → Track C is the foundation (~250–400 LOC). No → C0 only (~30 LOC) and delete 15,851 LOC.
Retrofit cost is asymmetric; "probably eventually" means yes, and the answer is needed now.
