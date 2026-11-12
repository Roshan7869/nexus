# NEXUS — Root-Cause Analysis & Remediation Docs

> Swarm-verified audit (2026-09-25). This directory contains one research-grounded
> remediation doc per issue surfaced by the 5-agent audit swarm + 3 deep-dive RCA
> agents. Every doc bridges four layers:
>
> **Paper / Principle → Engineering principle → This exact `file:line` → Concrete change + pass/fail test.**
>
> No doc cites research without terminating in a named file, a specific change, and a
> verification criterion. That is the "research → ground-level reality" contract.

---

## Provenance of this analysis

- **Trigger:** an external "cruel audit" report scored NEXUS at *5% reality / over-engineered theater*.
- **Method:** 5 parallel audit agents (router, OKF, learning, DB schema, MCP) cross-checked
  every claim against the live tree + live DB; 3 deep-dive agents did mechanism-level RCA on
  the three uncertain defects (tree divergence, cold-start latency, affinity flatline).
- **Headline correction:** the report's 5% is *inverted*. The core platform is live and
  substantial (`unified_v5` router, ~27K learning rows, 99 MCP tools, 89-table / 624 MB DB,
  74 Louvain communities). The genuine "theater" is isolated to **one quarantined, non-production
  subsystem** (the OKF graph engine). See `F2`.

---

## Causal model

Nine surfaced failures collapse into one systemic root cause + two isolated defects + a set of
legitimate design gaps. A follow-up schema study surfaced one further data-systems prerequisite (F8):

```
SYSTEMIC ROOT ─ Divergent repos + a quarantined-but-not-removed KG initiative
   ├─ F1  Two-tree divergence            (parent cause — also caused the false audit)
   ├─ F2  OKF graph engine orphaned       (dangling import in stale tree)
   ├─ F3  Silent tool-drop on ImportError (fail-soft anti-pattern)
   └─ F4  okf_migrate.py never run        (correctly-abandoned WIP)

ISOLATED DEFECTS (unrelated to the above)
   ├─ F5  Cluster-affinity flatline       (reward-shaping bug — but inert)
   └─ F6  Cold-start 14–21s               (misdirected warmup)

SCHEMA / DATA-SYSTEMS (prerequisite for the F7 KG work)
   └─ F8  Migration drift + dead registry (split dirs, untracked resource_edges ALTER)

DESIGN GAPS (not failures — roadmap)
   └─ F7  temporal / identity / cross-encoder / provenance / GraphDelta
          (F7a/F7d/F7e depend on F8 — no clean schema surface to edit until it lands)
```

---

## Doc index & priority

> **Execution order:** see [MASTER-PLAN.md](MASTER-PLAN.md) — the self-aware DAG that sequences
> every doc below into parallel waves with dependency edges, per-node verify gates, and the global
> audit loop. Critical path: `F1 → F8 → F7e → F7a → F7b`.

| Doc | Issue | Discipline | Severity | Effort | Priority |
|-----|-------|-----------|----------|--------|----------|
| [F1](F1-two-tree-divergence.md) | Two-tree divergence | Release Eng / SRE | HIGH | LOW | **P0** |
| [F2](F2-okf-orphaned-engine.md) | OKF graph engine orphaned | Build / Architecture | LOW(prod) | LOW | **P0** |
| [F3](F3-silent-tool-drop.md) | Silent tool drop on ImportError | Observability / SRE | MED | LOW | **P1** |
| [F4](F4-unrun-migration.md) | Migration never run | Data / ETL | LOW | LOW | **P1** |
| [F5](F5-affinity-flatline.md) | Cluster-affinity flatline | ML Systems / Bandits | MED(latent) | LOW | **P1** |
| [F6](F6-cold-start-latency.md) | Cold-start 14–21s | Performance Eng | MED | LOW | **P1** |
| [F7a](F7a-temporal-fact-model.md) | Temporal fact model | KG Architecture | HIGH | HIGH | **P2** |
| [F7b](F7b-identity-resolution.md) | Identity resolution | Entity Resolution | HIGH | HIGH | **P2** |
| [F7c](F7c-cross-encoder-rerank.md) | Cross-encoder rerank lane | IR Engineering | MED | MED | **P2** |
| [F7d](F7d-provenance.md) | Edge-level provenance | Data Provenance | MED | MED | **P2** |
| [F7e](F7e-graphdelta-writes.md) | Transactional KG writes | Data Systems | MED | MED | **P2** |
| [F8](F8-schema-migration-drift.md) | Migration drift + dead registry | Release Eng / Data Systems | HIGH | MED | **P1** |

---

## Standard doc schema

Every `Fx-*.md` follows this structure:

1. **Symptom (observed)** — live evidence, numbers, what was seen.
2. **Root cause (mechanism)** — the causal chain, with `file:line`.
3. **Scientific grounding** — papers + principles (full citations in §9).
4. **Engineering context** — how the principle maps to *this* codebase.
5. **Files / components to update** — exact paths + change + annotation marker.
6. **Execution steps** — ordered, atomic, ≤ 1 hr each.
7. **Verification (pass/fail)** — the test that proves it is fixed.
8. **Risk & rollback**.
9. **References** — full academic citations (author, title, venue, year).

---

## In-code annotation convention

Live files touched by a remediation carry a back-reference marker so code points to its doc:

```python
# RCA-F5(learning.py:1139): reward-shaping saturation — see docs/rca/F5-affinity-flatline.md
```

Format: `RCA-<IssueId>(<file>:<line>): <one-line reason> — see docs/rca/<doc>.md`.
Dead code recommended for deletion is **not** annotated (the removal itself is the fix; the doc records it).

---

## Canonical tree (established by F1)

- **Source of truth AND deployed:** `/home/roshan/nexus` @ branch `second-brain-v2`.
- **Production MCP entrypoint:** `/home/roshan/nexus/venv/bin/python3 /home/roshan/nexus/server/nexus-mcp-enhanced.py` (99 tools).
- **Non-canonical (archive/remove):** `/home/roshan/Downloads/nexus` @ branch `main` (stale ~4 months, disjoint git history).

---

## Shared reference library

Full citations live in each doc's §9. Cross-cutting anchors:

- Forsgren, N., Humble, J., Kim, G. *Accelerate: The Science of Lean Software and DevOps.* IT Revolution, 2018.
- Beyer, B., Jones, C., Petoff, J., Murphy, N. R. *Site Reliability Engineering.* O'Reilly, 2016.
- Ousterhout, J. *A Philosophy of Software Design.* Yaknyam Press, 2018.
- Sutton, R. S., Barto, A. G. *Reinforcement Learning: An Introduction*, 2nd ed. MIT Press, 2018.
- Dean, J., Barroso, L. A. "The Tail at Scale." *Communications of the ACM* 56(2), 2013.
