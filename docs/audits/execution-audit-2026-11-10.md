# Execution Audit — NEXUS local code

Date: 2026-11-10 · Branch: `second-brain-v2` · Method: EGM Goldfish review (`structural.md`, `design_critic.md`), four read-only auditors in fresh context, grouped by execution path. The main agent verified the key claims.

## Scope

56 execution sites (`subprocess`, `Popen`, `check_output`, `os.popen`, `__import__`, `importlib`) across `server/`, `core/`, `scripts/`, `providers/`, plus every HTTP route and MCP tool that can reach them.

## Verified by the main agent

| Claim | Evidence |
|---|---|
| `unified.py` binds `0.0.0.0` | `server/unified.py:1171` |
| `start.sh` runs `unified` on `0.0.0.0` | `start.sh:19` |
| No auth anywhere on the HTTP routes | no `NEXUS_API_KEY` or auth dependency in `server/*.py` |
| The process on port 8000 is the daemon, bound to `127.0.0.1` | `ss -ltnp`: `127.0.0.1:8000`, pid 288888 |
| `unified.py` is **not** running | `/nexus/route` returns 404 on port 8000 |
| `gcode_backend.py:48` runs `os.popen` on an f-string | confirmed |
| `GCODE_BINARY = None` by default, so that path is dormant | `gcode_backend.py:18` |

**Net effect today:** the exposed server is not running. The findings below become live if `start.sh` or `unified.py` is started as documented.

## Findings

### Critical / High

| # | Location | Input and path | Failure scenario | Status | Fix |
|---|---|---|---|---|---|
| H1 | `server/unified.py:1171`, `start.sh:19` | All ~60 routes, no auth | Any host on the LAN can call every route, including those that start tmux sessions and supervisord programs | **Latent** (unified not running) | Bind `127.0.0.1`; add a shared-token dependency to all POST routes |
| H2 | `server/context_harness/channels/target_site.py:124` (`fetch_page`) via `POST /mcp/harness_target_site` (`unified.py:1030-1060`) | Attacker-chosen URL | SSRF to `127.0.0.1`, `169.254.169.254`, and the LAN; no private-address check | **Live once unified runs** | Resolve the host; reject private, loopback and link-local addresses; https only; re-check redirects |
| H3 | `server/context_harness/gcode_backend.py:48` | Search `query` from the MCP tool, interpolated into a shell string | Metacharacters in the query run commands as the NEXUS user | **Dormant** (`GCODE_BINARY=None`) | Replace with `subprocess.run([bin, *args], shell=False, timeout=…, stdin=DEVNULL)`; put `--` before user text. **Fix before the binary is ever configured.** |
| H4 | `server/context_harness/gcode_backend.py:70-72` (`store`) | Key and value are never sent to the command | `store()` returns "stored" but stores nothing, and the child inherits stdin (the MCP JSON-RPC channel) | **Latent** | Pass the payload with `input=`; check the exit code; `stdin=DEVNULL` on every call |

### Medium

| # | Location | Input and path | Failure scenario | Fix |
|---|---|---|---|---|
| M1 | `unified.py:924-948` → `ide_manager.py:256-275` (`/ide/launch`, `/ide/launch-multi`) | `session_name` from the body goes unvalidated into tmux `-s` | Names with `:` or `.` break later `-t` targeting; duplicates fail silently; the registered PID is the shell's, not the IDE's | Validate `^[A-Za-z0-9_-]{1,64}$`; return an error instead of an empty result |
| M2 | `unified.py:1030-1043` (`/mcp/{tool}` with `harness_*`/`context_*` prefix) | Arbitrary body keys passed as kwargs to `call_harness_tool` | Unvalidated arguments reach the next layer | Allowlist kwargs per tool |
| M3 | `context_harness/server.py:688-701` (`harvest` with `ingest=True`) | Fetched page text stored in `project_contexts` | Stored prompt-injection content is later read into LLM context | Mark harvested text untrusted; strip before reuse |
| M4 | `nexus-mcp-enhanced.py:1439-1459` (`ruflo_invoke`) | `tool_name` and `arguments_json` go to the ruflo stdio child with no allowlist | Every ruflo tool is callable, including terminal and browser-eval tools (shell-equivalent). Prompt-injectable through the LLM. | Allowlist tool names; deny `terminal_*`, `browser_eval`, `config_set`, `system_reset` |
| M5 | `nexus-mcp-enhanced.py:162-175` → `forge.py:93-178` (`forge_plan`, `forge_spawn`) | `project_dir` and `brief` | Spawns an `opencode` agent in `project_dir` with a caller-written brief (prompt-injection to an agent with tools) | Confine `project_dir` to registered projects. Local MCP only. |
| M6 | `server/ruflo_proxy.py:87-152` | Child `stderr=PIPE`, stdout not matched by JSON-RPC id, lock held during read | A chatty child fills the 64 KB pipe and blocks; a late reply is returned as the next call's answer; one stuck call serialises all calls | Drain stderr to a log; match ids and discard stale lines; kill and restart the child on timeout |
| M7 | `server/process_supervisor.py:403-407` | `subprocess.run(timeout=10)` with no `TimeoutExpired` handler | `/nexus/ops/bootstrap` returns 500 if supervisord stays in the foreground | Catch and report as a boot failure |
| M8 | `server/ingest/cli_tool.py:49-89` | `bin` from ingest config; `["man", bin]` has no `--`; timeout is re-raised but not caught; `man_proc` not killed | Any absolute path runs as an executable with `--help`; a name starting with `-` is parsed as a man option; ingest crashes on timeout | Validate the name; `["man","--",bin]`; catch `TimeoutExpired`; kill both processes |
| M9 | `server/ingest/git_repo.py:105-110` | `source_path` may be `file://` or a local path; no timeout; no `stdin=DEVNULL`; no `GIT_TERMINAL_PROMPT=0` | Ingests an attacker-chosen local repo; a private URL blocks on a credential prompt; a hung remote blocks forever | Allow https and ssh only; add `timeout`, `stdin=DEVNULL`, `GIT_TERMINAL_PROMPT=0` |

### Low

| # | Location | Finding |
|---|---|---|
| L1 | `context_harness/channels/pubmed_search.py:28-41` | `NCBI_API_KEY` goes in the query string. `basicConfig(INFO)` in `nexus_http_daemon.py:49` and `nexus_daemon.py:104` plus httpx's request logging can write it to logs. **Fix:** set `httpx` logger to WARNING. |
| L2 | `ingest/local_dir.py:37-50`, `ingest/obsidian_vault.py:82-104` | No containment check; symlinks are followed on read. A symlink in a configured directory can pull files from outside it (for example `~/.ssh`) into the index. **Fix:** `resolve()` and `relative_to(root)`; skip symlinks; size cap. |
| L3 | `project_continuity.py` via `project_attach`/`project_note` (`nexus-mcp-enhanced.py:111, :137`) | Appends to `<git_root>/.nexus/memory.md` at any path. **Fix:** require a real git repo. |
| L4 | `repo_map.py` via `nexus-mcp-enhanced.py:150` | Reads any readable directory. **Fix:** restrict to registered projects. |
| L5 | `learning_faithful.py:56` | `int()` on an env var with no bounds: crash on bad input, CPU/memory blow-up on large input. **Fix:** clamp, e.g. 10–5000. |
| L6 | `scripts/nexus_exp.py:34-46` | `--base` has no `--` (option injection); `cmd` has the full inherited env, including API keys; output not size-capped. **Fix:** `--` before base; minimal env. |
| L7 | `orchestrator.py:83-92` | `git status` honours `core.fsmonitor` in the repo's `.git/config`, so a writable malicious repo can run commands. **Fix:** `-c core.fsmonitor=false`. |
| L8 | `ide_manager.py:267-275` | `pane_pid` returns the shell PID; the IDE PID is not recorded. |
| L9 | `ruflo_proxy.py:48-91` | Binary chosen by `PATH` or `~/.npm-global`. Low, but depends on environment. |

## Safe sites (no action)

- `process_supervisor.py:162, :188, :233` — fixed argv, timeouts set; program names are constants.
- `execution_plane.py:32`, `catalog_lifecycle.py:737`, `local_harness/providers.py:20` — fixed argv, fixed cwd, timeouts.
- `forge.py:128` — argv list, `send-keys -l` literal (the project string risk is M5).
- `scripts/verify_grounding.py:57`, `scripts/rebuild_faiss.py:36` — constant argv, timeouts.
- Dynamic imports: `ingest/__init__.py:23-28` (fixed registry), `agent_reach.py:112` (literal names), `router.py:276` (`find_spec` only), `ops_metrics.py`, `fusion.py:89`, `unified.py:1084, :1144`, `harness_mcp_bridge.py:70` (fixed paths).
- SQL: `local_harness/apply_db.py` uses `?` placeholders throughout.
- No `shell=True`, `eval`, `exec`, `pickle.load`, `marshal`, or `yaml.load` on external data.

## Priority fixes

1. **H1** — bind `start.sh` and `unified.py` to `127.0.0.1` now. One-line change, no behaviour change for local use.
2. **H2** — SSRF guard in `target_site.fetch_page` before any remote harvest is enabled.
3. **H3 and H4** — replace the `os.popen` call and fix `store()` before `GCODE_BINARY` is set.
4. **M4** — allowlist ruflo tool names before the MCP server is used with an LLM that reads untrusted text.
5. **M5** — confine `forge` project directories to registered projects.
6. Medium and Low items as normal hardening.

## Decisions for you

- Auth for the unified server: shared token (recommended for LAN use), or loopback-only with no remote access?
- Ruflo allowlist: which tools should the NEXUS MCP expose? The auditors suggest denying terminal, browser-eval, config-set and system-reset.
- Whether harvested web content (M3) should ever be written to `project_contexts`.

## Limits

- Auditors did not read every route handler in full. The end-to-end path from HTTP input to `gcode_backend.search` was not confirmed.
- The live exposure depends on which server is started. Re-run after the production target is decided (see `docs/plans/remediation/REVIEW.md` §4).
