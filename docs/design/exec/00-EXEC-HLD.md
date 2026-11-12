# NEXUS — Execution: HLD

Elaborates `00-HLD.md`. Every architectural claim and decision in the HLD becomes a **falsifiable
gate** here: a command, and a literal expected result. If a gate fails, the HLD section is stale — fix
the doc before building on it.

Grammar: `exec/README.md` §1. **Corrections that apply: `exec/06-CORRECTIONS.md` (P1, P3, C2).**

---

## §1 — System context: the seven facts

Each row of `00-HLD.md` §1 becomes a re-runnable check. Run all seven as one gate script.

```
G1.1  resources         sqlite3 -readonly db/nexus.db "SELECT COUNT(*) FROM resources"
      ASSERT  expect 3255
G1.2  active            ... "SELECT COUNT(*) FROM resources WHERE status='active'"
      ASSERT  expect 1862
G1.3  by type           ... "SELECT resource_type,COUNT(*) FROM resources GROUP BY 1 ORDER BY 2 DESC"
      ASSERT  skill 1296 · topic 419 · mcp 329 · command 276 · doc 194 · agent 186 ·
              design_system 173 · spec 156 · cli_tool 137 · hook 77 · craft 12
G1.4  clusters          ... "SELECT cluster,COUNT(*) FROM resources GROUP BY 1 ORDER BY 2 DESC"
      ASSERT  8 rows: knowledge_wiki 618 · frontend_ui 576 · devops_infra 500 · architect 488 ·
              general 435 · analyzer_planner 333 · backend_api 175 · quality_security 130
G1.5  DB size           stat -c%s db/nexus.db
      ASSERT  **538640384**  (538.6 MB)   <- CORRECTED from the HLD's "526 MB" (C2)
G1.6  serving           ss -ltn | grep -E "8000|8080|9999"
      ASSERT  ONLY 127.0.0.1:8000. :8080 and :9999 absent.
G1.7  structural gaps   graph query on .gitnexus/lbug — Module=0, Interface=0, ACCESSES=2
      ASSERT  values match, OR record the new ones. NOTE: no kuzu/ladybug client is installed
              (04-FEASIBILITY.md §5.3) — a query needs a client installed first, and must be
              BOUNDED (LIMIT/start-node/depth) or it will be killed at 600 s.
```

**Gate §1:** all seven match, or the HLD §1 table is updated in the same commit. A stale fact table is
how "526 MB" survived into three documents.

---

## §2 — Defect #1 (two parallel fusion systems): the migration gate

The HLD's strongest claim is that `backend_protocol.py:142` declares the new path canonical while 13
backends still use the old one. That is checkable.

```
G2.1  the policy line exists
      grep -n "New code should prefer" server/context_harness/backend_protocol.py
      ASSERT  expect `142:    New code should prefer ``fuse_registered()`` with a ``BackendRegistry``.`

G2.2  the old path is still the production path
      grep -c "fuser.register(BackendDef" server/context_harness/server.py
      ASSERT  expect **13**   (lines 918,943,968,991,1013,1032,1051,1070,1089,1113,1136,1160,1188)

G2.3  the new registry has zero production users
      grep -rn "BackendRegistry()" --include=*.py server/ | grep -v test
      ASSERT  expect 0 — confirm it is declared and unused

G2.4  the class is RRFFuser, NOT Fuser        <- CORRECTED (P1)
      grep -n "^class RRFFuser" server/context_harness/fusion.py
      ASSERT  expect `34:class RRFFuser:`
      A gate written against `Fuser` returns nothing and "passes" vacuously.

G2.5  the registry has no disposal today
      sed -n '35,60p' server/context_harness/backend_protocol.py
      ASSERT  `register()` has no cleanup handle, no reload, no unload
```

**Gate §2 (after C1-1):** `G2.2` → **0**, and every backend still returns results. Zero is the target;
13 is today. Nothing else is "done" for defect #1.

---

## §3 — Defect #2 (served ≠ documented): the surface gate

```
G3.1  served routes respond
      for p in /health /stats /v1/health; do curl -s -o /dev/null -m 5 -w "$p %{http_code}\n" \
        http://127.0.0.1:8000$p; done
      ASSERT  expect 200 200 200

G3.2  documented routes do NOT respond
      for p in /v1/models /v1/chat/completions; do ... ; done
      ASSERT  expect 404 404  (GET /v1/chat/completions is 405 on a POST-only route — 404 is the signal)

G3.3  /v1/ops                              <- CORRECTED (P3)
      for i in 1 2 3 4 5; do curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}\n' \
        http://127.0.0.1:8000/v1/ops; done
      ASSERT  **5/5 = 200, 1.35-2.07 s**. The HLD's "`000` (TIMEOUT) ... its own bug" is NOT
              reproducible. Do not build on it.

G3.4  route counts
      ASSERT  daemon 32 · unified.py 39 · core/server.py 10

G3.5  the dead port
      grep -n "NEXUS_DAEMON_URL" .mcp.json
      ASSERT  line **33** (not 34): "http://127.0.0.1:8080"
```

**Gate §3 (after A1-1/A1-2):** every README-named route non-404 · `POST /v1/chat/completions` ≠ 404 ·
`NEXUS_DAEMON_URL` → `:8000` · `G3.4` shows ONE process serving both surfaces.

---

## §4 — The eight sections: each one's live defect

The HLD asserts a specific defect per section. Each becomes a gate, so a section cannot be declared
"fixed" on a stale premise.

| section | HLD claim | gate command | expected (today) |
|---|---|---|---|
| **S1** | 132 rows where `status` and `lifecycle_state` disagree; 2,389 NULLs | `SELECT status,lifecycle_state,COUNT(*) ... GROUP BY 1,2` | active/archived **132**; active/NULL **1730**; archived/NULL **659** |
| **S2** | `_short_circuit` bypasses the cold tier | `sed -n '237,270p' v5.py` — no `_v5_rerank` call | confirmed; gate at `:463`, bypass at `:464` |
| **S3** | defect #1 lives here | see §2 above | two registries |
| **S4** | static tuple partition | `grep -n "^CORE_TOOLS" harness_engine/toolsets.py` | `8:CORE_TOOLS` (also :15 :29 :41) |
| **S5** | `NEXUS_DAEMON_URL` → dead `:8080` | see G3.5 | `.mcp.json:33` |
| **S6** | `autonomous_pipeline.py` is dead | `grep -rn autonomous_pipeline --include=*.py` | only `scoring.py:26` prose + its own import at `:38`; **396 L** |
| **S7** | learner state leaks across tests | `grep -c learner tests/conftest.py` | **0** — the fixture is genuinely absent (282-line conftest exists) |
| **S8** | `routed = 13` of 6,670 | `curl -s http://127.0.0.1:8000/v1/ops/coverage` | routing_coverage.total_routes 6670, by_outcome.routed 13 |

**Gate §4:** for every section you claim to have touched, the gate command's output changes in the
stated direction. A section "fixed" with an unchanged gate is not fixed.

---

## §5 — CORDIS mapping: one gate per primitive

The HLD maps four CORDIS primitives onto four attachment points. Each mapping is falsifiable.

```
G5.1  Disposer  (§5, Γ)  →  every backend in S3, every MCP tool in S5
      CHECK   server/disposer.py exists and `_demo()` passes
      ASSERT  double close() no-op · post-close effect() raises Disposed · reverse order ·
              atomic() preserves the previous tree on failure
      NOTE    ExitStack gives LIFO + exception-safety + pop_all(); we add ONLY the _closed guard
              and named kinds. Anything hand-written beyond ~30 LOC is the defect, not the fix.

G5.2  Registry  (§5)  →  BackendRegistry (S3), toolsets.py (S4)
      CHECK   complete the DECLARED migration (G2.2 -> 0). Do not add a new registry.
      ASSERT  `close()` stays OPTIONAL via getattr — the 4-member BackendProtocol is unchanged

G5.3  Waterfall (§5)  →  v5.py post-filters (S2), context assembly (S3)
      CHECK   the interception point sits AFTER `_autocut` (def :135), not inside it
      ASSERT  a listener that skips next() vetoes; no listener => byte-identical output

G5.4  Fiber     (§5)  →  StateGates (S7), resource lifecycle (S1)
      CHECK   precondition.py:65 StateGates · GLOBAL_GATES = StateGates() at :114
      ASSERT  `lifecycle_divergence_count` = 0 in steady state
      WARNING GLOBAL_GATES is LIVE (backs check_preconditions/update_state at
              nexus-mcp-enhanced.py). WRAP, DO NOT DELETE.
```

**Gate §5:** all four primitives exist, and each is *smaller* than its test file. If any primitive has
grown a hand-written subsystem, the mapping has drifted from "CORDIS is not a framework to install".

---

## §6 — The decision gate (blocks P4)

The HLD calls this "the load-bearing question". An unrecorded answer is a blocked phase.

```
G6.1  the answer is written down
      grep -n "plugin host" docs/design/00-HLD.md docs/design/02-PLAN-MACRO.md
      ASSERT  a recorded YES or NO with a date — currently the HLD poses it and does not answer it

G6.2  if NO  → C0-1 only (~30 LOC) + delete 15,851 LOC (Track B)
      ASSERT  03-EXEC-TICKETS C1..C4 are marked CANCELLED, not left "pending"

G6.3  if YES → C0→C1→C2→C3→C4 (~250-400 LOC)
      ASSERT  C1-1 requires C0-1; C4-1 additionally requires the §5 in-flight decision (G7.3)

G6.4  retrofit cost is asymmetric
      ASSERT  the decision is taken BEFORE P4 starts, not during. A composition layer built after
              plugins exist costs several times more than one built before.
```

**Gate §6:** the question has a written answer and the uncontested branch's tickets are marked
`CANCELLED`. "Probably eventually" resolves to **YES**.

---

## §7 — Deployment topology (target): the end-state gate

```
G7.1  ONE process serves both surfaces
      ss -ltn | grep 8000            → one listener
      curl /v1/models                → non-404 after A1-1
G7.2  :8080 is either removed from .mcp.json or deliberately brought up. Not both. Not neither.
G7.3  db/nexus.db — single writer; read-only connections via cache.get_ro_conn()
      ASSERT  ss -ltn shows no second writer process; every migration ran against a COPY first
G7.4  graph — .gitnexus/lbug, LadybugDB, NOT queryable from this shell
      ASSERT  no step assumes a working graph client; any query installs one first and is BOUNDED
```

---

## §8 — Non-goals: the "must NOT" checks

Non-goals are only real if something fails when they are violated. These are the tripwires.

```
G8.1  no distributed coordination
      ASSERT  no step introduces C_AFFECT / C_FIBER / C_ORDER machinery
G8.2  no framework
      ASSERT  the disposer is smaller than its own test file. If server/disposer.py exceeds ~60 LOC,
              the kernel has drifted.
G8.3  no static module enforcement via runtime machinery  (G_MODULE)
      ASSERT  Module=0, Interface=0 today. A DI container does not create boundaries.
              Do NOT build one over boundaries that are already violated (268/300 traced flows
              cross a community edge). import-linter or restructure is the fix — separate effort.
G8.4  no rewrite of fusion.py
      ASSERT  `RRFFuser` is FINISHED-MIGRATED-OFF, not deleted. The migration is declared at
              backend_protocol.py:142; execute it, do not rewrite the module.
G8.5  CORDIS proof obligations are omitted
      ASSERT  no C_CONFLUENCE / C_PROGRESS / Thms 68-80 work appears. Verified infeasible
              (04-FEASIBILITY.md §5.1) — one process, one SQLite writer, no distributed state space.
```

---

## HLD execution summary

| HLD section | gate | passes when |
|---|---|---|
| §1 facts | G1.1–G1.7 | all seven match, with DB = 538.6 MB **corrected** |
| §2 defect #1 | G2.1–G2.5 | `fuser.register` count 13 → **0** |
| §3 defect #2 | G3.1–G3.5 | README routes non-404; **`/v1/ops` re-scoped, not "fixed"** |
| §4 sections | per-section gate | the touched section's numbers move |
| §5 primitives | G5.1–G5.4 | four primitives exist, each tiny |
| §6 decision | G6.1–G6.4 | answer written; losing branch CANCELLED |
| §7 topology | G7.1–G7.4 | one process, one writer |
| §8 non-goals | G8.1–G8.5 | no tripwire fires |
