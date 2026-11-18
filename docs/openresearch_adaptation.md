# OpenResearch → NEXUS Adaptation Plan

**Source:** `third_party/OpenResearch` (cloned 2026-09-14, `--depth 1`, 18M, `openresearch-cli` 0.2.1, MIT)
**Upstream:** https://github.com/alphaXiv/OpenResearch — *"The local-first workspace for research agents and autoresearch."*
**Vendor rule (their AGENTS.md):** treat `third_party/OpenResearch` as **read-only reference** — do not edit it; keep local-only behavior local; Rust `src/` + dashboard `ui/src/`; dev via `scripts/dev-slot.mjs`; `ui/dist` committed; CI `fmt, clippy, test` + version sanity. If we need a patch, fork — never mutate the vendor copy.
**Git note:** `third_party/` is currently untracked (no `.gitignore` entry). Recommendation at §6 — submodule or ignore, not a silent vendor dump.

---

## 1. What OpenResearch is (verified by reads)

| Layer | Implementation | Notes |
|---|---|---|
| CLI `orx` | Rust, `src/main.rs` + `src/commands/` (33 cmds: agent, app, compute, discover, exp, paper, runs, logs, skill, supervise, mcp_gate, plan_gate, …) | `clap` derive, `tokio` multi-thread, `reqwest/rustls` (musl-static-safe), `rusqlite/bundled` |
| Dashboard | `orx up` → axum + SSE/WSS + embedded SPA (`ui/dist` committed) on `http://127.0.0.1:4791` | loopback-only, TLS-free |
| Store | Local SQLite (`src/store.rs`): `runs`, `local_projects`, `local_experiments`, `chat_sessions/messages/turns/queued/wakeups/spawns/leases`, `ssh_host_tests`, `overleaf_links`, `ui_state` | local-first; service account only for orgs/managed compute |
| Harness layer | `src/local/harness/` — **one `Harness` trait** (`mod.rs` ~850 lines) + per-agent impl (`claude.rs`, `codex.rs`, `cursor.rs`, `opencode.rs` 2307 lines) + `detect.rs`, `options.rs`, `plan_gate.rs`, `title.rs` | Capabilities: **detection / chat (`run_turn`) / skill-install**; `registry()` auto-picks-up new file — *"adding a harness is one new file + one line"* |
| OpenCode adapter | `src/local/opencode.rs` + `harness/opencode.rs`: lazily spawned `opencode serve` child, SSE `/event` subscribe + `session.prompt` POST per turn, inline `permission.asked`/`question.asked` (pause-not-finish), `auth.json` provider detect, `opencode models --verbose` model list | transport = loopback HTTP, never browser-exposed |
| Chat core | `src/local/chat/` — `WirePart` normalization (text/tool/prompt), `ResumeCtx`/`SteerMessage`, stored↔wire conversion | every harness normalizes its native event stream to wire parts |
| Experiment plane | `src/plane/local_plane.rs` (`LocalPlane`: list_runs, read_log, view/edit_project, experiment_status/desc, launch, cancel, wait, create_experiment) + `src/local/experiments.rs`, `git.rs` | git worktree per direction, immutable run archives |
| Skills | `SKILL.md` (cardinal rules + quick-ref) + `agent-skills/` (12 modules: experiment-tree, create, compute, instances, git, agent-delegation, evidence, reports, figures, customize, paper, lit-review) + `orx skill <name>` loader | task procedures live in skills, playbook stays durable-only |
| Playbook | `SYSTEM_PROMPT.md` (98 lines, `{token}` substitution: identity, project facts/state, chat contract, exec policy, skill routing) injected per harness natively (Claude `--append-system-prompt-file`, Codex `developerInstructions`, OpenCode `instructions` list) | leading comment stripped at render |
| Literature | `orx discover keyword|embedding|openalex|biorxiv` + `orx paper <id\|url>` (alphaXiv full-text first, OpenAlex/bioRxiv fallback) — **policy: use before any web search for research queries** | alphaXiv is the sister project |
| Compute | `src/jobs/` (localbox, ssh, slurm, k8s, ray, huggingface, modal, tinker, openresearch) — same snapshot runs anywhere, no publish required | `orx up --remote user@host` |
| Policy gates | `plan_gate.rs` (`command_is_readonly`/`decide`), `TURN_WATCHDOG` 30min, `ORX_MAX_ATTEMPTS` 4, `ORX_RETRY_BUDGET` 15s, 32K recovery snapshot | shared by adapters |
| Telemetry | Opt-out coarse events, random install ID, **never** code/prompts/paths/names/tokens (`orx telemetry off`, `--no-telemetry`; source builds send nothing) | model for NEXUS analytics |

**Cardinal rules (SKILL.md — the four non-negotiables):** (1) never edit a node once a run answers it (freeze incl. root; branch children for new ideas); (2) run command + environment fixed contract, inherited verbatim — vary **committed code**, never command/env; (3) hyperparameters in code/config branches, never CLI sweeps; (4) grow tree downward (fan within a round, descend on winner — root-with-many-children-no-grandchildren = failure mode). Repair/refill/promote/stop at each decision point.

---

## 2. Feature × NEXUS map (adopt / adapt / already-have / skip)

| OpenResearch feature | NEXUS today | Verdict | Adaptation (concrete) |
|---|---|---|---|
| `Harness` trait + `registry()` (detect/chat/skill-install, one-file-per-agent) | `server/opencode_adapter.py` PLANNED (M8), single-IDE | **ADOPT pattern** | A1: `server/local_harness/harness.py` — `Harness` Protocol (`id/name/supports_chat/detect/run_turn/skill_shim`) + `registry()` list; M8 OpenCode = first impl; Claude/Cursor later = one file each, zero dispatcher edits (mirrors their mod.rs:16-18) |
| Wire-part normalization (text/tool/prompt) | `ToolSession.to_dict` (`visible_tools`, `context_block`) — no event stream | **ADAPT** | A2: `WirePart` dataclass in harness.py; `OpenCodeAdapter.run_turn` translates SSE → wire parts; `task_start` payload gains optional `stream` contract (additive) |
| Playbook injection (`SYSTEM_PROMPT.md` + `{token}` render + per-harness channel) | `context_block` built in broker (`_build_context_block`) — no durable/task split | **ADOPT** | A3: split `context_block` = playbook (durable: identity, project facts, response contract, skill routing) + task brief (per-session). New `server/local_harness/playbook.py:render(name, id, state, artifacts)`; inject via existing MCP payload (Claude-style) — no new transport |
| `plan_gate` (read-only classifier + decide) | `server/precondition.py:check()` boolean gates (M9 adds subscribe/notify) | **MERGE** | A4: `command_is_readonly(cmd)` heuristic lands in M9 as a derived coeffect (`ctx.require("file:write")` only when non-readonly) — plan_gate semantics, Cordis mechanics |
| Watchdog 30min + retry 4×/15s + 32K snapshot | `CircuitBreaker` + `Bulkhead` (M15 realms) | **MERGE** | A5: `TURN_WATCHDOG`-equivalent per-turn timeout in `LocalHarnessSession.refresh` (default off); retry budget on `run_turn`; recovery tail = newest-UTF8-tail pattern copied verbatim (their `mod.rs:61-70` handles char boundaries — reuse the algorithm) |
| Experiment tree + freeze rules + fixed run command | `routing_log`/`outcome_log` (flat history, mutable) | **ADAPT (phased)** | A6: Phase 1 — adopt the *rules* as session policy (completed session immutable; new idea = new session; same task = same tool-surface contract). Phase 2 — `db/migrations/021_experiments.sql` (`experiments(parent_id, branch, run_command, frozen)`) + `scripts/nexus_exp.py` wrapping git worktrees for eval sweeps (their `orx-git` model) |
| Evidence-in-context (logs/diffs/artifacts tied to run) | `_archive_context` + `project_contexts` table | **ALREADY-HAVE, extend** | A7: artifact dir convention per session (`artifacts/<session_id>/`: logs, diffs, reports) + `report_session` returns artifact manifest (their `orx-reports` analog, NEXUS-flavored) |
| Literature first (`discover`/`paper`, alphaXiv-first policy) | `harness_arxiv/scholar_s2/openalex/crossref` bridge (27 tools) + `DEFAULT_RESEARCH_HARNESS_TOOLS` | **ALREADY-HAVE, adopt policy** | A8: document `orx-lit-review`-style routing in `local_harness_status`/playbook: paper/author/model-release query → alphaXiv/OpenAlex tools *before* generic web search; no code change |
| Skill modules + `orx skill <name>` lazy loader | `resources` table + `find_skills` + bundles (M5 exposes via MCP) | **ALREADY-HAVE, cross-pollinate** | A9: package 3 NEXUS skills in their format (`SKILL.md` + dir) for dogfooding inside OpenResearch sessions: `nexus-router` (route-first), `nexus-eval` (A/B + agreement gate), `nexus-harness` (open/report/reconcile). Install via `orx skills add` — validates our MCP surface from the outside |
| Local-first SQLite + loopback dashboard + opt-out telemetry | SQLite SoT + `:8765` dashboard + no telemetry | **ADOPT telemetry pattern** | A10: any future NEXUS analytics copies their contract verbatim (opt-out, random ID, never code/prompts/paths); dashboard borrows their views (tree, runs table newest-first, log ranges) as new `:8765` panels — no new port |
| Multi-compute (SSH/Slurm/K8s/…) same snapshot | Single-host (systemd/supervisord/stdio) | **SKIP** | Out of scope for IDE-local harness; revisit only if NEXUS eval needs remote GPUs |
| Overleaf/LaTeX/paper/figures pipeline | None | **SKIP** | Research-paper *production* is not NEXUS's job; `orx-paper` stays upstream |

---

## 3. Design principles to import (with landing spots)

1. **Registry over dispatch** (their mod.rs:16-18) → A1: new harness = one file + one `registry()` line. Kills the if-IDE switch statements before they grow.
2. **Durable playbook vs task procedures** → A3: SYSTEM_PROMPT carries only every-turn context; procedures live in skills. Our `context_block` bloat drops.
3. **Fixed contract, varied code** (cardinal rule 2) → A6: same task ⇒ same tool-surface contract across sessions; comparability of `report_outcome` requires it (today's Thompson beliefs assume it implicitly — make it explicit).
4. **Freeze answered nodes** (rule 1) → session immutability: `end_session` rows never mutated (already true — now *policy*, with `local_harness_reconcile` refusing to touch `completed` rows).
5. **Local-only by default, explicit capability client** (their AGENTS.md) → our M12/M16 posture already matches (Redis off, stdio-only); keep it.
6. **Recovery-safe streaming** (tail/snapshot/wedged-turn) → A5: copy the UTF-8-safe tail + wedged-turn error (generous bound, clear recoverable error).

## 4. What NOT to take

- **Rust** — do not port; adapt patterns in Python. Different runtime, different owners.
- **Remote/managed compute, instances, orgs, login/OAuth** — service-owned; NEXUS stays local (their own split says so).
- **`ui/dist` embedding workflow** — NEXUS dashboard is server-rendered; no SPA embedding needed.
- **LaTeX/paper pipeline** — upstream's domain, not ours.

## 5. Phased plan (extends M1–M20, does not replace)

- **P0 (this week, docs-only + 1 file):** A3 playbook split design + A8 literature-policy note in `local_harness_status`; A9 package one skill (`nexus-router/SKILL.md`) and `orx skills add` it into an OpenResearch session for outside-in validation.
- **P1 (with M5–M8):** A1 `harness.py` Protocol + registry (OpenCode first); A2 wire-parts on the new path only.
- **P2 (with M9/M15):** A4 read-only coeffect + A5 watchdog/retry/snapshot constants.
- **P3 (after M18):** A6 session-immutability policy → `021_experiments.sql` + `nexus_exp.py` worktree sweeps; A7 artifact dirs + manifest in `report_session`.
- **P4 (hardening):** A10 telemetry contract (only if analytics is requested) + dashboard tree/runs views.

## 6. Vendor hygiene

- Keep `third_party/OpenResearch` pristine (`git status` clean for that path). Decide: **git submodule** (preferred — tracks upstream, 18M not vendored) or **`.gitignore`d vendor** (if offline builds need it). Do NOT commit 18M silently into `main`.
- Their `.github/workflows/ci.yml` (`fmt, clippy, test`, version sanity) applies to *their* code only — NEXUS CI (`scripts/ci_eval.sh`) is unaffected.
- Cross-links: god-loop #21 (research grounding) gains a second root — this doc. Add `docs/nexus_docs_code_graph.json` loop #22 (`openresearch_adaptation.md` ↔ `server/local_harness/harness.py`) when A1 lands.
