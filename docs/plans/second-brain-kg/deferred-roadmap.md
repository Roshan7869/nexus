# Deferred roadmap — L2–L4 (not built in this track)

These layers are **enumerated, not implemented.** They are recorded here with their atom, gap,
and paper grounding so the decision to defer is explicit and nothing is lost. Promote a layer
to an active per-file doc only when L0+L1 are green and the layer is scheduled.

Authority for every paper below: [`docs/research/gap-paper-map.yaml`](../../research/gap-paper-map.yaml)
and [`ADR-002`](../../research/ADR-002-research-authority-second-brain-v2.md). Do not cite IDs
absent from those files.

---

## L2 — Learning-loop quality (atoms A5_feedback, A3_rank)

| Gap | What | Likely file(s) | New/Edit | Papers → role |
|-----|------|----------------|----------|---------------|
| **G13** | Replace silent `except: pass` on `routing_log` insert with fail-loud log + metric | `nexus-mcp-enhanced` find_skills path | edit | `arXiv:2303.11366` Reflexion (no silent signal loss); `arXiv:2602.10479` observability |
| **G12** | Route-id lifecycle: open→timeout auto-close→explicit outcome; raise coverage `outcome/routing > 0.8` | `learning.record_outcome`, an auto-close cron | edit + small new | `arXiv:2303.11366`; `arXiv:2505.24726`; `arXiv:2605.29682` EFC |
| **G14** | Wire pluggable Reflexion on `wrong/partial` only; cheap model; perplexity flag OFF | `server/autonomous_pipeline.py` | edit | `arXiv:2303.11366`; `arXiv:2405.06682`; `arXiv:2405.13009` MetaReflection |
| **G15** | Model cascade as a **separate** bandit layer (skills ≠ models) | new `server/model_cascade.py` | new | `arXiv:1707.02038` TS; `arXiv:2305.05176` FrugalGPT; `arXiv:2406.18665` RouteLLM |

**Deferred because:** L2 depends on a restored, non-empty brain (G1) and a live feedback
stream; wiring it against an empty registry produces no learning signal.

---

## L3 — Inductive / learned edges (atoms A1_registry, A3_rank)

| Gap | What | Likely file(s) | New/Edit | Papers → role |
|-----|------|----------------|----------|---------------|
| **G25 (L3 half)** | GraphSAGE inductive edge prediction for brand-new tools with no co-trigger history; multi-hop expansion in `graph_lane` | new `server/kg/edge_predict.py`; extend `graph_lane.py` `hops>1` | new + edit | `arXiv:1706.02216` GraphSAGE (sample→aggregate→link-predict); `SSRN:4807690` KG-TS |

**Deferred because:** ADR-002 fixes the order — *restore densified edges first, add learned
edge prediction only if edge-pred is actually needed.* L1's deterministic typed edges must be
shown insufficient on the golden suite before paying GraphSAGE's training/complexity cost.
Multi-hop expansion is deferred with it (Lost-in-Middle risk grows with hop count).

---

## L4 — Architecture hygiene (atom A6_serve, plus A2/A4)

| Gap | What | Likely file(s) | New/Edit | Papers → role |
|-----|------|----------------|----------|---------------|
| **G8** | Deprecate parallel routers: v5 production + v4 fallback flag only; delete dead paths after N days | router entry + flags | edit/delete | `arXiv:2603.22862`; `arXiv:2606.17519` |
| **G17** | Break `harness ↔ learning ↔ router` import cycle via dependency inversion (ports/late-import) | package split | edit | `arXiv:2602.10479` layering; `arXiv:2602.07147` anti-patterns |
| **G16** | One warm API facade (daemon primary; core gateway optional) | HTTP apps | edit | `arXiv:2606.30317` MCP proxy/aggregator |
| **G22** | Make `AutonomousToolCaller` the default agent path (before/after-model gate) | `autopilot_run`/broker wiring | edit | `arXiv:2604.21816`; `arXiv:2506.01056`; `arXiv:2303.11366` |

**Deferred because:** these are refactors with broad blast radius; they should follow the
routing cutover (G7) and only after L0/L1 prove the new surfaces work end-to-end. `server/kg/`
is designed as a leaf now (see `per-file/server.kg.__init__.py.md`) specifically so L1 does
**not** add to the G17 cycle it will later have to break.

---

## Promotion checklist (deferred → active)
1. L0 + L1 acceptance in `00-overview.md` all green.
2. Restored brain (G1) live; feedback stream non-zero (precondition for L2).
3. Golden-suite evidence that the layer's problem is real (e.g. L3 only if L1 typed edges
   under-recall on the suite).
4. Write a per-file doc under `per-file/` using the same template, then implement.
