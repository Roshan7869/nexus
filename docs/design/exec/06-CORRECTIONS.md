# NEXUS — Corrections (re-verification pass, 2026-09-13)

Every citation in `00-HLD.md` … `05-SITES-REGISTRY.md` was re-checked against the working tree at
`c8782ad` before writing the execution set. **13 were wrong.** Three are not typos — they are false
premises that change what the work is.

Read this before executing anything in `exec/`.

Legend: **C** = citation drift (cosmetic, fix the number) · **P** = false premise (changes the plan).

---

## P1 — `Fuser` does not exist. The class is `RRFFuser`. *(affects HLD §2, §8; LLD §3.1)*

| doc says | actual |
|---|---|
| `Fuser` (`fusion.py:44` `unregister`) | **`class RRFFuser` at `fusion.py:34`**; `unregister` at `:44` ✓ |

`fusion.py` contains `BackendDef` (:19), `CacheEntry` (:29), **`RRFFuser` (:34)**, `get_fuser()` (:133),
`fuse()` (:140). There is no symbol named `Fuser`. Any step that greps for `Fuser` returns nothing and
will silently "pass" a check it should fail.

```
CHECK   grep -n "^class RRFFuser" server/context_harness/fusion.py
ASSERT  expect: 34:class RRFFuser:
```

---

## P2 — I3's premise is **FALSE**: the MCP removal path already exists and works.

`02-PLAN-MACRO.md` I3 and `03-PLAN-MICRO.md` I3-1 both assert:

> *"`sync_mcp_tools` upserts with **no removal path** (`mcp_sync.py:222`), so a desynced server's tools
> persist forever."* — and conclude MCPs at 8.5% active are **"CRITICAL … the worst surface."**

**This is wrong on every count.**

| what was claimed | what is there |
|---|---|
| `mcp_sync.py:222` is the upsert | `:222` is inside the **removal** branch |
| "no removal path" | `delete_tool()` at **`:155`**; called at **`:218-222`** |
| function `sync_mcp_tools` | no such symbol in `mcp_sync.py` — it is an MCP *tool name*; the manager method is **`sync_from_mcp_server` (`:178`)**, called at `nexus-mcp-enhanced.py:964` |
| removal would be a new feature | it is existing, wired, and **has been running** |

```python
# mcp_sync.py:193, 218-222
server_names = {t.name for t in tools}
...
    if name not in server_names:
        if self.delete_tool(name):
            results['deleted'].append(name)
```

```python
# mcp_sync.py:155-160  — delete_tool IS the removal path
self.conn.execute("""
    UPDATE resources SET status='archived', updated_at=datetime('now')
    WHERE name = ? AND resource_type = 'mcp'""", (tool_name,))
```

**Consequence — the headline number inverts.** 329 MCP resources, 28 active (8.5%) is **not rot**.
301 archived rows are the removal path having **done its job** on tools the servers stopped reporting.
The macro plan's "MCPs are the worst surface in the system" is unsupported, and the 8.5% figure must
not be repeated as a health metric without saying what it measures.

**What the real defect is** (replaces I3-1's stated cause):

1. `sync_from_mcp_server` reconciles only against servers it is **currently called with**. A server that
   disappears from `.mcp.json` is never called again, so its tools are **never evaluated for removal** —
   they sit `active` forever. The blade only falls on tools of servers still in the loop.
2. `delete_tool` sets `status='archived'` but **never sets `archived_at`**.

```sql
SELECT COUNT(*) FROM resources WHERE resource_type='mcp' AND status='archived' AND archived_at IS NULL;
-- expect: 61   (301 archived total, so 240 carry archived_at from another sweep)
```

**I3-1 is re-scoped, not cancelled.** The work is "reconcile against the server registry, including
servers that are absent" + "set `archived_at` in `delete_tool`" — not "add a removal path".

---

## P3 — `/v1/ops` is **not** hanging. It returns 200 in ~1.5 s.

`00-HLD.md` §3 and A1-3 describe `/v1/ops` as `000 (TIMEOUT)` and call it "a served route that times
out is its own bug."

Live, five consecutive probes:

```
attempt1 code=200 time=1.376755s
attempt2 code=200 time=1.357868s
attempt3 code=200 time=1.589698s
attempt4 code=200 time=1.347169s
attempt5 code=200 time=2.073831s
```

**5/5 → 200, 1.35–2.07 s.** The earlier `000` was transient (the daemon was almost certainly mid-restart
during the original probe). A1-3's stated symptom does not reproduce.

**Re-scope:** A1-3 is no longer "fix a hang". The open question is "why does an ops endpoint cost the
same 1.35–2.07 s as a full route?" — i.e. it is a **latency** observation, and it is the same 1.2–2.1 s
signature as `04-FEASIBILITY.md` §1.1, which suggests `/v1/ops` runs the router internally. Downgrade
A1-3 from P1 to a P5/P6 profiling note, or close it. **Do not spend time on a hang that is not there.**

---

## C — citation drift (cosmetic; fix as you touch each)

| # | doc | claimed | actual |
|---|---|---|---|
| C1 | A1-2, LLD §5.1, HLD §3 | `.mcp.json:34` | **`.mcp.json:33`** — `"NEXUS_DAEMON_URL": "http://127.0.0.1:8080",` |
| C2 | HLD §1 | DB `526 MB` | **538.6 MB** = 538,640,384 B (feasibility §1.3 already correct) |
| C3 | B1-1 | `autonomous_pipeline.py` 397 L · `fts5.py` 216 L | **396 L** · **215 L** (`test_v3.py` = 380 L) |
| C4 | LLD §2.1 | `_short_circuit` call site `v5.py:463` | call is at **`:464`**; the **gate** is at `:463` |
| C5 | LLD §2.1 | gate `v5.py:463` | ✓ **correct** — `if confidence > short_circuit_confidence and pool_size >= short_circuit_min_pool:` |
| C6 | LLD §2.1 | `_v5_rerank` call `v5.py:487` | ✓ **correct** |
| C7 | LLD §2.1 constants | `:123-124` | ✓ `SHORT_CIRCUIT_CONFIDENCE = 0.85` :123, `SHORT_CIRCUIT_MIN_POOL = 50` :124 |
| C8 | (near-miss) | — | **`v5.py:478` has a *different* gate**: `pool_size >= 20` is the **L2 `constrain`** condition, not the short-circuit gate. Do not confuse these two — A2-0 reads the `:463` gate. |
| C9 | A3-1, LLD §1.2 | "recorded in `schema_migrations`" | table exists (`migrations/010_fts5_outcome_log.sql`) but holds **1 row**. And **two migration dirs**: `migrations/` (8 files, max `016_`) and `db/migrations/` (11 files, max `017_`). **Canonical dir must be decided before A3-1.** |
| C10 | LLD §3.1 | `fusion.py:44  class Fuser: unregister(name)` | see **P1** — `RRFFuser` at `:34` |
| C11 | LLD §4.1 | tuple constants | ✓ `CORE_TOOLS` :8 · `RESEARCH_TOOLS` :15 · `BROWSER_TOOLS` :29 · `MEMORY_TOOLS` :41 |
| C12 | LLD §7.1 | `precondition.py:65-114` | ✓ `StateGates` :65 · `GLOBAL_GATES = StateGates()` :114 |
| C13 | HLD §2, LLD §3.1 | `server.py:918-1188` ×13 registrations | ✓ **exactly 13**: 918, 943, 968, 991, 1013, 1032, 1051, 1070, 1089, 1113, 1136, 1160, 1188 |

Also verified as **already correct** and worth not re-litigating: `A4-1`'s premise — `tests/conftest.py`
exists (282 lines) and contains **no** learner isolation, no `autouse` fixture touching `_ENABLED`. The
fixture genuinely is missing. `/v1/models` 404 and `/v1/chat/completions` 404 both reproduce; the route
counts 32 (daemon) / 39 (`unified.py`) / 10 (`core/server.py`) are exact.

---

## Method, so this is repeatable

Each claim was checked with a direct read, never inferred:

```
grep -n "<symbol>" <file>            # existence + line
sed -n '<a>,<b>p' <file>             # context, to see which branch a line is IN
curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}' <url>   # x5, never once
sqlite3 -readonly db/nexus.db "<query>"   # via file:...?mode=ro
wc -l <file>                         # not "approximately"
```

**Five probe repetitions, not one, is what caught P3.** A single `-m 3` probe produced the false hang
that survived into three parent docs.

**Lesson for the next pass:** a line number proves a symbol *exists*, not that it *does* what the doc
claims. `mcp_sync.py:222` is a real line in the removal branch — the citation was "valid" and the
conclusion was backwards. Read the branch, not the line.
