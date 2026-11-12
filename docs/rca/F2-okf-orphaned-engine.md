# F2 — OKF Graph Engine Orphaned · Build & Packaging / Architecture

**Severity:** LOW (production) · **Effort:** LOW · **Blast radius:** Dead-in-dead-tree · **Status:** OPEN

---

## 1. Symptom (observed)

Six MCP tools — `okf_ingest`, `okf_query`, `okf_context`, `okf_build_relationships`,
`okf_graph_loop`, `okf_stats` — are registered in code but **never load at runtime**. The external
audit called this "1,100 lines that can't be imported / missing numpy" and used it to justify a
whole-system "theater" verdict.

## 2. Root cause (mechanism)

The report's *conclusion* (engine is non-functional) is correct; its *cause* is wrong.

```python
# Downloads/nexus/server/nexus-mcp.py:469-474  (STALE, non-production tree)
sys.path.insert(0, str(_Path.home() / 'nexus/server'))     # → /home/roshan/nexus/server
try:
    from okf_graph_engine import NexusGraphEngine, OKFParser, ConceptGraph, ...
    # ... registers 6 okf_* tools (:485-558)
except ImportError as e:                                    # :561
    logger.warning(...)                                     # :562  → tools silently absent
```

- The engine (`okf_graph_engine.py`, **1,158 lines**) imports **fine** in isolation (`numpy 2.4.4`
  present) — so "can't import / missing numpy" is **refuted**.
- It is **orphaned by path**: the file was quarantined into `Downloads/nexus/theater_backup/`
  (untracked, on no `sys.path`) while the loader looks for it at `~/nexus/server/okf_graph_engine.py`,
  where it has **never** existed (`git log --all -- '**/okf_graph_engine.py'` is empty in both repos).
- The file that imports it — `nexus-mcp.py` — lives **only in the stale Downloads tree**. Production
  runs `nexus-mcp-enhanced.py` (99 tools, no `okf_*` graph tools). So the failure never touches
  production traffic.

**Deliberate quarantine, not accident:** `theater_backup/` matches the live tree's own vocabulary
(`docs/REALITY_CHECK_AND_PATH.md`: "autopilot theater", "autonomy theater"). The engine was parked
as looks-functional-but-unwired code, but the dangling import + tool registrations were left behind.

## 3. Scientific grounding

- **Ousterhout, *A Philosophy of Software Design* (2018)** — "shallow modules" and "special-case
  code" are complexity liabilities. A 1,158-line engine that exposes 6 tools yet delivers zero
  runtime capability is maximal complexity for zero depth: delete or make it deep (real).
- **Lehman's Laws of Software Evolution (1980)** — Law of Increasing Complexity: without active
  effort to reduce it, complexity grows and quality declines. Dead subsystems that still register
  tools are exactly the accreted complexity Lehman warns about.
- **Dead-code reachability (compiler theory; Aho, Lam, Sethi, Ullman, *Compilers*, 2006)** — code
  unreachable from any live entrypoint should be eliminated; here the entrypoint (`nexus-mcp.py`)
  is itself unreachable from production.
- **PEP 451 (import system, 2015)** — clarifies that a failed import is a hard, observable event;
  swallowing it with `except ImportError` (see F3) hides a contract violation.

## 4. Engineering context

Two coupled decisions: (a) the OKF **graph** engine is a stalled initiative; (b) a separate,
*working* OKF **section-enrichment** path (`server/okf_sections.py`, `server/ingest/okf_ingest.py`,
`server/export/okf_export.py`) is what actually serves context and imports cleanly. Do not confuse them —
only the graph engine is dead. Remediation: remove the dead graph engine + its dangling loader
(with F1's tree cleanup and F4's migration), keeping the section-enrichment path untouched.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `Downloads/nexus/theater_backup/okf_graph_engine.py` | 1,158-line orphan | Remove (archived by F1) | N/A |
| `Downloads/nexus/server/nexus-mcp.py:469-562` | Dead import + 6 tools | Remove block (or promote engine w/ real path) | N/A (removed) |
| `server/okf_sections.py`, `server/ingest/okf_ingest.py`, `server/export/okf_export.py` | **Working** | **No change** — confirm not confused with engine | comment note |

## 6. Execution steps

1. Confirm the working OKF section path imports (regression guard):
   `venv/bin/python3 -c "import server.okf_sections; from server.export import okf_export; print('ok')"`
2. If keeping F1's archive, no separate archive needed (theater_backup is inside Downloads).
3. Remove the dead engine + loader block **only after F1's archive exists** (they ride together).
4. Add a one-line comment to `okf_sections.py` clarifying it is the live OKF path, not the engine.

## 7. Verification (pass/fail)

- **PASS** iff production MCP tool count is unchanged (99) after removal — proves the `okf_*` graph
  tools were never live: compare `list_tools()` count before/after.
- **PASS** iff the working section path still imports (step 1).

## 8. Risk & rollback

- Low risk: the removed code is non-production and never loads. Rollback via F1's tarball.
- Do **not** delete `server/okf_sections.py` / `okf_export.py` — those are live.

## 9. References

1. Ousterhout, J. *A Philosophy of Software Design.* Yaknyam Press, 2018.
2. Lehman, M. M. "Programs, Life Cycles, and Laws of Software Evolution." *Proceedings of the IEEE* 68(9), 1980.
3. Aho, A.; Lam, M.; Sethi, R.; Ullman, J. *Compilers: Principles, Techniques, and Tools*, 2nd ed. Addison-Wesley, 2006 (§9 dead-code elimination).
4. Python Software Foundation. *PEP 451 — A ModuleSpec Type for the Import System.* 2015.
