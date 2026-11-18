# `server/kg/__init__.py` — knowledge-graph package marker (NEW)

| Field | Value |
|-------|-------|
| Layer | L1 (KG usable at retrieval time) |
| Status | DONE |
| New or edit | **NEW** (package init; trivial) |
| Depends on | none |
| First-principle atom | A1_registry |
| Gaps served | — (structural) |

## Role — what this file is
Package marker that makes `server/kg/` an importable module namespace. It exists so the one
genuinely new L1 module (`server/kg/graph_lane.py`) has a home that is clearly *the graph
retrieval layer*, separate from:
- `scripts/densify_kg.py` — **offline** edge derivation (batch, reversible, ops-run), and
- `server/router_v2/` — the existing fusion/scoring pipeline the lane folds into.

## Why a new package (not a loose module)
Keeping KG **retrieval-time** helpers in `server/kg/` draws a clean seam: derivation is a
script you run out-of-session; consumption is a library the router imports. This also keeps the
new code out of the `harness ↔ learning ↔ router` import cycle flagged in **G17** — `kg` is a
leaf that `router_v2` imports, and `kg` imports nothing from `router_v2`.

## Content (guided plan)
```python
"""server.kg — knowledge-graph retrieval helpers.

Offline edge derivation lives in scripts/densify_kg.py (batch, reversible).
This package is the *read* side: turn resource_edges into retrieval signal.
"""
```
No exports beyond the module(s) it contains. Keep it a docstring-only file; do not add
re-exports that would create import coupling.

## As built
Docstring-only package marker, as planned (no re-exports). `server/kg/` contains
`__init__.py` + `graph_lane.py`; importable both as `server.kg.*` (repo-root on path) and
`kg.*` (server/ on path — the form `router_v2.chunk_retrieval` uses).

## Acceptance
- [x] `from server.kg.graph_lane import expand_over_edges` resolves.
      — verified: `python3 -c "from server.kg.graph_lane import expand_over_edges"` OK;
      tests import via `kg.graph_lane` (server/ path) — both forms green in the 465-pass suite.
- [x] `gitnexus check --cycles` shows no new import cycle introduced by `server/kg/`.
      — check returns 3 cycles, all pre-existing and unrelated (`context_harness↔learning↔
      router`, `execution_plane↔local_harness`, `harness_metrics↔ide_manager`); none includes
      any file under `server/kg/`, which imports stdlib only.

## Rollback
Additive. Remove the `server/kg/` directory; nothing else depends on it until `graph_lane.py`
is wired into `router_v2`.
