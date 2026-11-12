# 07 — Plan Graph: the execution plan as a queryable relational graph

The exec docs in this directory are prose. This document defines a **typed graph** over them,
materialised to `plan_graph.lbug`, and **joined to the live code graph** that GitNexus already
maintains for this repository. The result is one Cypher surface that can answer "what is ready",
"what breaks if I touch this", and "which gate proves it" — instead of grepping seven markdown files.

Generated artefact: `docs/design/exec/plan_graph.lbug` (LadybugDB / Kuzu, single file).
Tooling: `_research/plan_graph/` (Node, `@ladybugdb/core@0.19.1` pinned exactly).

---

## 1. Why a separate database

`.gitnexus/lbug` is the **code** graph. The plan graph is a **different graph with a different
lifetime** — it changes whenever the docs change, and re-running `gitnexus analyze` rewrites the
code graph wholesale. Writing plan nodes into `.gitnexus/lbug` would mean either losing them on the
next index or corrupting the analyser's store. So:

| graph | file | owner | rebuild |
|---|---|---|---|
| code | `.gitnexus/lbug` | GitNexus | `gitnexus analyze` |
| plan | `docs/design/exec/plan_graph.lbug` | this directory | `node build_graph.js` |

They are joined by a **shared key, not a shared file**: `filePath`, repo-relative. That join is
mirrored *into* the plan graph (see §3), so a single Cypher query can walk plan → file → symbol →
external caller without a federated query engine.

> **Storage-version trap.** `.gitnexus/lbug` is LadybugDB storage **v43**. The globally installed
> `gitnexus` 1.6.9 bundles `@ladybugdb/core` 0.18.1, which reads **v42** and refuses the file
> (`Database file version: 43, Current build storage version: 42`). v0.19.x is the build that
> matches — **0.19.1 is pinned**, and 0.20.x is already v47. Do not float this dependency; a
> caret range will silently break the reader on the next install.

---

## 2. Node model (13 tables, 1,310 nodes)

| table | n | meaning | key columns |
|---|---|---|---|
| `Plan` | 1 | the whole exec set | `headCommit` |
| `Phase` | 7 | `P0`..`P6` | `num`, `goal` |
| `Ticket` | 24 | `A4-1`, `I5-2`, … | `owner`, `risk`, `est`, `statusTxt` |
| `Step` | 60 | atomic steps `A2-0.1` | `whereTxt`, `checkTxt`, `doTxt`, `runTxt`, `assertTxt`, `undoTxt`, `isComplete` |
| `Gate` | 175 | a CHECK / ASSERT / DECISION gate | `kindTxt`, `cmd`, `expectTxt` |
| `Correction` | 16 | `P1`..`P3`, `C1`..`C13` | `severity` |
| `Decision` | 4 | the open questions | `questionTxt`, `optionsTxt`, `blockingTxt`, `statusTxt` |
| `Measurement` | 4 | `M1`..`M4` | `assertTxt`, `blocked` |
| `Path` | 53 | a file (33 code, 20 design/output) | `onDisk`, `toCreate`, `roleTxt`, `loc`, `bytes` |
| `Sym` | 652 | a symbol mirrored from the code graph | `name`, `kindTxt`, `filePath`, `startLine`, `isExternal` |
| `Area` | 109 | a GitNexus community (functional area) | `labelTxt`, `cohesion` |
| `Flow` | 205 | a GitNexus process (multi-step flow) | `processType`, `stepCount` |

Every node id is namespaced so the id alone determines the label. Three collisions were found and
fixed during the build, and they are worth knowing because they are easy to re-introduce:

- **`P1`/`P2`/`P3` are both phase ids and correction ids.** Corrections are stored as `COR-P1`;
  corrections are never referenced bare.
- **`Phase P4` and `Decision D-PLUGIN` both name a phase as the blocked thing** — decisions can
  point at phases (`BLOCKS_PHASE`) as well as tickets.
- **Cypher escapes a quote as `\'`, not SQL-style `''`.** The doc text is full of apostrophes
  ("A2-0's"), so every string is **bound as a parameter** — never interpolated. Hand-escaping
  produced a parser failure at byte 2351.

Also note: `exists`, `options`, `kind`, `type`, `label`, `status`, `note`, `doc`, `line` are
reserved or ambiguous as Kuzu column names. They are renamed on insert via a single `RENAME` map
(`exists`→`onDisk`, `options`→`optionsTxt`, …) applied identically to the schema and the rows.

---

## 3. The code mirror (how the join works)

`build_graph.js` reads `.gitnexus/lbug` read-only and mirrors **only the subgraph the plan touches**:

```
Path ──DEFINES──► Sym ──CALLS──► Sym          (390 + 261 edges)
Sym  ──MEMBER_OF──► Area                       (295)   functional area
Sym  ──ENTRY_POINT_OF──► Flow                  (205)   multi-step flows
Path ──HANDLES──► Sym(kind=Route)              (66)    the served surface
Sym  ──HANDLES_TOOL──► Sym(kind=Tool)          (98)    the tool surface
Path ──IMPORTS──► Path                         (10)
```

**Plus one inbound hop** — callers *outside* the touched set, flagged `isExternal` on the `Sym`.
That hop is what makes the graph useful for change-safety: 174 edges from 52 files, dominated by
`tests/`, which is exactly the set the regression gates must cover.

Directions were read from the source graph, not assumed. `DEFINES` is `File → Symbol`;
`CALLS` is both `Symbol → Symbol` and a **file-level rollup** `File → Symbol`. The rollup is real
GitNexus output — it must be kept as an edge (`Path → Sym`) but must **not** be materialised as a
symbol node, which is what produced 12 phantom `File:...` symbols on the first pass.

A Cypher `MATCH` that finds nothing produces **no rows and no error**, so a naive
`UNWIND … MATCH … CREATE` silently drops edges — the first build reported 1,577 "inserted" against
445 actually present. The builder now verifies both endpoints against the loaded node set and
reports every drop. Current build: **0 skipped**.

---

## 4. Edge model (26 types, 1,801 edges)

Plan-side:

| edge | meaning | n |
|---|---|---|
| `CONTAINS` | Plan→Phase→Ticket→Step | 61 |
| `GATED_BY` / `HAS_GATE` | step→gate / phase→gate | 110 / 31 |
| `TOUCHES` / `READS` / `REFERENCES` | step or ticket → file | 34 / 100 / 26 |
| `PREREQ` | **`a → b` = "a depends on b"** | 15 |
| `BLOCKS` | **`a → b` = "a blocks b"** (⇒ b depends on a) | 4 |
| `ORDER_BEFORE` | **`a → b` = "a precedes b"** (⇒ b depends on a) | 21 |
| `IN_PHASE` | ticket → phase | 25 |
| `CORRECTS` / `CORRECTS_FILE` | correction → ticket / file | 10 / 24 |
| `CITES` | ticket → correction it invokes | 2 |
| `BLOCKS_TICKET` / `BLOCKS_PHASE` | open decision → what it blocks | 3 / 1 |
| `MOVES_TO`, `SPLITS_INTO`, `ELABORATES`, `RESOLVED_BY` | structural | 1 each |

**The three ordering edge types use three different conventions** (`PREREQ` points
dependent→dependency; `BLOCKS` and `ORDER_BEFORE` point cause→effect). They are collapsed into a
single `dependsOn` map before any sorting, inside `exec_order.js`. Treating them uniformly is the
most likely way to get the schedule silently wrong.

---

## 5. Graphical export

`export_graph.js` emits **Mermaid** (default) or **Graphviz DOT** (`--dot`), and Graphviz renders
straight to SVG/PNG:

```
node export_graph.js plan    --dot --out out/spine.dot       # 32 nodes, 73 edges
node export_graph.js gates   --dot --out out/gates.dot       # 241 nodes, 141 edges
node export_graph.js ticket A2-1 --out out/ticket-A2-1.mmd   # one ticket expanded
node export_graph.js blast  A2-1 --out out/blast-A2-1.mmd    # files → symbols → callers
dot -Tpng -Gdpi=140 out/spine.dot -o out/spine.png
```

Rendered in `out/`: `spine.{dot,mmd,svg,png}`, `blast-A2-1.*`, `ticket-A2-1.*`, `gates.*`.
`spine.svg` verifies as 32 `class="node"` and 73 `class="edge"` groups.

---

## 6. Execution driver

`exec_order.js` is the "prepare for the execution" half. State lives in
`docs/design/exec/.exec_state.json`.

```
node exec_order.js status          # progress, effort, critical path
node exec_order.js order           # topological waves
node exec_order.js ready           # ready now, with the reason for each block
node exec_order.js next            # the single next ticket + its full step/gate brief
node exec_order.js check [ID]      # run the read-only CHECK gates (precondition sweep)
node exec_order.js start|done|reset ID
node exec_order.js trace ID
```

`check` runs **only** commands that are provably inspection (`grep`, `ls`, `python -c`, `git log`,
`curl -s`, …) and refuses anything matching a mutation pattern; refused gates print their command
so they can be run deliberately. It also splits the doc convention `<cmd> → expect <X>` and
**evaluates** the expectation — passing `→ expect 135` to the shell made `grep` exit 2 *while
printing the match*, reporting a passing gate as a failure.

Current state: **24 tickets, 0 done, 3 ready, 21 blocked, 28.5 h total, critical path 27.0 h across
20 waves.**

---

## 7. What building the graph found

These are defects in the plan documents, surfaced by making the docs machine-readable. They are
listed here rather than silently patched, because each is a judgement call.

1. **`B2-1.1` CHECK is wrong and returns `2`.** It reads
   `json.load(open('_research/delete_list_recovered.json'))` and prints `len(d)`. That file is a
   **dict** — `{"final": [95 paths], "loc": {95 entries}}` — so `len` is **2**, not 95. The ticket's
   premise ("the 95-file list") is correct; the *gate* is wrong. It should be `len(d['final'])`.
2. **`B2-1.2` CHECK is a non-runnable placeholder that false-passes.** Command is
   `ls $(the 95 paths) | wc -l`. Executed naively, bash runs `the 95 paths` as a command (not
   found) and `ls` with no arguments lists the **working directory** — reporting 79, a
   plausible-looking number with no relationship to the gate. It is now refused rather than run.
3. **`A2-2.2` CHECK is `none`** — the step has an ASSERT but no precondition.
4. **`A2-0` is assigned to two phases** (25 `IN_PHASE` edges for 24 tickets). Everything else is 1:1.
5. **The plan has almost no parallelism**: 24 tickets over 20 waves. The critical path consumes
   27.0 h of the 28.5 h total — i.e. the schedule is one serial chain and adding executors will not
   shorten it.
6. **`_short_circuit` is not dead code.** The code graph shows exactly one caller —
   `unified_route_v5`, same file, confidence 0.85. So `A2-0`'s question is not "is it unreachable"
   but "does that one path fail its gates at runtime", which is a measurement, not a reading.
7. **`A2-1`'s external blast radius is much smaller than the file suggests.** Touching
   `server/router_v2/v5.py` breaks exactly one external caller: `server/router_cutover.py`
   (`_shape_v5`). The 13 symbols in that file have no other out-of-file dependents.
8. **13 gates are orphans** — all the `00#Gx.y` DECISION gates from the HLD. They belong to document
   sections rather than steps, so nothing links them. This is by design, but it means they will
   never appear in a step-level sweep.
9. **1 step has no gate** (`A2-0.4`, the branches step) and **17 steps have no file** (verification
   steps). Both are expected; recorded so the "0 orphan steps" figure is not misread as complete.

---

## 8. Limits

- **The code mirror is a snapshot.** `.gitnexus/lbug` was indexed at commit `2dfb62d`; `HEAD` is
  `c8782ad`. Symbol positions may have drifted. Re-run `gitnexus analyze` then `node build_graph.js`
  to refresh. The plan graph itself is always current relative to the docs.
- **Call edges are 1 hop inbound only.** Transitive blast radius is not materialised; query for it.
- **`Sym` covers Function/Method/Class/Interface + Route/Tool.** Variables, properties and imports
  are not mirrored as symbols.
- **Gate commands are extracted, not validated.** `check` evaluates the `→ expect` convention; a
  gate written in prose (as `B2-1.2` is) can only be refused, not judged.

## 9. Rebuild

```
cd /home/roshan/nexus/_research/plan_graph
node parse_plan.js        # docs → plan.json   (verify the summary before loading)
node build_graph.js       # plan.json + .gitnexus/lbug → docs/design/exec/plan_graph.lbug
node pg.js summary        # sanity: node/edge census
node exec_order.js status # readiness + critical path
```
