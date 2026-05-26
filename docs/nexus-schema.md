# Nexus Canonical Memory Taxonomy

## Overview

Six canonical document types unify all memory across FAISS (tool retrieval) and ChromaDB (trading knowledge). Every stored artifact maps to exactly one type. This prevents the current confusion where resource metadata, skill descriptions, and trade outcomes are mixed inconsistently.

---

## 1. ResourceDoc

Documents that describe a retrievable operational resource (skill, tool, command, agent, hook, MCP).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `doc_type` | string | yes | `"ResourceDoc"` |
| `id` | string | yes | Canonical ID (maps to `resources.id`) |
| `name` | string | yes | Display name |
| `resource_type` | enum | yes | `skill`, `command`, `agent`, `cli_tool`, `mcp`, `hook`, `design_system`, `craft`, `doc`, `spec` |
| `aliases` | string[] | no | Alternative names for fuzzy matching |
| `description` | string | yes | One-line summary (≤200 chars) |
| `body_text` | string | no | Full content (lazy-loaded) |
| `tier` | int | yes | 1 (fast), 2 (standard), 3 (complex) |
| `env_scope` | string[] | yes | Where this resource is valid (e.g., `["local", "sandbox", "prod"]`) |
| `risk_level` | enum | yes | `safe`, `warning`, `danger` |
| `tool_name` | string | no | Associated MCP/CLI tool name if applicable |
| `args_schema_ref` | string | no | Path or reference to argument schema |
| `version` | string | no | Semver of this resource |
| `status` | enum | yes | `active`, `degraded`, `broken`, `archived` |
| `times_triggered` | int | yes | Invocation counter |
| `times_correct` | int | yes | Successful invocation counter |
| `times_wrong` | int | yes | Failed invocation counter |
| `created_at` | datetime | yes | |
| `updated_at` | datetime | yes | |
| `checksum` | string | no | sha256 of index binary (FAISS) |
| `built_at` | datetime | no | Last FAISS rebuild timestamp |

**Storage:** FAISS index + SQLite `resources` table + `.meta.json` sidecar.

---

## 2. SkillDoc

Documents that describe a reusable workflow extracted from successful task trajectories.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `doc_type` | string | yes | `"SkillDoc"` |
| `id` | string | yes | Canonical ID (`skilldoc-{name}`) |
| `title` | string | yes | Human-readable workflow name |
| `trigger_conditions` | string[] | yes | Natural-language patterns that trigger this skill |
| `preconditions` | string[] | no | Required state before executing |
| `steps` | string[] | yes | Ordered list of execution steps |
| `tools_used` | string[] | yes | Tools/commands used in this workflow |
| `expected_outputs` | string[] | no | What success looks like |
| `failure_modes` | string[] | no | Known failure patterns and mitigations |
| `verification_checks` | string[] | no | How to confirm the skill worked |
| `example_query` | string | yes | Canonical example query that triggers this |
| `source_trajectory_ids` | string[] | yes | Trajectories that produced this SkillDoc |
| `env_tags` | string[] | yes | Environment requirements |
| `reproducibility_score` | float | no | 0-1: how reliably this skill reproduces |
| `created_at` | datetime | yes | |
| `updated_at` | datetime | yes | |

**Storage:** FAISS index (as `resource_type='skilldoc'`) + SQLite `resources` table.

---

## 3. TradingChunk

Documents that describe a trading strategy chunk for RAG retrieval.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `doc_type` | string | yes | `"TradingChunk"` |
| `chunk_id` | string | yes | Unique chunk identifier |
| `setup_name` | string | yes | Strategy name (e.g., "Fair Value Gap") |
| `setup_type` | enum | yes | `entry`, `exit`, `filter`, `confirmation`, `market_structure`, `risk_management`, `psychology`, `trade_management`, `position_sizing`, `philosophy`, `session_filter` |
| `market_condition` | enum | yes | `trending`, `ranging`, `volatile`, `reversal`, `breakout`, `any` |
| `timeframe` | string | yes | e.g., `"1h"`, `"5min"`, `"any"` |
| `instrument` | string[] | yes | Applicable assets (e.g., `["BTC", "ETH"]`) |
| `chunk_text` | string | yes | Main content body |
| `entry_condition` | string | yes | Entry rules |
| `stop_loss_rule` | string | yes | Stop loss description |
| `target_exit_rule` | string | yes | Take profit / exit rules |
| `risk_reward` | string | no | e.g., `"1:3"` |
| `confidence` | float | no | 0-1: confidence in this chunk's accuracy |
| `source` | string | yes | YouTube channel or `"synthetic"` |
| `source_reliability` | float | yes | 0-1: trust weight for this source |
| `recency_weight` | float | yes | 0-1: decays with time since ingestion |
| `outcome_win_rate` | float | no | Win rate from outcome feedback loop |
| `outcome_total_trades` | int | no | Number of trades recorded |
| `outcome_last_updated` | datetime | no | Last outcome sync timestamp |
| `ingested_at` | datetime | yes | When this chunk was added |
| `regime_validity` | string[] | yes | Which regimes this strategy is valid for |

**Storage:** ChromaDB `trading_strategies` collection (metadata + document).

---

## 4. OutcomeDoc

Documents that describe the tracked performance of a strategy over time.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `doc_type` | string | yes | `"OutcomeDoc"` |
| `setup_name` | string | yes | Strategy name (keyed to TradingChunk.setup_name) |
| `win_rate` | float | yes | Overall win rate |
| `total_trades` | int | yes | Total trades recorded |
| `wins` | int | yes | Winning trades |
| `losses` | int | yes | Losing trades |
| `avg_pnl` | float | yes | Average PnL per trade |
| `regime_breakdown` | dict | no | Per-regime win rates: `{"trending": 0.6, "ranging": 0.4}` |
| `best_setup` | string | no | Best-performing specific setup |
| `worst_setup` | string | no | Worst-performing specific setup |
| `last_updated` | datetime | yes | |

**Storage:** ChromaDB `trading_strategies` collection (as metadata on matching TradingChunk) + JSON sidecar at `strategy_db/strategy_performance_db.json`.

---

## 5. FailureDoc

Documents that describe a known failure mode and its correction.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `doc_type` | string | yes | `"FailureDoc"` |
| `id` | string | yes | Canonical ID |
| `query` | string | yes | The query/input that failed |
| `expected_result` | string | yes | What should have happened |
| `actual_result` | string | yes | What actually happened |
| `failure_class` | enum | yes | `wrong_retrieval`, `wrong_tool`, `hallucination`, `timeout`, `crash`, `user_correction` |
| `correction` | string | yes | How to fix or avoid |
| `source_trajectory_id` | string | no | Trajectory where this failure was observed |
| `created_at` | datetime | yes | |

**Storage:** FAISS index (as `resource_type='failuredoc'`) + SQLite `resources` table. These are retrieved only in DEBUG/ADVERSARIAL mode, never in production queries.

---

## 6. TrajectoryRecord

Documents that capture a complete query lifecycle for offline analysis and learning.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `doc_type` | string | yes | `"TrajectoryRecord"` |
| `trajectory_id` | string | yes | Unique ID |
| `session_id` | string | no | Session context |
| `query` | string | yes | User's original input |
| `task_type` | enum | yes | `tooling`, `trading`, `hybrid`, `debug`, `chat` |
| `faiss_results` | json | no | Top-k FAISS results with scores |
| `chroma_results` | json | no | Top-k ChromaDB results with scores |
| `rerank_scores` | json | no | Post-rerank scores |
| `tool_calls` | json | no | Sequence of tool invocations |
| `final_response` | string | no | Generated output |
| `latency_ms` | float | yes | Total query latency |
| `outcome` | enum | no | `correct`, `wrong`, `pending` |
| `user_feedback` | enum | no | `thumbs_up`, `thumbs_down`, `none` |
| `delayed_outcome` | json | no | Outcome recorded after trade/action settled |
| `created_at` | datetime | yes | |
| `resolved_at` | datetime | no | When outcome/delayed_outcome was set |

**Storage:** SQLite `trajectory_store` table (dedicated table, NOT in `resources`).

---

## Schema Versioning

All document types carry implicit versioning via their `doc_type` field. When a field is added:

1. The `doc_type` string remains unchanged (backward-compatible addition)
2. The reading code uses `.get(field, default)` to handle missing fields
3. A migration guide is maintained in `docs/schema-migrations.md`

Current schema version: **1.0**
Last updated: **2026-05-20**
