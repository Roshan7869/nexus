#!/usr/bin/env python3
"""NEXUS Orchestrator Tools Integration Layer — gstack + ruflo + graphify assistants.

Adds external orchestrator/routing tools as assistants to the NEXUS planning
protocol. Zero edits to existing routing code. All integration is additive.

Usage:
    from orchestrator_tools import (
        resolve_orchestrators,
        enhance_plan,
        invoke_orchestrator,
        report_orchestrator_outcome,
        get_registry_stats,
    )

    # Find orchestrator tools for a task in a specific NEXUS phase
    tools = resolve_orchestrators("deploy to production", "deploy")

    # Enhance a plan DAG with orchestrator tool mappings
    enhanced = enhance_plan(plan, task)

    # Invoke an orchestrator tool by registry key
    result = invoke_orchestrator("gstack:ship", {"task": "ship feature"})

    # Report outcome back to NEXUS learning
    report_orchestrator_outcome("gstack:qa", "correct", "QA passed all checks")
"""

import json
import logging
import subprocess
import shlex
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / 'nexus/db/nexus.db'
GRAPHIFY_OUT = Path.home() / 'Downloads' / 'Algotrading' / 'graphify-out'


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR REGISTRY — Phase-Indexed
# ═══════════════════════════════════════════════════════════════════════════
# Maps NEXUS phase types to orchestrator tools that assist during that phase.

PHASE_TOOLS = {
    "research": [
        {
            "key": "gstack:office-hours",
            "provider": "gstack",
            "name": "office-hours",
            "type": "skill",
            "description": "YC Office Hours — startup diagnostic or builder-mode brainstorming",
            "trigger": "brainstorm this / is this worth building / office hours",
            "invoke": {"method": "skill_load", "skill_name": "office-hours"},
        },
        {
            "key": "gstack:scrape",
            "provider": "gstack",
            "name": "scrape",
            "type": "skill",
            "description": "Web scraping via browser daemon for competitive/intel research",
            "trigger": "scrape page data / research competitors / pull from site",
            "invoke": {"method": "skill_load", "skill_name": "scrape"},
        },
        {
            "key": "gstack:skillify",
            "provider": "gstack",
            "name": "skillify",
            "type": "skill",
            "description": "Codify a scrape flow into permanent browser-skill",
            "trigger": "save this scrape / make scrape permanent",
            "invoke": {"method": "skill_load", "skill_name": "skillify"},
        },
        {
            "key": "gstack:learn",
            "provider": "gstack",
            "name": "learn",
            "type": "skill",
            "description": "Search/prune/export project learnings across sessions",
            "trigger": "show learnings / what have we learned / search learnings",
            "invoke": {"method": "skill_load", "skill_name": "learn"},
        },
        {
            "key": "ruflo:hooks_route",
            "provider": "ruflo",
            "name": "hooks_route",
            "type": "mcp_tool",
            "description": "Route task to optimal agent via learned patterns",
            "trigger": "route task / find best agent / which agent handles",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_route"},
        },
        {
            "key": "ruflo:memory_search",
            "provider": "ruflo",
            "name": "memory_search",
            "type": "mcp_tool",
            "description": "Semantic vector search across past patterns and learnings",
            "trigger": "search memory / find past pattern / recall similar",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "memory_search"},
        },
        {
            "key": "ruflo:memory_search_unified",
            "provider": "ruflo",
            "name": "memory_search_unified",
            "type": "mcp_tool",
            "description": "Search across both Claude Code and ruflo memory",
            "trigger": "unified search / search all memory / find across systems",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "memory_search_unified"},
        },
        {
            "key": "ruflo:embeddings_search",
            "provider": "ruflo",
            "name": "embeddings_search",
            "type": "mcp_tool",
            "description": "ONNX-based embedding search — alternative to sentence-transformers",
            "trigger": "semantic search / embedding compare / vector search",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "embeddings_search"},
        },
        {
            "key": "ruflo:agentdb_pattern_search",
            "provider": "ruflo",
            "name": "agentdb_pattern_search",
            "type": "mcp_tool",
            "description": "HNSW-indexed pattern search via ReasoningBank (150x faster)",
            "trigger": "pattern search / reasoning bank / hnsw patterns",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "agentdb_pattern-search"},
        },
        {
            "key": "ruflo:analyze_diff",
            "provider": "ruflo",
            "name": "analyze_diff",
            "type": "mcp_tool",
            "description": "AST-based diff analysis, MinCut boundaries, Louvain communities",
            "trigger": "analyze diff / code analysis / dependency graph",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "analyze_diff"},
        },
        {
            "key": "nexus:plan-executor",
            "provider": "nexus",
            "name": "plan-executor",
            "type": "service",
            "description": "NEXUS Plan Executor — persistent phase/task/subtask engine with OOC/OOM-safe checkpoint-resume",
            "trigger": "create plan / execute plan / resume plan / checkpoint / long task / phase tracking",
            "invoke": {"method": "module", "module": "nexus_plan_executor", "class": "PlanExecutor"},
        },
        {
            "key": "nexus:plan-executor-resume",
            "provider": "nexus",
            "name": "plan-executor-resume",
            "type": "service",
            "description": "Resume a plan from DB checkpoint — survives context reset, picks up exactly where left off",
            "trigger": "resume / continue / pick up / restore plan / reload context",
            "invoke": {"method": "module", "module": "nexus_plan_executor", "class": "PlanExecutor", "method": "resume_plan"},
        },
        {
            "key": "gstack:benchmark-models",
            "provider": "gstack",
            "name": "benchmark-models",
            "type": "skill",
            "description": "Cross-model benchmark — Claude vs GPT vs Gemini side-by-side",
            "trigger": "compare models / model benchmark / which model is best / cross model",
            "invoke": {"method": "skill_load", "skill_name": "benchmark-models"},
        },
        {
            "key": "gstack:gstack-upgrade",
            "provider": "gstack",
            "name": "gstack-upgrade",
            "type": "skill",
            "description": "Upgrade gstack to the latest version with migration",
            "trigger": "upgrade gstack / update gstack / get latest gstack version",
            "invoke": {"method": "skill_load", "skill_name": "gstack-upgrade"},
        },
        {
            "key": "graphify:state-analysis",
            "provider": "graphify",
            "name": "graphify-state-analysis",
            "type": "service",
            "description": "Graphify knowledge graph state analysis — 6676-node component map for Phase 1 planning",
            "trigger": "graph state / knowledge graph / component map / what exists / codebase structure",
            "invoke": {"method": "module", "module": "graphify_nexus_bridge", "function": "graphify_state_analysis"},
        },
        {
            "key": "graphify:component-context",
            "provider": "graphify",
            "name": "graphify-component-context",
            "type": "service",
            "description": "Get component neighbors, community, type from graphify KG for Phase 2 planning",
            "trigger": "component context / neighbor analysis / dependency graph / what depends on",
            "invoke": {"method": "module", "module": "graphify_nexus_bridge", "function": "graphify_component_context"},
        },
        {
            "key": "graphify:impact-analysis",
            "provider": "graphify",
            "name": "graphify-impact-analysis",
            "type": "service",
            "description": "Given changed files, find all potentially affected components via edge traversal",
            "trigger": "impact analysis / change impact / affected components / what breaks",
            "invoke": {"method": "module", "module": "graphify_nexus_bridge", "function": "graphify_impact_analysis"},
        },
    ],

    "design": [
        {
            "key": "gstack:plan-ceo-review",
            "provider": "gstack",
            "name": "plan-ceo-review",
            "type": "skill",
            "description": "CEO/founder-mode plan review — 4 scope modes",
            "trigger": "think bigger / expand scope / strategy review",
            "invoke": {"method": "skill_load", "skill_name": "plan-ceo-review"},
        },
        {
            "key": "gstack:plan-eng-review",
            "provider": "gstack",
            "name": "plan-eng-review",
            "type": "skill",
            "description": "Engineering plan review — architecture, data flow, coverage",
            "trigger": "review architecture / eng plan / tech review",
            "invoke": {"method": "skill_load", "skill_name": "plan-eng-review"},
        },
        {
            "key": "gstack:plan-design-review",
            "provider": "gstack",
            "name": "plan-design-review",
            "type": "skill",
            "description": "Designer's eye plan review — rates dimensions 0-10",
            "trigger": "design review / ux critique / visual plan check",
            "invoke": {"method": "skill_load", "skill_name": "plan-design-review"},
        },
        {
            "key": "gstack:plan-devex-review",
            "provider": "gstack",
            "name": "plan-devex-review",
            "type": "skill",
            "description": "Developer experience plan review — DX expansion/polish/triage",
            "trigger": "dx review / developer experience / API design review",
            "invoke": {"method": "skill_load", "skill_name": "plan-devex-review"},
        },
        {
            "key": "gstack:autoplan",
            "provider": "gstack",
            "name": "autoplan",
            "type": "skill",
            "description": "Auto-review pipeline: runs CEO + eng + design + DX reviews sequentially",
            "trigger": "run all reviews / auto review / full plan review",
            "invoke": {"method": "skill_load", "skill_name": "autoplan"},
        },
        {
            "key": "gstack:design-consultation",
            "provider": "gstack",
            "name": "design-consultation",
            "type": "skill",
            "description": "Full design system from scratch — aesthetic, typography, color",
            "trigger": "design system / brand guidelines / create DESIGN.md",
            "invoke": {"method": "skill_load", "skill_name": "design-consultation"},
        },
        {
            "key": "gstack:design-shotgun",
            "provider": "gstack",
            "name": "design-shotgun",
            "type": "skill",
            "description": "Multi-variant design exploration via GPT Image",
            "trigger": "design variants / show me options / visual brainstorm",
            "invoke": {"method": "skill_load", "skill_name": "design-shotgun"},
        },
        {
            "key": "gstack:plan-tune",
            "provider": "gstack",
            "name": "plan-tune",
            "type": "skill",
            "description": "Self-tuning question sensitivity + developer psychographic",
            "trigger": "tune questions / stop asking / show my profile / developer vibe",
            "invoke": {"method": "skill_load", "skill_name": "plan-tune"},
        },
        {
            "key": "ruflo:hooks_pretrain",
            "provider": "ruflo",
            "name": "hooks_pretrain",
            "type": "mcp_tool",
            "description": "Bootstrap intelligence from repo patterns before planning",
            "trigger": "pretrain / bootstrap intelligence / learn repo patterns",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_pretrain"},
        },
        {
            "key": "ruflo:hooks_build_agents",
            "provider": "ruflo",
            "name": "hooks_build_agents",
            "type": "mcp_tool",
            "description": "Auto-build agent definitions from codebase patterns",
            "trigger": "build agents / create agents from repo / auto-discover agents",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_build-agents"},
        },
        {
            "key": "ruflo:guidance_compile",
            "provider": "ruflo",
            "name": "guidance_compile",
            "type": "mcp_tool",
            "description": "Compile NEXUS routing rules into enforceable policy bundles",
            "trigger": "compile guidance / enforce rules / policy bundles",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "guidance_compile"},
        },
        {
            "key": "ruflo:agentdb_route",
            "provider": "ruflo",
            "name": "agentdb_route",
            "type": "mcp_tool",
            "description": "Route task via AgentDB Semantic Router (alternative to FAISS)",
            "trigger": "agentdb route / semantic route / alternative routing",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "agentdb_route"},
        },
    ],

    "implement": [
        {
            "key": "gstack:design-html",
            "provider": "gstack",
            "name": "design-html",
            "type": "skill",
            "description": "Production-quality Pretext HTML/CSS — 30KB overhead, zero deps",
            "trigger": "build the design / code the mockup / finalize design",
            "invoke": {"method": "skill_load", "skill_name": "design-html"},
        },
        {
            "key": "gstack:investigate",
            "provider": "gstack",
            "name": "investigate",
            "type": "skill",
            "description": "Systematic debugging — 4 phases, Iron Law: no fix without root cause",
            "trigger": "debug this / fix this bug / root cause / why broken",
            "invoke": {"method": "skill_load", "skill_name": "investigate"},
        },
        {
            "key": "gstack:codex",
            "provider": "gstack",
            "name": "codex",
            "type": "skill",
            "description": "Codex CLI wrapper — review, challenge, consult modes",
            "trigger": "codex review / second opinion / outside voice",
            "invoke": {"method": "skill_load", "skill_name": "codex"},
        },
        {
            "key": "gstack:careful",
            "provider": "gstack",
            "name": "careful",
            "type": "skill",
            "description": "Destructive command guardrails — warns before rm -rf, DROP TABLE, force-push",
            "trigger": "be careful / safety mode / warn before destructive",
            "invoke": {"method": "skill_load", "skill_name": "careful"},
        },
        {
            "key": "gstack:freeze",
            "provider": "gstack",
            "name": "freeze",
            "type": "skill",
            "description": "Restrict file edits to a specific directory during implementation",
            "trigger": "freeze edits / restrict to directory / lock edits",
            "invoke": {"method": "skill_load", "skill_name": "freeze"},
        },
        {
            "key": "gstack:guard",
            "provider": "gstack",
            "name": "guard",
            "type": "skill",
            "description": "Full safety mode: careful + freeze combined",
            "trigger": "full safety / guard mode / maximum protection",
            "invoke": {"method": "skill_load", "skill_name": "guard"},
        },
        {
            "key": "ruflo:swarm_init",
            "provider": "ruflo",
            "name": "swarm_init",
            "type": "mcp_tool",
            "description": "Initialize multi-agent swarm for parallel implementation",
            "trigger": "swarm / parallel agents / multi-agent implementation",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "swarm_init"},
        },
        {
            "key": "ruflo:agent_spawn",
            "provider": "ruflo",
            "name": "agent_spawn",
            "type": "mcp_tool",
            "description": "Spawn agent with intelligent defaults for sub-task execution",
            "trigger": "spawn agent / create subagent / delegate task",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "agent_spawn"},
        },
        {
            "key": "ruflo:hive_mind_spawn",
            "provider": "ruflo",
            "name": "hive_mind_spawn",
            "type": "mcp_tool",
            "description": "Spawn consensus-coordinated worker swarm",
            "trigger": "hive mind / consensus workers / coordinated swarm",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hive-mind_spawn"},
        },
        {
            "key": "ruflo:wasm_agent_create",
            "provider": "ruflo",
            "name": "wasm_agent_create",
            "type": "mcp_tool",
            "description": "Create WASM-sandboxed agent for secure sub-task execution",
            "trigger": "wasm agent / sandboxed agent / secure execution",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "wasm_agent_create"},
        },
        {
            "key": "ruflo:workflow_run",
            "provider": "ruflo",
            "name": "workflow_run",
            "type": "mcp_tool",
            "description": "Execute ruflo workflow with multi-step orchestration",
            "trigger": "run workflow / execute workflow / workflow orchestration",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "workflow_run"},
        },
        {
            "key": "ruflo:autopilot_enable",
            "provider": "ruflo",
            "name": "autopilot_enable",
            "type": "mcp_tool",
            "description": "Persistent swarm completion loop until all tasks done",
            "trigger": "autopilot / persistent execution / complete all tasks",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "autopilot_enable"},
        },
        {
            "key": "nexus:plan-executor-track",
            "provider": "nexus",
            "name": "plan-executor-track",
            "type": "service",
            "description": "Track a phase execution with subtask granularity — begin_subtask, complete_subtask, get_progress",
            "trigger": "track phase / subtask progress / granular tracking / phase status",
            "invoke": {"method": "module", "module": "nexus_plan_executor", "class": "PlanExecutor", "method": "begin_subtask"},
        },
        {
            "key": "gstack:tdd-workflow",
            "provider": "gstack",
            "name": "tdd-workflow",
            "type": "skill",
            "description": "Test-driven development workflow with 80%+ coverage requirement",
            "trigger": "tdd / test driven / write tests first / coverage driven",
            "invoke": {"method": "skill_load", "skill_name": "tdd-workflow"},
        },
        {
            "key": "gstack:sparc-methodology",
            "provider": "gstack",
            "name": "sparc-methodology",
            "type": "skill",
            "description": "SPARC methodology: Specification, Pseudocode, Architecture, Refinement, Completion",
            "trigger": "sparc / specification first / pseudocode / architecture first",
            "invoke": {"method": "skill_load", "skill_name": "sparc-methodology"},
        },
        {
            "key": "graphify:execution-context",
            "provider": "graphify",
            "name": "graphify-execution-context",
            "type": "service",
            "description": "Live DAG execution tracking from graphify phase_tracker for Phase 3",
            "trigger": "live tracking / dag status / execution progress / phase tracker",
            "invoke": {"method": "module", "module": "graphify_nexus_bridge", "function": "graphify_execution_context"},
        },
    ],

    "test": [
        {
            "key": "gstack:qa",
            "provider": "gstack",
            "name": "qa",
            "type": "skill",
            "description": "Full QA: test + fix loop with before/after health scores",
            "trigger": "qa / quality check / test the site / find bugs",
            "invoke": {"method": "skill_load", "skill_name": "qa"},
        },
        {
            "key": "gstack:qa-only",
            "provider": "gstack",
            "name": "qa-only",
            "type": "skill",
            "description": "Report-only QA — finds bugs without fixing",
            "trigger": "qa report / bug report / test but dont fix",
            "invoke": {"method": "skill_load", "skill_name": "qa-only"},
        },
        {
            "key": "gstack:browse",
            "provider": "gstack",
            "name": "browse",
            "type": "skill",
            "description": "Fast headless browser daemon — navigate, interact, screenshot, assert",
            "trigger": "browse page / headless browser / take screenshot / test UI",
            "invoke": {"method": "skill_load", "skill_name": "browse"},
        },
        {
            "key": "gstack:setup-browser-cookies",
            "provider": "gstack",
            "name": "setup-browser-cookies",
            "type": "skill",
            "description": "Import real browser cookies into headless session for auth testing",
            "trigger": "import cookies / authenticate browser / login for testing",
            "invoke": {"method": "skill_load", "skill_name": "setup-browser-cookies"},
        },
        {
            "key": "gstack:review",
            "provider": "gstack",
            "name": "review",
            "type": "skill",
            "description": "Pre-landing PR review — SQL safety, LLM trust boundaries, side effects",
            "trigger": "review PR / code review / check my diff / pre-landing",
            "invoke": {"method": "skill_load", "skill_name": "review"},
        },
        {
            "key": "gstack:devex-review",
            "provider": "gstack",
            "name": "devex-review",
            "type": "skill",
            "description": "Live DX audit — times TTHW, screenshots errors, evaluates CLI help",
            "trigger": "dx audit / test onboarding / developer experience test",
            "invoke": {"method": "skill_load", "skill_name": "devex-review"},
        },
        {
            "key": "gstack:benchmark",
            "provider": "gstack",
            "name": "benchmark",
            "type": "skill",
            "description": "Performance regression detection — Core Web Vitals, page load, bundle size",
            "trigger": "performance / benchmark / page speed / web vitals",
            "invoke": {"method": "skill_load", "skill_name": "benchmark"},
        },
        {
            "key": "ruflo:hooks_coverage_route",
            "provider": "ruflo",
            "name": "hooks_coverage_route",
            "type": "mcp_tool",
            "description": "Route test coverage analysis — identify gaps and suggest tests",
            "trigger": "test coverage / coverage gaps / find untested code",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_coverage-route"},
        },
        {
            "key": "ruflo:hooks_coverage_suggest",
            "provider": "ruflo",
            "name": "hooks_coverage_suggest",
            "type": "mcp_tool",
            "description": "Suggest test cases for uncovered code paths",
            "trigger": "suggest tests / generate test cases / coverage suggestions",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_coverage-suggest"},
        },
        {
            "key": "ruflo:agentdb_feedback",
            "provider": "ruflo",
            "name": "agentdb_feedback",
            "type": "mcp_tool",
            "description": "Record task feedback for reinforcement learning",
            "trigger": "record feedback / reinforcement learning / task reward",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "agentdb_feedback"},
        },
        {
            "key": "ruflo:neural_predict",
            "provider": "ruflo",
            "name": "neural_predict",
            "type": "mcp_tool",
            "description": "Neural pattern prediction — forecast test outcomes from historical data",
            "trigger": "predict outcomes / neural forecast / pattern prediction",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "neural_predict"},
        },
        {
            "key": "ruflo:performance_benchmark",
            "provider": "ruflo",
            "name": "performance_benchmark",
            "type": "mcp_tool",
            "description": "Performance profiling and comparison benchmarks",
            "trigger": "performance profile / benchmark comparison / profiling",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "performance_benchmark"},
        },
    ],

    "deploy": [
        {
            "key": "gstack:ship",
            "provider": "gstack",
            "name": "ship",
            "type": "skill",
            "description": "Full ship workflow: merge, bump VERSION, CHANGELOG, commit, push, create PR",
            "trigger": "ship / deploy / create PR / push to main / release",
            "invoke": {"method": "skill_load", "skill_name": "ship"},
        },
        {
            "key": "gstack:land-and-deploy",
            "provider": "gstack",
            "name": "land-and-deploy",
            "type": "skill",
            "description": "Merge PR + wait for CI + deploy + verify production health",
            "trigger": "land pr / merge and deploy / ship to production",
            "invoke": {"method": "skill_load", "skill_name": "land-and-deploy"},
        },
        {
            "key": "gstack:setup-deploy",
            "provider": "gstack",
            "name": "setup-deploy",
            "type": "skill",
            "description": "One-time deploy configuration — detect platform, write config",
            "trigger": "configure deploy / setup deploy / set deploy platform",
            "invoke": {"method": "skill_load", "skill_name": "setup-deploy"},
        },
        {
            "key": "gstack:canary",
            "provider": "gstack",
            "name": "canary",
            "type": "skill",
            "description": "Post-deploy canary monitoring — console errors, perf regressions, failures",
            "trigger": "canary / monitor deploy / post-deploy check / watch production",
            "invoke": {"method": "skill_load", "skill_name": "canary"},
        },
        {
            "key": "gstack:landing-report",
            "provider": "gstack",
            "name": "landing-report",
            "type": "skill",
            "description": "Read-only queue dashboard — VERSION slots, open PRs, ship queue",
            "trigger": "landing report / ship queue / what version / version slots",
            "invoke": {"method": "skill_load", "skill_name": "landing-report"},
        },
        {
            "key": "ruflo:hooks_worker_dispatch",
            "provider": "ruflo",
            "name": "hooks_worker_dispatch",
            "type": "mcp_tool",
            "description": "Dispatch background workers (audit, optimize, testgaps, map, document)",
            "trigger": "background workers / dispatch workers / post-deploy tasks",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_worker-dispatch"},
        },
        {
            "key": "ruflo:claims_claim",
            "provider": "ruflo",
            "name": "claims_claim",
            "type": "mcp_tool",
            "description": "Claims-based authorization for human-agent handoff during deploy",
            "trigger": "claim task / human handoff / authorization / claim workflow",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "claims_claim"},
        },
        {
            "key": "ruflo:deployment_manage",
            "provider": "ruflo",
            "name": "deployment_manage",
            "type": "mcp_tool",
            "description": "Deployment environment management and rollbacks",
            "trigger": "manage deployment / rollback / environment switch",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "deployment_manage"},
        },
    ],

    "verify": [
        {
            "key": "gstack:design-review",
            "provider": "gstack",
            "name": "design-review",
            "type": "skill",
            "description": "Live-site visual audit — finds + fixes visual inconsistency, AI slop",
            "trigger": "visual audit / design QA / fix design issues / polish UI",
            "invoke": {"method": "skill_load", "skill_name": "design-review"},
        },
        {
            "key": "gstack:health",
            "provider": "gstack",
            "name": "health",
            "type": "skill",
            "description": "Code quality dashboard — composite 0-10 score, trend tracking",
            "trigger": "health check / code quality / run all checks / quality score",
            "invoke": {"method": "skill_load", "skill_name": "health"},
        },
        {
            "key": "gstack:benchmark-models",
            "provider": "gstack",
            "name": "benchmark-models",
            "type": "skill",
            "description": "Cross-model benchmark — Claude vs GPT vs Gemini side-by-side",
            "trigger": "compare models / model benchmark / which model is best",
            "invoke": {"method": "skill_load", "skill_name": "benchmark-models"},
        },
        {
            "key": "gstack:pair-agent",
            "provider": "gstack",
            "name": "pair-agent",
            "type": "skill",
            "description": "Pair remote AI agent with browser for collaborative verification",
            "trigger": "pair agent / remote agent / share browser / collaborative review",
            "invoke": {"method": "skill_load", "skill_name": "pair-agent"},
        },
        {
            "key": "ruflo:hooks_model_route",
            "provider": "ruflo",
            "name": "hooks_model_route",
            "type": "mcp_tool",
            "description": "Route verification tasks to optimal LLM models",
            "trigger": "model routing / which model for verification / optimal model",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_model-route"},
        },
        {
            "key": "ruflo:hooks_model_outcome",
            "provider": "ruflo",
            "name": "hooks_model_outcome",
            "type": "mcp_tool",
            "description": "Record model outcome for Thompson belief updates",
            "trigger": "record model outcome / model feedback / belief update",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_model-outcome"},
        },
        {
            "key": "ruflo:neural_optimize",
            "provider": "ruflo",
            "name": "neural_optimize",
            "type": "mcp_tool",
            "description": "Neural optimization — find optimal verification strategy",
            "trigger": "optimize verification / neural optimize / find best approach",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "neural_optimize"},
        },
    ],

    "audit": [
        {
            "key": "gstack:cso",
            "provider": "gstack",
            "name": "cso",
            "type": "skill",
            "description": "Chief Security Officer audit — secrets, supply chain, CI/CD, OWASP, STRIDE",
            "trigger": "security audit / threat model / OWASP / vulnerability scan / pentest",
            "invoke": {"method": "skill_load", "skill_name": "cso"},
        },
        {
            "key": "gstack:retro",
            "provider": "gstack",
            "name": "retro",
            "type": "skill",
            "description": "Weekly engineering retrospective — commit analysis, trend tracking, per-person",
            "trigger": "weeky retro / engineering retro / what did we ship",
            "invoke": {"method": "skill_load", "skill_name": "retro"},
        },
        {
            "key": "gstack:document-release",
            "provider": "gstack",
            "name": "document-release",
            "type": "skill",
            "description": "Post-ship docs update — README, ARCHITECTURE, CHANGELOG, TODO cleanup",
            "trigger": "update docs / document release / post-ship documentation",
            "invoke": {"method": "skill_load", "skill_name": "document-release"},
        },
        {
            "key": "gstack:context-save",
            "provider": "gstack",
            "name": "context-save",
            "type": "skill",
            "description": "Save working context — git state, decisions, remaining work",
            "trigger": "save context / checkpoint / save state / save progress",
            "invoke": {"method": "skill_load", "skill_name": "context-save"},
        },
        {
            "key": "gstack:context-restore",
            "provider": "gstack",
            "name": "context-restore",
            "type": "skill",
            "description": "Restore saved context across sessions and branches",
            "trigger": "restore context / resume / where was I / pick up",
            "invoke": {"method": "skill_load", "skill_name": "context-restore"},
        },
        {
            "key": "gstack:make-pdf",
            "provider": "gstack",
            "name": "make-pdf",
            "type": "skill",
            "description": "Convert audit markdown to publication-quality PDF",
            "trigger": "make pdf / export to pdf / generate document",
            "invoke": {"method": "skill_load", "skill_name": "make-pdf"},
        },
        {
            "key": "ruflo:hooks_intelligence_stats",
            "provider": "ruflo",
            "name": "hooks_intelligence_stats",
            "type": "mcp_tool",
            "description": "Intelligence system statistics — trajectory, pattern, learning stats",
            "trigger": "intelligence stats / learning stats / pattern statistics",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "hooks_intelligence_stats"},
        },
        {
            "key": "ruflo:agentdb_consolidate",
            "provider": "ruflo",
            "name": "agentdb_consolidate",
            "type": "mcp_tool",
            "description": "Run memory consolidation across sessions",
            "trigger": "consolidate memory / merge sessions / memory optimization",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "agentdb_consolidate"},
        },
        {
            "key": "ruflo:guidance_retrieve",
            "provider": "ruflo",
            "name": "guidance_retrieve",
            "type": "mcp_tool",
            "description": "Retrieve compiled guidance rules for compliance audit",
            "trigger": "guidance audit / rule compliance / policy check",
            "invoke": {"method": "mcp_tool", "server": "ruflo", "tool": "guidance_retrieve"},
        },
        {
            "key": "gstack:guard",
            "provider": "gstack",
            "name": "guard",
            "type": "skill",
            "description": "Full safety mode: destructive command warnings + directory-scoped edits",
            "trigger": "guard / full safety / maximum protect / safe mode",
            "invoke": {"method": "skill_load", "skill_name": "guard"},
        },
        {
            "key": "gstack:unfreeze",
            "provider": "gstack",
            "name": "unfreeze",
            "type": "skill",
            "description": "Remove freeze boundary to allow edits everywhere",
            "trigger": "unfreeze / unlock edits / remove restrictions / allow all",
            "invoke": {"method": "skill_load", "skill_name": "unfreeze"},
        },
    ],

    "cli_tools": [
        {
            "key": "gstack_bin:chrome-cdp",
            "provider": "gstack_bin",
            "name": "chrome-cdp",
            "type": "cli_tool",
            "description": "Chrome DevTools Protocol interface for headless browser automation",
            "trigger": "chrome cdp / devtools / browser protocol",
            "invoke": {"method": "cli_command", "command": "gstack chrome-cdp"},
        },
        {
            "key": "gstack_bin:dev-setup",
            "provider": "gstack_bin",
            "name": "dev-setup",
            "type": "cli_tool",
            "description": "Developer environment setup and initialization",
            "trigger": "dev setup / environment setup / init workspace",
            "invoke": {"method": "cli_command", "command": "gstack dev-setup"},
        },
        {
            "key": "gstack_bin:dev-teardown",
            "provider": "gstack_bin",
            "name": "dev-teardown",
            "type": "cli_tool",
            "description": "Developer environment teardown and cleanup",
            "trigger": "dev teardown / cleanup env / destroy workspace",
            "invoke": {"method": "cli_command", "command": "gstack dev-teardown"},
        },
        {
            "key": "gstack_bin:gstack-config",
            "provider": "gstack_bin",
            "name": "gstack-config",
            "type": "cli_tool",
            "description": "gstack configuration manager — read/write config values",
            "trigger": "gstack config / configure gstack / gstack settings",
            "invoke": {"method": "cli_command", "command": "gstack gstack-config"},
        },
        {
            "key": "gstack_bin:gstack-analytics",
            "provider": "gstack_bin",
            "name": "gstack-analytics",
            "type": "cli_tool",
            "description": "Analytics dashboard for gstack usage metrics",
            "trigger": "gstack analytics / usage stats / gstack metrics",
            "invoke": {"method": "cli_command", "command": "gstack gstack-analytics"},
        },
        {
            "key": "gstack_bin:gstack-review-log",
            "provider": "gstack_bin",
            "name": "gstack-review-log",
            "type": "cli_tool",
            "description": "Log and query review outcomes",
            "trigger": "review log / log review / query review history",
            "invoke": {"method": "cli_command", "command": "gstack gstack-review-log"},
        },
        {
            "key": "gstack_bin:gstack-review-read",
            "provider": "gstack_bin",
            "name": "gstack-review-read",
            "type": "cli_tool",
            "description": "Read review history and specialist statistics",
            "trigger": "read review / review history / previous reviews",
            "invoke": {"method": "cli_command", "command": "gstack gstack-review-read"},
        },
        {
            "key": "gstack_bin:gstack-learnings-log",
            "provider": "gstack_bin",
            "name": "gstack-learnings-log",
            "type": "cli_tool",
            "description": "Log project learnings to persistent store",
            "trigger": "log learning / save insight / record finding",
            "invoke": {"method": "cli_command", "command": "gstack gstack-learnings-log"},
        },
        {
            "key": "gstack_bin:gstack-learnings-search",
            "provider": "gstack_bin",
            "name": "gstack-learnings-search",
            "type": "cli_tool",
            "description": "Search across all recorded project learnings",
            "trigger": "search learnings / find insight / query past knowledge",
            "invoke": {"method": "cli_command", "command": "gstack gstack-learnings-search"},
        },
        {
            "key": "gstack_bin:gstack-timeline-log",
            "provider": "gstack_bin",
            "name": "gstack-timeline-log",
            "type": "cli_tool",
            "description": "Log timeline events for session auditing",
            "trigger": "log timeline / record event / session audit",
            "invoke": {"method": "cli_command", "command": "gstack gstack-timeline-log"},
        },
        {
            "key": "gstack_bin:gstack-timeline-read",
            "provider": "gstack_bin",
            "name": "gstack-timeline-read",
            "type": "cli_tool",
            "description": "Read timeline events across sessions",
            "trigger": "read timeline / session history / event log",
            "invoke": {"method": "cli_command", "command": "gstack gstack-timeline-read"},
        },
        {
            "key": "gstack_bin:gstack-telemetry-log",
            "provider": "gstack_bin",
            "name": "gstack-telemetry-log",
            "type": "cli_tool",
            "description": "Log telemetry events for performance tracking",
            "trigger": "log telemetry / telemetry event / perf tracking",
            "invoke": {"method": "cli_command", "command": "gstack gstack-telemetry-log"},
        },
        {
            "key": "gstack_bin:gstack-telemetry-sync",
            "provider": "gstack_bin",
            "name": "gstack-telemetry-sync",
            "type": "cli_tool",
            "description": "Sync telemetry data to remote store",
            "trigger": "sync telemetry / upload metrics / push telemetry",
            "invoke": {"method": "cli_command", "command": "gstack gstack-telemetry-sync"},
        },
        {
            "key": "gstack_bin:gstack-session-update",
            "provider": "gstack_bin",
            "name": "gstack-session-update",
            "type": "cli_tool",
            "description": "Update session state for checkpoint tracking",
            "trigger": "session update / save session / checkpoint session",
            "invoke": {"method": "cli_command", "command": "gstack gstack-session-update"},
        },
        {
            "key": "gstack_bin:gstack-diff-scope",
            "provider": "gstack_bin",
            "name": "gstack-diff-scope",
            "type": "cli_tool",
            "description": "Analyze diff scope for review planning",
            "trigger": "diff scope / analyze diff / change impact",
            "invoke": {"method": "cli_command", "command": "gstack gstack-diff-scope"},
        },
        {
            "key": "gstack_bin:gstack-open-url",
            "provider": "gstack_bin",
            "name": "gstack-open-url",
            "type": "cli_tool",
            "description": "Open a URL in the gstack browser",
            "trigger": "open url / navigate to / browse url",
            "invoke": {"method": "cli_command", "command": "gstack gstack-open-url"},
        },
        {
            "key": "gstack_bin:gstack-model-benchmark",
            "provider": "gstack_bin",
            "name": "gstack-model-benchmark",
            "type": "cli_tool",
            "description": "Benchmark LLM models for quality and performance",
            "trigger": "model benchmark / test model / eval model",
            "invoke": {"method": "cli_command", "command": "gstack gstack-model-benchmark"},
        },
        {
            "key": "gstack_bin:gstack-platform-detect",
            "provider": "gstack_bin",
            "name": "gstack-platform-detect",
            "type": "cli_tool",
            "description": "Detect current platform (macOS/Linux/Windows)",
            "trigger": "detect platform / os detection / platform check",
            "invoke": {"method": "cli_command", "command": "gstack gstack-platform-detect"},
        },
        {
            "key": "gstack_bin:gstack-next-version",
            "provider": "gstack_bin",
            "name": "gstack-next-version",
            "type": "cli_tool",
            "description": "Determine the next VERSION bump",
            "trigger": "next version / version bump / determine version",
            "invoke": {"method": "cli_command", "command": "gstack gstack-next-version"},
        },
        {
            "key": "gstack_bin:gstack-security-dashboard",
            "provider": "gstack_bin",
            "name": "gstack-security-dashboard",
            "type": "cli_tool",
            "description": "Security metrics and vulnerability dashboard",
            "trigger": "security dashboard / vuln metrics / security stats",
            "invoke": {"method": "cli_command", "command": "gstack gstack-security-dashboard"},
        },
        {
            "key": "gstack_bin:gstack-community-dashboard",
            "provider": "gstack_bin",
            "name": "gstack-community-dashboard",
            "type": "cli_tool",
            "description": "Community metrics and activity dashboard",
            "trigger": "community dashboard / metrics / activity stats",
            "invoke": {"method": "cli_command", "command": "gstack gstack-community-dashboard"},
        },
        {
            "key": "gstack_bin:gstack-developer-profile",
            "provider": "gstack_bin",
            "name": "gstack-developer-profile",
            "type": "cli_tool",
            "description": "Developer psychographic profile management",
            "trigger": "developer profile / psychographic / dev persona",
            "invoke": {"method": "cli_command", "command": "gstack gstack-developer-profile"},
        },
        {
            "key": "gstack_bin:gstack-question-preference",
            "provider": "gstack_bin",
            "name": "gstack-question-preference",
            "type": "cli_tool",
            "description": "Manage per-question ask preferences for plan-tune",
            "trigger": "question preference / ask pref / tune question",
            "invoke": {"method": "cli_command", "command": "gstack gstack-question-preference"},
        },
    ],

    "gbrain": [
        {
            "key": "gbrain:init",
            "provider": "gstack_gbrain",
            "name": "gbrain-init",
            "type": "cli_tool",
            "description": "Initialize gbrain local PGLite or Supabase instance",
            "trigger": "init gbrain / setup gbrain / install gbrain",
            "invoke": {"method": "cli_command", "command": "gstack gstack-brain-init"},
        },
        {
            "key": "gbrain:sync",
            "provider": "gstack_gbrain",
            "name": "gbrain-sync",
            "type": "cli_tool",
            "description": "Sync codebase knowledge into gbrain memory",
            "trigger": "sync gbrain / refresh brain / reindex to gbrain",
            "invoke": {"method": "cli_command", "command": "gstack gstack-brain-sync"},
        },
        {
            "key": "gbrain:restore",
            "provider": "gstack_gbrain",
            "name": "gbrain-restore",
            "type": "cli_tool",
            "description": "Restore gbrain state from backup",
            "trigger": "restore brain / recover gbrain / load from backup",
            "invoke": {"method": "cli_command", "command": "gstack gstack-brain-restore"},
        },
        {
            "key": "gbrain:detect",
            "provider": "gstack_gbrain",
            "name": "gbrain-detect",
            "type": "cli_tool",
            "description": "Detect if gbrain is installed and configured",
            "trigger": "detect gbrain / check gbrain / is gbrain installed",
            "invoke": {"method": "cli_command", "command": "gstack gstack-gbrain-detect"},
        },
        {
            "key": "gbrain:install",
            "provider": "gstack_gbrain",
            "name": "gbrain-install",
            "type": "cli_tool",
            "description": "Install gbrain CLI tool",
            "trigger": "install gbrain / get gbrain / download gbrain",
            "invoke": {"method": "cli_command", "command": "gstack gstack-gbrain-install"},
        },
        {
            "key": "gbrain:supabase-provision",
            "provider": "gstack_gbrain",
            "name": "gbrain-supabase-provision",
            "type": "cli_tool",
            "description": "Provision Supabase backend for gbrain",
            "trigger": "supabase brain / provision gbrain / setup supabase gbrain",
            "invoke": {"method": "cli_command", "command": "gstack gstack-gbrain-supabase-provision"},
        },
        {
            "key": "gbrain:supabase-verify",
            "provider": "gstack_gbrain",
            "name": "gbrain-supabase-verify",
            "type": "cli_tool",
            "description": "Verify Supabase configuration for gbrain",
            "trigger": "verify supabase / check supabase / supabase gbrain status",
            "invoke": {"method": "cli_command", "command": "gstack gstack-gbrain-supabase-verify"},
        },
        {
            "key": "gbrain:repo-policy",
            "provider": "gstack_gbrain",
            "name": "gbrain-repo-policy",
            "type": "cli_tool",
            "description": "Set repository trust policy for gbrain",
            "trigger": "repo policy / trust policy / brain access rules",
            "invoke": {"method": "cli_command", "command": "gstack gstack-gbrain-repo-policy"},
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# PROVIDER REGISTRY — Flat catalog by provider
# ═══════════════════════════════════════════════════════════════════════════

PROVIDER_REGISTRY = {
    "gstack": {
        "type": "skill_collection",
        "version": "1.26.3",
        "root": str(Path.home() / ".claude/skills/gstack"),
        "browse_binary": str(Path.home() / ".claude/skills/gstack/browse/dist/browse"),
        "design_binary": str(Path.home() / ".claude/skills/gstack/design/dist/design"),
        "pdf_binary": str(Path.home() / ".claude/skills/gstack/make-pdf/dist/pdf"),
        "bin_dir": str(Path.home() / ".claude/skills/gstack/bin"),
        "total_skills": 47,
        "total_cli_tools": 59,
        "total_review_specialists": 7,
        "total_binaries": 4,
        "description": "47 primary skills + 59 CLI tools + 7 review specialists + 4 binaries across plan-review, browser QA, design, deploy, security, memory, safety",
    },
    "gstack_bin": {
        "type": "cli_tool_collection",
        "version": "1.26.3",
        "bin_dir": str(Path.home() / ".claude/skills/gstack/bin"),
        "total_tools": 59,
        "description": "59 CLI executables: chrome-cdp, dev-setup, gstack-config, gstack-analytics, gbrain, leanguages, timeline, telemetry, review-log, etc.",
    },
    "gstack_gbrain": {
        "type": "brain_service",
        "version": "1.0",
        "description": "gbrain CLI + Supabase-backed learning memory. Tools: gstack-brain-init, gstack-brain-sync, gstack-brain-restore, gstack-gbrain-install, gstack-gbrain-detect, gstack-gbrain-supabase-provision",
    },
    "ruflo": {
        "type": "mcp_server",
        "version": "3.6.27",
        "cli": "ruflo",
        "registry_command": "claude mcp add ruflo -- npx -y ruflo@latest mcp start",
        "total_tools": 261,
        "description": "261 MCP tools: swarm, memory, neural, hooks, agents, AIDefence, embeddings, workflow, wasm",
    },
    "nexus": {
        "type": "internal_service",
        "version": "4.0",
        "total_tools": 3,
        "description": "Plan Executor: persistent phase/task/subtask engine with OOC/OOM-safe checkpoint-resume",
    },
    "graphify": {
        "type": "knowledge_graph",
        "version": "0.8.1",
        "py_package": "graphifyy",
        "graph_path": str(GRAPHIFY_OUT / "graph.json"),
        "total_tools": 4,
        "graph_stats": "6676 nodes, 10168 edges, 813 communities",
        "description": "Knowledge graph state analysis: component mapping, neighbor traversal, impact analysis, live DAG tracking",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# BUILD FLAT INDEXES (for fast lookups)
# ═══════════════════════════════════════════════════════════════════════════

def _build_indexes():
    """Build flat key→entry and phase→key indexes from PHASE_TOOLS."""
    key_index = {}
    phase_index = {}
    for phase, tools in PHASE_TOOLS.items():
        phase_index[phase] = []
        if not isinstance(tools, list):
            continue
        for tool in tools:
            key_index[tool["key"]] = tool
            phase_index[phase].append(tool["key"])
    return key_index, phase_index


_KEY_INDEX, _PHASE_INDEX = _build_indexes()


# ═══════════════════════════════════════════════════════════════════════════
# RESOLVE — Map task + phase to orchestrator tools
# ═══════════════════════════════════════════════════════════════════════════

_ORCHESTRATOR_TRIGGERS = None


def _build_trigger_index():
    """Build keyword→key index from trigger field in all tools."""
    global _ORCHESTRATOR_TRIGGERS
    if _ORCHESTRATOR_TRIGGERS is not None:
        return _ORCHESTRATOR_TRIGGERS
    _ORCHESTRATOR_TRIGGERS = {}
    for phase, tools in PHASE_TOOLS.items():
        for tool in tools:
            triggers = [t.strip().lower() for t in tool.get("trigger", "").split("/")]
            for trigger in triggers:
                if trigger:
                    words = trigger.split()
                    for word in words:
                        if len(word) >= 3:
                            if word not in _ORCHESTRATOR_TRIGGERS:
                                _ORCHESTRATOR_TRIGGERS[word] = []
                            _ORCHESTRATOR_TRIGGERS[word].append(tool["key"])
    return _ORCHESTRATOR_TRIGGERS


def resolve_orchestrators(
    task: str,
    phase_type: Optional[str] = None,
    provider: Optional[str] = None,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """Find orchestrator tools that assist the given task.

    Args:
        task: The task description (used for keyword matching against triggers).
        phase_type: Filter by NEXUS phase type (research/design/implement/test/
                    deploy/verify/audit). None = all phases.
        provider: Filter by provider ('gstack' or 'ruflo'). None = both.
        max_results: Maximum tools to return.

    Returns:
        List of matching orchestrator tool dicts, scored by relevance.
    """
    task_lower = task.lower()
    triggers = _build_trigger_index()

    # Collect all candidate tools
    candidates = []
    for phase, tool_keys in _PHASE_INDEX.items():
        if phase_type and phase != phase_type:
            continue
        for key in tool_keys:
            tool = _KEY_INDEX[key]
            if provider and tool["provider"] != provider:
                continue
            candidates.append(tool)

    # Score by keyword overlap with triggers
    scored = []
    task_words = set(w for w in task_lower.split() if len(w) >= 3)
    for tool in candidates:
        trigger_text = tool.get("trigger", "").lower()
        trigger_words = set(w for w in trigger_text.split() if len(w) >= 3)
        overlap = len(task_words & trigger_words)
        if overlap > 0:
            scored.append((tool, overlap))

    # Sort by overlap descending
    scored.sort(key=lambda x: -x[1])

    # Add description + phase info
    results = []
    seen_keys = set()
    for tool, _ in scored:
        if tool["key"] in seen_keys:
            continue
        seen_keys.add(tool["key"])
        # Find which phases this tool belongs to
        phases = [p for p, keys in _PHASE_INDEX.items() if tool["key"] in keys]
        result = dict(tool)
        result["phases"] = phases
        results.append(result)
        if len(results) >= max_results:
            break

    return results


# ═══════════════════════════════════════════════════════════════════════════
# ENHANCE PLAN — Inject orchestrator tools into a NEXUS plan's phases
# ═══════════════════════════════════════════════════════════════════════════

def enhance_plan(plan: Dict[str, Any], task: str) -> Dict[str, Any]:
    """Enhance a NEXUS plan DAG by adding orchestrator tool mappings to each phase.

    Takes a plan dict (from orchestrator.py's Phase 2 output) and adds an
    'orchestrator_tools' key to each phase with relevant gstack/ruflo tools.

    Args:
        plan: The plan dict with a 'phases' list. Each phase should have at least
              an 'id' and 'type' field.
        task: The original task description for trigger matching.

    Returns:
        Enhanced plan dict with orchestrator_tools injected into each phase.
    """
    enhanced = dict(plan)
    phases = list(enhanced.get("phases", []))

    for i, phase in enumerate(phases):
        phase_type = phase.get("type", "implement")
        tools = resolve_orchestrators(task, phase_type=phase_type, max_results=4)
        phase["orchestrator_tools"] = tools

        # Add to existing resources if present
        resources = phase.get("resources", {})
        if not isinstance(resources, dict):
            resources = {}
        resources["orchestrators"] = [t["key"] for t in tools]
        phase["resources"] = resources

    enhanced["phases"] = phases
    enhanced["orchestrator_integration"] = {
        "enabled": True,
        "providers": list(PROVIDER_REGISTRY.keys()),
        "tools_mapped": sum(len(ph["orchestrator_tools"]) for ph in phases),
    }
    return enhanced


# ═══════════════════════════════════════════════════════════════════════════
# INVOKE — Unified orchestrator tool invocation
# ═══════════════════════════════════════════════════════════════════════════

def invoke_orchestrator(
    key: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate invocation instructions for an orchestrator tool.

    Does NOT execute tools directly (that would require MCP server access or
    CLI execution). Instead, returns structured invocation instructions that
    the caller (typically an agent or skill) can use to invoke the tool.

    Args:
        key: Tool key like 'gstack:qa' or 'ruflo:swarm_init'.
        context: Optional dict with 'task', 'project', 'args' for context.

    Returns:
        Dict with invocation method, command/skill name, and parameters.
    """
    tool = _KEY_INDEX.get(key)
    if not tool:
        return {"status": "error", "message": f"Tool '{key}' not found in registry"}

    context = context or {}
    invoke = tool.get("invoke", {})
    method = invoke.get("method", "unknown")

    instruction = {
        "status": "ready",
        "tool_key": key,
        "tool_name": tool["name"],
        "provider": tool["provider"],
        "description": tool["description"],
        "invocation": {},
    }

    if method == "skill_load":
        instruction["invocation"] = {
            "type": "skill",
            "action": f'skill(name="{invoke["skill_name"]}")',
            "usage": f'Load the {tool["name"]} skill via the "skill" tool',
            "context": context.get("task", ""),
        }
    elif method == "mcp_tool":
        instruction["invocation"] = {
            "type": "mcp",
            "server": invoke["server"],
            "tool": invoke["tool"],
            "usage": f'Invoke the MCP tool "{invoke["tool"]}" on the "{invoke["server"]}" server',
            "context": context.get("task", ""),
        }
    elif method == "cli_command":
        instruction["invocation"] = {
            "type": "cli",
            "command": invoke["command"],
            "usage": f'Run: {invoke["command"]}',
            "context": context.get("task", ""),
        }

    # Add to instruction for current task
    instruction["task"] = context.get("task", "")
    instruction["project"] = context.get("project", "default")

    return instruction


# ═══════════════════════════════════════════════════════════════════════════
# REPORT OUTCOME — Bridge back to NEXUS learning
# ═══════════════════════════════════════════════════════════════════════════

def report_orchestrator_outcome(
    tool_key: str,
    outcome: str,
    task_summary: str = "",
    project: str = "default",
) -> Dict[str, Any]:
    """Report an orchestrator tool outcome back to NEXUS learning system.

    Bridges to the existing log_outcome() function in intent_classifier.py
    so that orchestrator tool usage feeds into the same learning pipeline.

    Args:
        tool_key: Tool key like 'gstack:qa'.
        outcome: 'correct' or 'wrong'.
        task_summary: What the tool was used for.
        project: Project name.

    Returns:
        Result dict with status and bridging details.
    """
    tool = _KEY_INDEX.get(tool_key)
    if not tool:
        return {"status": "error", "message": f"Tool '{tool_key}' not found"}

    try:
        from server.intent_classifier import log_outcome
    except ImportError:
        try:
            from intent_classifier import log_outcome
        except ImportError:
            logger.warning("intent_classifier.log_outcome not available")
            return {"status": "logged_direct"}

    try:
        log_outcome(
            task=task_summary or tool["description"],
            predicted_skills=[tool["name"]],
            actual_outcome=outcome,
        )
        return {
            "status": "logged",
            "tool_key": tool_key,
            "outcome": outcome,
            "via": "intent_classifier.log_outcome",
        }
    except Exception as e:
        logger.warning(f"report_orchestrator_outcome failed: {e}")
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# ROUTING ASSIST — Get orchestrator routing suggestions
# ═══════════════════════════════════════════════════════════════════════════

def get_orchestrator_routing(task: str) -> Dict[str, Any]:
    """Get routing suggestions from orchestrator systems for a task.

    Returns the best gstack skill and best ruflo MCP tool for the task,
    regardless of phase. Useful as a cross-check against NEXUS routing.

    Args:
        task: The task description.

    Returns:
        Dict with top gstack and ruflo suggestions.
    """
    all_tools = resolve_orchestrators(task, max_results=10)
    gstack_tools = [t for t in all_tools if t["provider"] == "gstack"]
    ruflo_tools = [t for t in all_tools if t["provider"] == "ruflo"]

    return {
        "task": task,
        "gstack_suggestion": gstack_tools[0] if gstack_tools else None,
        "ruflo_suggestion": ruflo_tools[0] if ruflo_tools else None,
        "all_matches": all_tools[:8],
        "total_tools_cataloged": len(_KEY_INDEX),
    }


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY STATS
# ═══════════════════════════════════════════════════════════════════════════

def get_registry_stats() -> Dict[str, Any]:
    """Get statistics about the orchestrator tools registry."""
    return {
        "total_tools": len(_KEY_INDEX),
        "phases": {
            phase: len(keys)
            for phase, keys in _PHASE_INDEX.items()
        },
        "providers": {
            name: info.get("total_tools") or info.get("total_skills", 0)
            for name, info in PROVIDER_REGISTRY.items()
        },
        "phase_tools": {
            phase: sorted(
                [{"key": k, "name": _KEY_INDEX[k]["name"], "provider": _KEY_INDEX[k]["provider"]}
                 for k in keys],
                key=lambda x: x["key"],
            )
            for phase, keys in _PHASE_INDEX.items()
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# RUPLO MCP SERVER REGISTRATION HELPER
# ═══════════════════════════════════════════════════════════════════════════

def register_ruflo_mcp() -> Dict[str, Any]:
    """Generate instructions for registering ruflo MCP server with Claude.

    Returns registration command + instructions. Does NOT execute it.
    """
    return {
        "status": "instructions",
        "command": "claude mcp add ruflo -- npx -y ruflo@latest mcp start",
        "verification": "claude mcp list | grep ruflo",
        "note": "Once registered, ruflo's 261 MCP tools become available via ToolSearch",
        "current_status": "not_registered",
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: orchestrator_tools.py <command> [args]")
        print()
        print("Commands:")
        print("  stats                    Show registry statistics")
        print("  resolve <task> [phase]   Find orchestrator tools for task")
        print("  route <task>             Get routing suggestions")
        print("  phases                   List all phases with tool counts")
        print("  providers                List all providers")
        print("  register-ruflo           Show ruflo MCP registration instructions")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "stats":
        stats = get_registry_stats()
        print(f"Total orchestrator tools: {stats['total_tools']}")
        print(f"\nPer phase:")
        for phase, count in stats["phases"].items():
            print(f"  {phase}: {count} tools")
        print(f"\nPer provider:")
        for provider, count in stats["providers"].items():
            print(f"  {provider}: {count} tools")
        print(f"\nAll tools by phase:")
        for phase, tools in stats["phase_tools"].items():
            print(f"\n  {phase}:")
            for t in tools:
                print(f"    [{t['provider']}] {t['key']} — {t['name']}")

    elif cmd == "resolve":
        task = sys.argv[2] if len(sys.argv) > 2 else ""
        phase = sys.argv[3] if len(sys.argv) > 3 else None
        results = resolve_orchestrators(task, phase_type=phase)
        print(f"Task: {task}")
        print(f"Phase filter: {phase or 'all'}")
        print(f"Matches: {len(results)}")
        for r in results:
            print(f"\n  [{r['provider']}] {r['key']}")
            print(f"    Description: {r['description']}")
            print(f"    Trigger: {r['trigger']}")
            print(f"    Phases: {', '.join(r['phases'])}")
            print(f"    Invoke: {r['invoke']['method']}")

    elif cmd == "route":
        task = sys.argv[2] if len(sys.argv) > 2 else ""
        routing = get_orchestrator_routing(task)
        print(f"Task: {routing['task']}")
        print(f"Total tools cataloged: {routing['total_tools_cataloged']}")
        print(f"\nTop gstack suggestion:")
        if routing["gstack_suggestion"]:
            gs = routing["gstack_suggestion"]
            print(f"  {gs['key']}: {gs['description']}")
        else:
            print("  (none)")
        print(f"\nTop ruflo suggestion:")
        if routing["ruflo_suggestion"]:
            rs = routing["ruflo_suggestion"]
            print(f"  {rs['key']}: {rs['description']}")
        else:
            print("  (none)")
        print(f"\nAll matches:")
        for m in routing["all_matches"]:
            print(f"  [{m['provider']}] {m['key']} ({', '.join(m['phases'])})")

    elif cmd == "phases":
        stats = get_registry_stats()
        print(f"Orchestrator tools by NEXUS phase:")
        print(f"  Total: {stats['total_tools']}")
        for phase, count in stats["phases"].items():
            print(f"  {phase}: {count} tools")

    elif cmd == "providers":
        print("Orchestrator providers:")
        for name, info in PROVIDER_REGISTRY.items():
            print(f"\n  {name}:")
            print(f"    Type: {info['type']}")
            print(f"    Version: {info['version']}")
            print(f"    Total: {info['total_tools']} tools/registry")
            print(f"    Description: {info['description']}")

    elif cmd == "register-ruflo":
        instr = register_ruflo_mcp()
        print("Ruflo MCP Server Registration:")
        print(f"\n  Command: {instr['command']}")
        print(f"\n  Verification: {instr['verification']}")
        print(f"\n  Note: {instr['note']}")
        print(f"  Current status: {instr['current_status']}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
