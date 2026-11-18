# Archify → NEXUS Adaptation Plan

**Source:** `third_party/Archify` (cloned 2026-09-14, `--depth 1`, 57M, skill `2.17.0-dev.1`, MIT)
**Upstream:** https://github.com/tt-a1i/archify — *"Turn a codebase or system description into a polished, interactive system map — directly in chat."*
**Vendor rule:** treat as read-only reference (their `REVIEWING.md`/`CONTRIBUTING.md` govern upstream, not us). Verified working in THIS env: Node v22.22.2, `validate architecture … --quality showcase` → 0 errors / 0 warnings, `doctor` all `[ok]`.
**DSH note:** ships a first-party community plugin — `dsh plugin --profile web add @tt-a1i/archify-dsh@0.1.0` (skill-only bundle, no tools/network/telemetry; repo root is NOT installable — see their issue #341).

---

## 1. What Archify is (verified by reads + runs)

| Layer | Implementation | Notes |
|---|---|---|
| Agent skill | `archify/SKILL.md` + `skill-release.json`, installed via `npx skills add tt-a1i/archify -g` (Cursor/Codex/Claude/OpenCode switcher) | bounded authoring path: pick type → read 1 schema + 1 example → write candidate FIRST → validate → deliver |
| Typed JSON IR | `archify/schemas/` — `architecture`, `workflow`, `sequence`, `dataflow`, `lifecycle` (+`common`), Workflow schema v2 for new work | agents author IR, never raw SVG |
| Deterministic render | `archify/renderers/{architecture,dataflow,lifecycle,sequence,shared,workflow}/` + `bin/archify.mjs` (`render/validate/deliver/preview/compare/inspect/check/visual-check/guide/brands/examples/doctor/demo`) | `validate` = 9 showcase artifact checks; passing candidate FREEZES (never edit after) |
| arch-review | `archify/delta/architecture-delta.mjs` via `archify compare architecture base.json head.json` → Before / Delta / After with exact added/removed/changed/moved/rerouted facts | for pre-merge review |
| Viewer | `viewer/` + `examples/*.html`, dark/light, guided stories, route probe (shortest authored path), semantic lens (role compare), reach (authored upstream/downstream — explicitly NOT runtime impact), share cards 1200×630, PNG/SVG/WebM export | search + revision-verified source links; finite motion only |
| Trust model | `PRODUCT.md` principles: truth-before-spectacle, one spatial narrative first, portable proof (self-contained artifact + receipt, no hosted runtime), identity-without-drift (digest-pinned brand marks) | receipts over claims |
| Governance | `DESIGN.md` (design tokens), `PRODUCT.md`, `ROADMAP.md`, `CHANGELOG.md`, `SECURITY.md`, 40+ `docs/research-*.md` iteration logs, `benchmarks/`, `experiments/`, `generated/`, Proof Lab gallery (11 checked scenarios + receipts) | unusually honest paper trail |

## 2. Feature × NEXUS map

| Archify feature | NEXUS today | Verdict | Adaptation |
|---|---|---|---|
| Architecture IR → validated HTML maps | `docs/nexus_docs_code_graph.md` (hand Mermaid) + GitNexus graph (code-only, stale) | **ADOPT** | B1: author `docs/nexus-maps/nexus-runtime.architecture.json` (8–12 nodes from graph hubs: MCP stdio, HTTP :8080, v5 router, Thompson learner, L0-L3, SQLite, dashboard; one primary path task→route→tools→report) from `docs/nexus_docs_code_graph.json` hub degrees; `validate --quality showcase` → `deliver` → commit HTML + receipt. Replaces Mermaid with presentable, checkable artifact |
| `compare` Before/Delta/After | `code-review` skill (standards+spec, no visual) + `detect_changes()` (flows, no picture) | **ADOPT** | B2: snapshot IR per release (`base.json`); on PR, author `head.json`, `archify compare` → Delta HTML attached to review. Visual arch review joins the existing two-axis review |
| Workflow/sequence/lifecycle IR | v5 pipeline + session lifecycle described in prose across M-docs | **ADOPT** | B3: `nexus-router-v5.workflow.json` (L1→short-circuit→L2→L3→L4), `nexus-session.lifecycle.json` (INACTIVE→LOADING→ACTIVE⇄UNLOADING), `nexus-task.sequence.json` (IDE→MCP→broker→report). Validated diagrams become the LLD figures M1–M20 reference |
| Authored reach vs impact | GitNexus `impact()` (code CALLS edges, stale index) | **COMPLEMENT** | B4: rule of thumb — Archify reach = *approved/authored* topology for HLD reviews (share cards); GitNexus impact = *actual* code edges before edits. Never substitute one for the other; cite both in HIGH-risk PRs |
| DSH skill plugin | This session runs in DSH web; archify skill not installed | **INSTALL** | B5: `dsh plugin --profile web add @tt-a1i/archify-dsh@0.1.0` (exact version; Skill-only, no network/telemetry per their README). Then: *"Use the archify skill to map this repository's runtime…"* — generated JSON/HTML are normal workspace files ( Produced-Files strip needs exact paths returned — their documented limitation) |
| Skill authoring discipline | M-docs are long-form prose + pseudocode | **BORROW** | B6: their "artifact first, validate after every edit, freeze on pass" loop mirrors OpenResearch freeze rules (A6) — adopt as doc policy for `docs/nexus-maps/*.json`: validated + frozen, edits = new version, never silent mutation |
| Viewer/downstream tooling (brands, share cards, WebM) | Not needed | **SKIP** | social cards/motion are presentation-layer; take only if a demo demands it |

## 3. Concrete next steps (commands run from `/home/roshan/nexus`)

```bash
# B5 first (one command, unlocks the rest inside agent sessions):
dsh plugin --profile web add @tt-a1i/archify-dsh@0.1.0

# B1 (deterministic, no skill needed — toolchain proven above):
node third_party/Archify/archify/bin/archify.mjs validate architecture third_party/Archify/archify/examples/checkout-platform.base.architecture.json --quality showcase --json  # reference shape
# then author docs/nexus-maps/nexus-runtime.architecture.json (≤12 nodes, showcase profile),
# validate → deliver → docs/nexus-maps/nexus-runtime.architecture.html + receipt

# B2 (per-PR arch review):
node third_party/Archify/archify/bin/archify.mjs compare architecture docs/nexus-maps/nexus-runtime.base.json docs/nexus-maps/nexus-runtime.head.json docs/nexus-maps/nexus-delta.html --json

# B3: one IR per M-critical flow (router v5, session lifecycle, task sequence), same validate→deliver loop
```

## 4. Principle imports (their PRODUCT.md → our docs)

1. **Truth before spectacle** → maps show only graph-verified edges (`nexus_docs_code_graph.json` EXTRACTED); INFERRED implements-edges labeled as planned until files land.
2. **One spatial narrative first** → each map has ONE primary path (route→tools→report); side detail in cards, never extra edges (their invoke prompt's "8–12 components, cards not edges" rule).
3. **Portable proof** → commit IR JSON + HTML + receipt together; HTML self-contained, no hosted runtime.
4. **Freeze on pass** → validated IR immutable (same rule as OpenResearch experiment nodes, A6).

## 5. What NOT to take

- Brand-marks/social apparatus, gallery/Proof-Lab process weight, motion exports — presentation, not needed.
- Their release/CI machinery (`cargo-dist`-style stager, `release.json` pins) — NEXUS ships Python, not npm skills.
- Do not `dsh plugin add tt-a1i/archify` (repo root, no bundle metadata — their #341).

## 6. Vendor hygiene

- Keep `third_party/Archify` pristine; with OpenResearch decision pending (§6 of that doc), a **single `third_party/` strategy** covers both: submodule(s) preferred over vendoring 75M+ into `main`.
- Cross-link: docs-graph loop #22 was reserved for OpenResearch harness; add loop #23 (`archify_adaptation.md` ↔ `docs/nexus-maps/*.json`) when B1 lands.
