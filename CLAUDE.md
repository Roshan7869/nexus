# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# NEXUS — Self-Learning Resource Router for AI Coding Agents

A Python 3.11+ project. The codebase is split between a FastAPI LLM gateway (`core/`) and an MCP orchestrator (`server/`) that route tasks through a self-learning resource graph backed by SQLite + FAISS.

## Common Commands

All Python commands assume the venv at `/home/roshan/nexus/venv` is active (or use `venv/bin/python3` directly).

```bash
# Install / editable install
pip install -r requirements.txt
pip install -e .                          # exposes `nexus`, `nexus-mcp`, `nexus-http`, `nexus-dashboard`, `nexus-harness`

# Tests
pytest tests/ -q                          # full suite
pytest tests/test_unified.py -v           # single test file
pytest tests/test_context_harness.py::test_foo -v  # single test
pytest tests/ -m "not integration"          # skip real-backend tests
pytest tests/ --run-integration             # run integration tests
pytest tests/ --benchmark                   # run benchmark tests

# CI / ops gates
bash scripts/ci_eval.sh                   # unit + ops gates
bash scripts/ci_eval.sh --ab                # + quick A/B
bash scripts/ci_eval.sh --full-ab           # + full 20-query A/B (slow)

# Run the unified server (LLM gateway + routing on :8000)
bash start.sh
# or
uvicorn server.unified:app --host 0.0.0.0 --port 8000

# Run individual components
python server/nexus-mcp-enhanced.py       # stdio MCP server (used by .mcp.json)
python server/nexus_http_daemon.py        # persistent HTTP execution plane (default :8080)
python server/dashboard/server.py         # ops dashboard (:8765)
python server/context_harness/server.py   # context-harness MCP (optional)

# Database / maintenance
python scripts/init_db.py                 # create/upgrade nexus.db
python scripts/rebuild_faiss.py           # rebuild FAISS resource index
python scripts/benchmark_nexus.py         # run router benchmark
python scripts/run_ops_eval.py --json --out logs/ops_eval_ci.json
```

Formatting is configured in `pyproject.toml` (`tool.black`, line length 100). There is no separate lint script; use `black` or `ruff` manually if needed.

## Project Structure

```
nexus/
├── nexus.yaml                # central YAML config (providers, routing, feature flags)
├── pyproject.toml            # package metadata, deps, pytest + black config
├── requirements.txt          # runtime + dev deps
├── core/                     # LLM gateway: FastAPI server, config, metrics, learner
│   ├── server.py             # /v1/chat/completions proxy
│   ├── learner.py            # Thompson Sampling beliefs
│   ├── config.py             # NexusConfig + LearningConfig (feature-flag source of truth)
│   └── ...
├── server/                   # MCP orchestrator + routing + learning pipeline
│   ├── unified.py            # unified FastAPI entry point (port 8000)
│   ├── nexus-mcp-enhanced.py # stdio MCP server exposing NEXUS tools
│   ├── nexus_http_daemon.py  # persistent HTTP execution plane (port 8080)
│   ├── router.py             # v4 routing pipeline (cluster → ISO → Thompson)
│   ├── learning.py           # outcome tracking, TS, reflection, cluster affinity
│   ├── db.py                 # consolidated DB connection helpers
│   ├── router_v2/
│   │   ├── v5.py             # production unified_route_v5 (USE_UNIFIED_ROUTER=2)
│   │   ├── unified_route.py  # v2 unifier (USE_UNIFIED_ROUTER=1)
│   │   └── fusion.py         # L3 rerank weights
│   ├── context_harness/      # multi-backend context retrieval engine
│   ├── dashboard/            # ops UI
│   └── ...
├── db/
│   ├── nexus.db              # live SQLite DB (gitignored)
│   ├── migrations/           # numbered SQL migrations
│   └── faiss/                # FAISS index files
├── scripts/                  # maintenance, benchmarks, backfills
└── tests/                    # pytest suite; conftest.py sets in-memory DB fixtures
```

## Architecture

### Two systems

1. **LLM Gateway (`core/`)**: proxies chat-completions requests to Ollama Cloud / NVIDIA NIM providers and tracks latency/tokens.
2. **MCP Orchestrator (`server/`)**: exposes NEXUS tools to IDEs, routes user tasks to skills/resources, and learns from outcomes.

### Routing pipeline

Routing is selected by `USE_UNIFIED_ROUTER` (env var) and `core.config.LearningConfig`:

- `USE_UNIFIED_ROUTER=0` — legacy v4 `route_task` in `server/router.py`.
- `USE_UNIFIED_ROUTER=1` — v2 unifier `unified_route` in `server/router_v2/unified_route.py`.
- `USE_UNIFIED_ROUTER=2` — production v5 `unified_route_v5` in `server/router_v2/v5.py`.

Default in `.mcp.json` and `supervisord.conf` is `2`. Rollback: set `USE_UNIFIED_ROUTER=0` and restart the MCP.

The v5 pipeline (L1 intent → L2 topic → L3 rerank → L4 autocut) is documented in `server/router_v2/v5.py:13`. Blend weights live in `server/router_v2/fusion.py` and can be overridden via `NEXUS_V5_W_*` env vars.

### Feature flags

All flags resolve **YAML > env var > code default** via `core.config.LearningConfig`:

| Flag | Env var | Default |
|------|---------|---------|
| Thompson routing | `NEXUS_THOMPSON_ROUTING` | `true` |
| Cluster routing | `NEXUS_CLUSTER_ROUTING` | `false` |
| Reflection | `NEXUS_REFLECTION` | `false` |
| MCP sync | `NEXUS_MCP_SYNC` | `false` |
| Auto invoke | `NEXUS_AUTO_INVOKE` | `false` |

### Process topology

The project uses **split ownership**, not a single parent. See `docs/process_topology.md`.

| Component | Port | Primary owner | Notes |
|-----------|------|---------------|-------|
| Unified server | `:8000` | manual / optional | FastAPI gateway |
| HTTP execution plane | `:8080` | systemd `nexus-daemon.service` | persistent daemon |
| MCP stdio server | n/a | IDE via `.mcp.json` | per-session spawn |
| Dashboard | `:8765` | supervisord | `autostart=true` |
| Context harness | n/a | MCP bridge in-process | `server/harness_mcp_bridge.py` |

`supervisorctl status` showing MCP/HTTP/harness as **STOPPED** is intentional; do not flip `autostart=true` while the IDE or systemd owns those processes.

### Database

Single SQLite DB at `db/nexus.db` (default; override with `NEXUS_DB_PATH`). Key tables:

- `resources` — skills, agents, commands, MCP tools, docs
- `resource_sections` — OKF-style sections per resource
- `resource_edges` — co-trigger / similar-to edges
- `bundle_membership` — project-type and core-infra bundles
- `classification` — project-type classifications
- `reflection_failures` / `reflection_fixes` — self-reflection data
- `outcome_log` / `routing_log` — feedback-loop history

Migrations are in `db/migrations/`. The test suite uses an in-memory DB seeded by `tests/conftest.py`.

## Development Conventions

- Read a file before editing it.
- Keep files under 500 lines; split if growing larger.
- Never create documentation files unless explicitly requested.
- Never save working files or tests to the repository root; use `tests/`, `scripts/`, `docs/`.
- Do not commit `.env`, secrets, or the live `db/nexus.db` (they are gitignored).
- Validate inputs at system boundaries.
- Feature flags must go through `core.config.LearningConfig`; avoid reading env vars directly.
- Scripts that import `core.*` or `server.*` must put the repo root on `sys.path` (several scripts already do this; tests handle it via `conftest.py`).

## Testing Notes

- `pytest.ini` / `pyproject.toml` configure `testpaths = ["tests"]` and `asyncio_mode = auto`.
- Tests marked `integration` hit real backends/network and are skipped unless `--run-integration`.
- Tests marked `benchmark` are skipped unless `--benchmark`.
- `conftest.py` disables learning/routing by default via env vars so unit tests are deterministic.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **nexus** (8712 symbols, 15191 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/nexus/context` | Codebase overview, check index freshness |
| `gitnexus://repo/nexus/clusters` | All functional areas |
| `gitnexus://repo/nexus/processes` | All execution flows |
| `gitnexus://repo/nexus/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
