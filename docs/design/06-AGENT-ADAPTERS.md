# NEXUS — Agent Adapters (backlog W5)

Status: DRAFT v1 · 2026-11-11 · design only, no code
Companions: `docs/local_harness/M08-opencode-adapter.md`, `docs/local_harness/README.md`, `05-SITES-REGISTRY.md`

Scope: let Claude Code, Codex and Hermes use NEXUS routing the way OpenCode does today.

Conventions: every claim is tagged with a repo path (`file:line`) or marked **UNVERIFIED**. Nothing in this
document is an external API fact unless a repo file states it. No agent-vendor documentation was consulted,
so all vendor-side behaviour (how Codex or Hermes load MCP servers or hooks) is UNVERIFIED.

Naming note: in `docs/rca/MASTER-PLAN.md:145`, "W5" is wave 5 of the RCA plan (task `F7b`), unrelated to
agent adapters. This document uses "W5" only as the backlog label the request supplied.

---

## 1. Current OpenCode adapter contract

### 1.1 Components (all exist in repo)

| Piece | Location | Role |
|---|---|---|
| Adapter module | `server/opencode_adapter.py` | `IDE_NAME`, `get_opencode_config()`, `session_payload()`, `report_mapping()` |
| Harness registry | `server/local_harness/harness.py` | `Harness` Protocol, `OpenCodeHarness`, `REGISTRY`, `get(ide)` |
| MCP tools | `server/nexus-mcp-enhanced.py:1617-1770` | `local_harness_open`, `local_harness_report`, `local_harness_reconcile`, `local_harness_status` |
| Session wrapper | `server/local_harness/session.py` | `open_local_session`, `dispose_by_id`, `LocalHarnessSession` |
| Legacy tools | `server/nexus-mcp-enhanced.py:686,718` | `task_start`, `task_complete` (default `ide="claude-code"`) |
| HTTP contract | `server/execution_plane.py` (header), `server/nexus_http_daemon.py:201-285` | `POST /v1/session/open`, `POST /v1/session/{id}/report`, `GET /v1/session/{id}`, `GET /v1/health` |
| Launcher | `opencode-with-nexus.sh` | starts `uvicorn server.unified:app` on `:8000`, exports `NEXUS_IDE`, `NEXUS_LOCAL_HARNESS`, runs `opencode` |
| MCP config | `.mcp.json` entry `nexus` | stdio spawn of `server/nexus-mcp-enhanced.py`, with `"NEXUS_IDE": "claude-code"` |

### 1.2 The adapter surface (`server/opencode_adapter.py`)

- `IDE_NAME = "opencode"`.
- `get_opencode_config() -> dict`: static dict with `transport: "stdio"`, `command: ["venv/bin/python3", "server/nexus-mcp-enhanced.py"]`,
  `env: {USE_UNIFIED_ROUTER: "2", NEXUS_IDE: "opencode", NEXUS_LOCAL_HARNESS: "1"}`, and an optional `http` block
  (`http://127.0.0.1:8000`). It is a description of how to spawn NEXUS, not a call into any agent.
- `session_payload(d) -> dict`: projects a `ToolSession.to_dict()` to `{session_id, tools, context, instructions}`.
  It reads `visible_tools` (produced by `ToolSession.to_dict`, `tool_session_broker.py:46-53`) and `context_block`
  (key presence in `to_dict` is **UNVERIFIED**; the adapter defaults it to `""`).
- `report_mapping(outcome) -> str`: delegates to `ToolSessionBroker._normalize_outcome` (`tool_session_broker.py:540`), which maps
  `success/passed/done/complete(d)` to `correct`, `failed/fail/error` to `wrong`, `partial/mixed/skip*/cancel*` to `partial`, anything else to `correct`.
  The adapter deliberately does not fork this table.

Observation: `session_payload` is not called by any non-test code. `local_harness_open` returns
`lh.inner.to_dict()` plus extras directly (`nexus-mcp-enhanced.py:1636-1646`), so the OpenCode projection is currently a
tested but unwired helper. Caller grep for `opencode_adapter|get_opencode_config` returned only `server/local_harness/harness.py:54`
(detect), `tests/test_local_harness.py:255,265,266`, and `scripts/verify_grounding.py:40`.

### 1.3 Session lifecycle as the agent sees it

1. Agent calls MCP tool `local_harness_open(task, project="default", budget=8, ide="opencode", task_type="general")`.
   - If `NEXUS_LOCAL_HARNESS != "1"`, it delegates to `task_start` unchanged (`nexus-mcp-enhanced.py:1630-1631`).
   - Otherwise `open_local_session` creates a `NexusContext`, calls `broker.start_session(..., max_tools=min(max(1,budget),8), ctx=ctx)`,
     wraps the result, declares cluster coeffects, binds task sites, and registers the wrapper in a process-local `_WRAPPERS` dict (`session.py`).
2. Response is JSON: broker `to_dict()` fields (including `session_id`, `visible_tools`) plus `session_kind: "local_harness"`, `realm`,
   `site_status`, `mcp_proxy`, `instructions`.
3. Agent works using only `visible_tools`.
4. Agent calls `local_harness_report(session_id, outcome="correct", notes="", quality=1.0, skill_name="")`.
   `dispose_by_id` calls `execution_plane.report_session` first (belief_delta), then pops and LIFO-disposes the wrapper.
   Response is the `report_session` dict plus `sites_released`.

Failure behaviour recorded in code: a server restart between open and report loses `_WRAPPERS`; report still completes via the broker
path and wrapper inverses are skipped (`session.py` comment on `_WRAPPERS`).

### 1.4 IDE identity plumbing

`ide` is an enum-like string normalised in several independent places. All already accept `codex`, `hermes`, `claude-code`, `opencode`:

| Site | Allowed set |
|---|---|
| `server/execution_plane.py:_sanitize_ide` | opencode, claude-code, unknown, hermes, codex, mcp, auto_invoke, http (+aliases `claude`, `claude_code`, `codenomad`, `open-code`) |
| `server/router_cutover.py:166-186` | above plus eval, pytest; falls back to `NEXUS_IDE` env |
| `server/broker_runtime.py:62` `ALL_IDES` | codex, opencode, hermes, claude-code, unknown |
| `server/broker_runtime.py:104-106` `normalize_legacy_ide` | collapses to opencode / claude-code / unknown only (codex and hermes become `unknown` in legacy tables) |
| `server/db.py:195,215` CHECK constraints | opencode, claude-code, unknown, hermes, codex, mcp, auto_invoke, http, eval, pytest |
| `server/learning.py:371` | opencode, claude-code, unknown, hermes, codex |
| `server/ide_manager.py:250-252` `TMUX_LAUNCH_CMD` | maps `claude-code`->`claude`, `codex`->`codex`, `hermes`->`hermes`, plus aider |

Consequence: identity is already wired for all three target agents. The gap is the adapter layer (config, payload shape, registry entry, launch/wiring docs), not the enum.

### 1.5 Harness registry

`Harness` Protocol (`harness.py:28-34`): `id()`, `name()`, `supports_chat()`, `detect() -> HarnessInfo | None`, `async run_turn(session, message) -> list[WirePart]`, `skill_shim() -> str | None`.
`OpenCodeHarness` is the only implementation; `run_turn` raises `UnsupportedHarness`, `skill_shim` returns `None`. `REGISTRY = [OpenCodeHarness()]`.
`get(ide)` resolves by id, defaults to OpenCode and never raises for unknown ids (so `get("codex")` today silently returns OpenCode, a pitfall noted in section 5).

---

## 2. Common adapter interface

### 2.1 Goal and non-goals

Goal: one shape for per-agent facts so `local_harness_open` / `local_harness_report` and the HTTP execution plane stay agent-agnostic.
Non-goals: no change to routing, scoring, or session semantics; no streaming `run_turn` (already deferred to "A2" in `harness.py:63`).

### 2.2 Proposed interface (design-level, not code)

Each agent adapter module exposes the same four functions that `opencode_adapter.py` already has, parameterised by agent:

| Function | Contract | Source of truth |
|---|---|---|
| `IDE_NAME` | One of the strings in section 1.4; must pass `_sanitize_ide` unchanged | repo |
| `get_<agent>_config() -> dict` | Same keys as OpenCode: `transport`, `command`, `env` (must set `NEXUS_IDE`, `NEXUS_LOCAL_HARNESS`), optional `http` | repo pattern |
| `session_payload(tool_session_dict) -> dict` | Same four keys as OpenCode (`session_id`, `tools`, `context`, `instructions`); `instructions` may differ per agent only in the tool names it cites | repo pattern |
| `report_mapping(outcome) -> str` | MUST delegate to `ToolSessionBroker._normalize_outcome`; no per-agent tables | repo rule (M08) |

To avoid three near-copies, factor the shared body once and let each agent supply only constants (`IDE_NAME`, extra env, instruction text).
Recommended: a small shared base (for example `server/agent_adapters/base.py`) with `OpenCodeAdapter` re-expressed on it and `server/opencode_adapter.py`
kept as a thin re-export so the existing tests and `harness.py:54` import keep working. This is a proposal, not an existing structure.

Registry: add one `Harness` implementation per agent and one `REGISTRY` line each (`harness.py:70`, "ONE line per future harness").
`detect()` should be side-effect free: report `installed` from adapter config presence, and (optionally) from `shutil.which` of the launch command in
`TMUX_LAUNCH_CMD`. Whether `which` is acceptable inside `detect()` is an open question (section 4).

### 2.3 Transport matrix

| Path | Entry | Agents it fits | Status |
|---|---|---|---|
| MCP stdio | `server/nexus-mcp-enhanced.py` tools `local_harness_*` / `task_*` | any agent that can launch an MCP stdio server | exists |
| HTTP execution plane | `POST /v1/session/open`, `POST /v1/session/{id}/report` on the daemon | any agent or wrapper that can make HTTP calls; documented for "AgentRouter / Hermes / CodeNomad" in `execution_plane.py:1` | exists |
| OpenAI-compatible proxy | `/v1/chat/completions`, `/nexus/event` on `:8000` | agents that can point at a custom OpenAI base URL | README diagram only (`README.md:12-25`); route list **UNVERIFIED** against code |
| tmux launch | `ide_manager.launch_tmux` | codex, hermes, claude-code, opencode, aider | exists |

Two ports appear in the repo: `:8000` (`opencode-with-nexus.sh`, README) and `:8080` (`NEXUS_DAEMON_URL` in `.mcp.json`; `nexus_http_daemon.py`).
Which process serves which route is an open question (section 4).

### 2.4 Invariants every adapter must keep

1. `NEXUS_IDE` set to the agent's `IDE_NAME` in the spawned MCP env, or `ide=` passed explicitly on each open.
2. Outcome normalisation through the single broker function.
3. With `NEXUS_LOCAL_HARNESS=0`, behaviour is identical to legacy `task_start` / `task_complete`.
4. Adapter module imports no `server.*` at top level (lazy imports), per M08 build step 1.
5. Adapters never write to `db/nexus.db` directly; all persistence goes through the broker and `report_session`.

---

## 3. Per-agent differences

Only repo-supported facts are stated as facts. Everything about how the vendor product behaves is UNVERIFIED.

### 3.1 Claude Code (via MCP)

Repo-supported:
- `.mcp.json` already registers a `nexus` stdio server (`venv/bin/python3 server/nexus-mcp-enhanced.py`) with `NEXUS_IDE=claude-code`, `NEXUS_LOCAL_HARNESS=1`, `USE_UNIFIED_ROUTER=2`, and DB path env.
- `task_start` defaults `ide="claude-code"` (`nexus-mcp-enhanced.py:688`), so the legacy path is Claude-Code-first.
- `local_harness_open` defaults `ide="opencode"` (`:1621`). A Claude Code caller must pass `ide="claude-code"` or rely on the env default; the tool does not read `NEXUS_IDE` for its default. This is a concrete gap.
- `ide_manager` can launch it in tmux as `claude` (`ide_manager.py:250`).
- `README.md:12-25` shows Claude Code connecting "MCP" (diagram only).

Needed for the adapter: a `claude_code_adapter` (config echoing the `.mcp.json` entry; `session_payload`), a `ClaudeCodeHarness` registry line, and a decision on the `ide` default in `local_harness_open` (open question Q2).

UNVERIFIED: how Claude Code surfaces tool names from an MCP server (naming prefix such as `mcp__nexus__...`), whether it can restrict its own tool surface to `visible_tools`, and hook/permission behaviour. The instruction text "Use only visible_tools" is advisory; enforcement is not established by the repo.

### 3.2 Codex

Repo-supported:
- `codex` is an accepted `ide` everywhere in section 1.4 and launched as `codex` by `ide_manager.py:251`.
- `README.md:12-25` diagram lists "Codex ... OpenAI" connecting to the proxy (OpenAI-compatible path), not MCP.
- `server/intent_classifier.py:83` mentions "codex" only as a keyword tied to the word "get"; not an integration.
- `normalize_legacy_ide` maps `codex` to `unknown` for legacy tables (`broker_runtime.py:106`), which loses attribution there.

UNVERIFIED: whether Codex can load an MCP stdio server and how it is configured (config file name, format, env passthrough); whether it can override its OpenAI base URL; its tool-calling and session semantics; whether it has hooks. Two candidate integration paths follow from the repo, and both need vendor verification before building:
- Path A, MCP stdio: reuse `get_<agent>_config()` shape with `NEXUS_IDE=codex`.
- Path B, HTTP/proxy: use `POST /v1/session/open|report` from a wrapper script, as `opencode-with-nexus.sh` does for the HTTP server.

### 3.3 Hermes

Repo-supported:
- `hermes` is an accepted `ide`; `ide_manager.py:251` launches `hermes`; the `execution_plane.py` docstring names Hermes as a consumer of the HTTP contract ("AgentRouter / Hermes / CodeNomad"); `README.md` diagram shows "HERMES ... CLI".
- `catalog_lifecycle.py:530` and `router.py:558` treat `hermes-skill-` as a resource name prefix, i.e. Hermes skills exist in the catalog.
- `nexus-mcp-enhanced.py:84,495` list `hermes` as a *project* name (kaari, vihaar, hermes, trading). This is a different meaning of "hermes" (a project, not the agent) and must not be conflated in the adapter's `project` vs `ide` arguments.

UNVERIFIED: what Hermes is as a product (the repo never defines it beyond the above), whether it speaks MCP, HTTP, or only CLI, its config format, and its session model. The README labels it "CLI", which suggests a wrapper-script integration over the HTTP contract (Path B), but that is an inference and is not confirmed.

### 3.4 Comparison

| Aspect | OpenCode (today) | Claude Code | Codex | Hermes |
|---|---|---|---|---|
| `IDE_NAME` accepted by enums | yes | yes | yes | yes |
| Adapter module | `opencode_adapter.py` | none | none | none |
| Registry entry | `OpenCodeHarness` | none | none | none |
| Launcher in repo | `opencode-with-nexus.sh` | `.mcp.json` only (+ tmux) | tmux only | tmux only |
| Repo-indicated transport | MCP stdio + HTTP | MCP stdio | proxy (README) | CLI / HTTP contract (README, docstring) |
| Vendor config details | known to repo (`opencode.json` referenced in shell comment; file not in repo listing, **UNVERIFIED**) | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Legacy-table attribution | kept | kept | collapsed to `unknown` | collapsed to `unknown` |

---

## 4. Open questions

1. **Q1 Scope of "W5".** The RCA plan uses W5 for `F7b`. Confirm the intended backlog item and its acceptance criteria.
2. **Q2 `ide` default.** Should `local_harness_open` default `ide` from `NEXUS_IDE` (env already set per agent in `.mcp.json`) instead of the literal `"opencode"`? Changing it edits an existing function; callers must be grepped first (`grep -rn local_harness_open --include=*.py`) and the compat test `test_local_flag_contract` re-run.
3. **Q3 Codex and Hermes integration path.** MCP stdio, OpenAI-compatible proxy, or HTTP wrapper? Requires vendor documentation (UNVERIFIED throughout).
4. **Q4 Port and process map.** `:8000` (`server.unified:app`) vs `:8080` (`NEXUS_DAEMON_URL`, `nexus_http_daemon.py`): which serves `/v1/session/*`, `/v1/chat/completions`, `/nexus/event`? Needed so adapter `http.url` is correct.
5. **Q5 Legacy attribution.** `normalize_legacy_ide` collapses `codex` and `hermes` to `unknown`. Is that acceptable, or does the legacy enum need widening (a migration, out of scope here)?
6. **Q6 `session_payload` wiring.** It is unused by the live MCP path (section 1.2). Should adapters actually shape the response, or stay a tested helper?
7. **Q7 Enforcement.** "Use only visible_tools" is advisory. Is any per-agent enforcement (permission lists, allow-lists) in scope?
8. **Q8 `get()` fallback.** `harness.get("codex")` returns OpenCode silently. Keep the never-raise compat, or return an explicit unknown marker once more harnesses exist?
9. **Q9 `detect()` semantics.** May it probe the filesystem or `PATH`, or must it stay config-only as `OpenCodeHarness.detect` is today?
10. **Q10 Hermes meaning.** Confirm the agent "Hermes" is distinct from the `hermes` project name and from `hermes-skill-*` catalog entries.
11. **Q11 Stale doc.** M08 describes `server/opencode_adapter.py` as "CREATE, replace lost connector", but the file exists and is tested; its shell wrapper references `opencode.json`, which was not located. Update the M-doc status separately.

---

## 5. Test plan

Principles: DB-free where possible (match the existing "Wave 1" tests in `tests/test_local_harness.py:253-300`); never touch `db/nexus.db`; run with `venv/bin/python3 -m pytest`.

### 5.1 Unit (per adapter, parametrised over claude-code / codex / hermes / opencode)

| Test | Assertion |
|---|---|
| `test_adapter_ide_name_sanitized` | `IDE_NAME` is unchanged by `execution_plane._sanitize_ide` and `router_cutover._sanitize_ide` |
| `test_adapter_config_shape` | `transport`, `command`, `env` present; `env["NEXUS_IDE"] == IDE_NAME`; `NEXUS_LOCAL_HARNESS == "1"` |
| `test_adapter_payload_projection` | the four keys; `tools` equals `visible_tools`; missing keys default safely |
| `test_report_mapping_matches_broker` | for each outcome string in the normalizer table plus unknown and empty input, adapter result equals `ToolSessionBroker._normalize_outcome` |
| `test_adapter_no_toplevel_server_imports` | importing the module in a clean interpreter does not import `server.tool_session_broker` |

### 5.2 Registry

- `test_registry_has_entry_per_agent`: `get("codex").id() == "codex"` etc.
- `test_registry_unknown_still_defaults`: unknown id returns the default without raising (preserves current compat, `test_registry_default_and_unknown`).
- `test_detect_is_side_effect_free`: `detect()` returns `HarnessInfo` or `None`, never raises, with `PATH` emptied.
- `test_run_turn_unsupported`: raises `UnsupportedHarness` until A2.

### 5.3 Integration (broker-backed, temp DB only)

Mirror `test_broker_ctx_round_trip` and `test_report_disposes_wrapper`, using a temporary `NEXUS_DB_PATH` (never the live DB):
- open with `ide=<agent>`, assert the persisted/returned session records that ide and `visible_tools` is non-empty;
- report with each synonym outcome and assert `belief_delta` present and wrapper popped;
- `NEXUS_LOCAL_HARNESS=0` yields output equal in shape to `task_start` (extends `test_local_flag_contract`).

### 5.4 MCP stdio smoke (per agent config)

Spawn the command from `get_<agent>_config()` with its env, list tools, confirm the four `local_harness_*` tools plus `task_start`/`task_complete`, then do open and report round-trips. This validates the NEXUS side only; it does not prove the vendor agent can use it (UNVERIFIED until run against the real product).

### 5.5 HTTP contract

Against the daemon with a temp DB: `POST /v1/session/open` with `ide` in {claude-code, codex, hermes}, then `POST /v1/session/{id}/report`; assert `contract == "nexus.execution_plane.v1"` fields and ide persisted per the CHECK constraint.

### 5.6 Regression gates (existing, from `docs/local_harness/README.md`)

`pytest tests/ -q`, `bash scripts/ci_eval.sh`, `tests/test_local_harness.py` unchanged and green, and `scripts/verify_grounding.py` still passing for M08.

### 5.7 Manual vendor verification (blocked on UNVERIFIED items)

For each of Codex and Hermes: confirm from vendor docs or a local run (a) how it registers an MCP server or HTTP endpoint, (b) env passthrough, (c) tool naming, (d) whether a session boundary can trigger open and report. Record results here and remove the UNVERIFIED tags only with a cited source.
