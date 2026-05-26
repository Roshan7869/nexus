#!/usr/bin/env python3
"""Register discovered system resources into NEXUS."""
import sqlite3, sqlite_vec, ollama
from pathlib import Path

NEXUS_DB = '/home/roshan/nexus/db/nexus.db'
EMBED_MODEL = 'nomic-embed-text:latest'

conn = sqlite3.connect(NEXUS_DB)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
cur = conn.cursor()

def get_emb(text):
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:12000])
        return sqlite_vec.serialize_float32(resp['embedding'])
    except Exception as e:
        print(f"  EMBED FAIL: {e}")
        return None

resources = [
    # === optillm (reasoning optimization proxy) ===
    ("optillm", "skill", "optillm", "README.md",
     "OptiLLM: OpenAI-compatible optimizing inference proxy. 20+ techniques (MCTS, MOA, BON, CoT, PlanSearch) for 2-10x accuracy improvements on reasoning tasks without training.",
     "OptiLLM is an OpenAI API-compatible optimizing inference proxy implementing 20+ state-of-the-art inference-time optimization techniques including Mixture of Agents (MOA), Monte Carlo Tree Search (MCTS), Best-of-N (BON), Chain-of-Thought Reflection, PlanSearch, and R* algorithms. Runs as a proxy server on port 8000. Supports multi-provider (OpenAI, Anthropic, Google, Cerebras, LiteLLM). Configured in NEXUS nexus.yaml as approaches.enabled=true. Key files: optillm/plugins/ for MCP, memory, privacy, JSON plugins."),

    ("optillm-moa", "skill", "optillm", "optillm/moa.py",
     "Mixture of Agents (MOA) reasoning technique — uses multiple LLM agents in parallel, then aggregates responses for improved quality. Matches GPT-4 performance from GPT-4o-mini.",
     "MOA is a Mixture of Agents approach that runs multiple LLM agents in parallel on the same prompt, then uses a meta-agent to synthesize the best response. Usage: prefix 'moa-' before model name. Also supports pipelining: 'cot_reflection&moa' for sequential processing."),

    ("optillm-mcts", "skill", "optillm", "optillm/mcts.py",
     "Monte Carlo Tree Search for LLM reasoning — explores multiple reasoning paths via MCTS to find optimal response chains.",
     "MCTS-based reasoning that explores multiple reasoning paths, using Monte Carlo Tree Search to balance exploration vs exploitation. Effective for math and coding tasks."),

    ("optillm-bon", "skill", "optillm", "optillm/bon.py",
     "Best-of-N sampling — generates N candidate responses and selects the best one via scoring.",
     "Best-of-N sampling. Generates N candidate responses, scores each, returns highest-scoring. Simple 2-3x accuracy boost on reasoning tasks."),

    ("optillm-cot-reflection", "skill", "optillm", "optillm/cot_reflection.py",
     "Chain-of-Thought with self-reflection — generates reasoning traces then self-critiques and refines them.",
     "CoT with self-reflection. Generates initial reasoning trace, then critiques and refines. Two-pass approach catching reasoning errors."),

    ("optillm-plansearch", "skill", "optillm", "optillm/plansearch.py",
     "PlanSearch — generates multiple execution plans, evaluates each, and executes the best one. +20% pass@5 on LiveCodeBench.",
     "PlanSearch generates N high-level execution plans, scores each for feasibility, executes the best with detailed step-by-step reasoning."),

    # === yournextstore ===
    ("yournextstore", "skill", "yournextstore", "README.md",
     "Your Next Store: Open-source AI-native Next.js e-commerce template. Stripe-native, Next.js 16 App Router, Commerce Kit SDK.",
     "Open-source AI-native Next.js e-commerce template with Stripe integration. Stack: Bun, TypeScript, Shadcn UI, Commerce Kit SDK, Stripe. Features Commerce Kit SDK with typed methods (productBrowse, cartUpsert, productGet), Next.js 16 App Router, AGENTS.md."),

    ("yournextstore-agent", "agent", "yournextstore", "AGENTS.md",
     "YNS agent persona: e-commerce implementation specialist. Patterns: safe-try, formatMoney, functional array methods, Biome rules.",
     "Agent for implementing features on Your Next Store. Follows patterns: safe-try for error handling, formatMoney from lib/money.ts, functional array methods, Biome strict rules. Commands: bun dev/build/start/lint/format/test."),

    # === Scrapling ===
    ("scrapling", "skill", "Scrapling", "README.md",
     "Scrapling: Adaptive web scraping framework. Bypasses Cloudflare/anti-bot, adaptive parser, spider framework with proxy rotation.",
     "Adaptive Web Scraping framework. StealthyFetcher bypasses Cloudflare Turnstile, adaptive parser auto-relocates elements when sites change, spider framework with concurrent crawls, pause/resume, proxy rotation. Has MCP server and CLI."),

    ("scrapling-mcp", "mcp", "Scrapling", "agent-skill/mcp/",
     "Scrapling MCP server — web scraping via MCP protocol. Fetch, parse, spider operations through MCP tool interface.",
     "MCP server exposing web scraping operations as MCP tools: fetch, parse, and spider capabilities for MCP-compatible agents."),

    ("scrapling-cli", "cli_tool", "Scrapling", "docs/cli/overview.html",
     "Scrapling CLI — command-line web scraping tool with fetch, parse, and crawl commands.",
     "CLI tool for web scraping. Commands: fetch (single page with adaptive parsing), parse (extract elements), crawl (spider with proxy rotation). Python package: pip install scrapling."),

    # === gemma4-browser-extension ===
    ("gemma4-browser-extension", "skill", "gemma4-browser-extension", "README.md",
     "Gemma 4 browser extension: on-device AI assistant running entirely in browser via WebGPU + Transformers.js.",
     "Chrome extension running Gemma 4 on-device via WebGPU and Transformers.js. Natural language command understanding, browser interaction tools. Private, offline-capable."),

    # === domleca-llm-wiki ===
    ("llm-wiki-obsidian", "skill", "domleca-llm-wiki", "README.md",
     "LLM Wiki for Obsidian: Karpathy-inspired structured wiki from personal notes using LLMs.",
     "Obsidian-based LLM Wiki inspired by Karpathy's LLM knowledge base concept. Compiles personal notes into structured, queryable wiki using LLMs. Private, runs inside Obsidian."),

    # === medusa ===
    ("medusa", "skill", "medusa", "README.md",
     "Medusa: Open-source digital commerce platform. Headless commerce with REST APIs, admin, multi-tenant.",
     "Headless commerce platform. Node.js/TypeScript with PostgreSQL. Features: products, orders, inventory, customers, payments, shipping. Monorepo with Turbo repo, Jest testing."),

    # === sherlock ===
    ("sherlock", "skill", "sherlock", "README.md",
     "Sherlock: OSINT social media username search across hundreds of platforms.",
     "Sherlock OSINT tool. Searches for a given username across hundreds of social media platforms. Useful for reconnaissance and threat intelligence."),

    # === scrapecraft ===
    ("scrapecraft", "skill", "scrapecraft", "README.md",
     "ScrapeCraft: AI-powered web scraping editor with Cursor-like interface. Uses ScrapeGraphAI API.",
     "AI-powered web scraping editor with Cursor-like interface for building scraping pipelines. Uses ScrapeGraphAI API and OpenRouter (Kimi-k2)."),

    # === System CLI tools ===
    ("ollama", "cli_tool", "custom", "/usr/local/bin/ollama",
     "Ollama CLI: local LLM runner. Primary model provider for NEXUS embeddings and inference.",
     "Ollama CLI for running LLMs locally. Supports ollama pull/run, API on localhost:11434. NEXUS uses for embeddings via nomic-embed-text. Models in ~/.ollama/models/."),

    ("docker", "cli_tool", "custom", "/usr/bin/docker",
     "Docker CLI: container runtime. Installed at /usr/bin/docker.",
     "Docker engine for containers. Used by OptiLLM, InsForge, and other services. Commands: docker ps/compose/build/run."),

    ("git", "cli_tool", "custom", "/usr/bin/git",
     "Git: version control system. Installed at /usr/bin/git.",
     "Git CLI for version control. Used across all repos including nexus, ruflo, optillm, yournextstore, etc."),

    ("bun", "cli_tool", "custom", "/home/roshan/.bun/bin/bun",
     "Bun: JavaScript runtime and package manager. Installed at ~/.bun/bin/bun.",
     "Bun runtime + package manager. Used by yournextstore for dev/build/test/lint."),

    ("cargo", "cli_tool", "custom", "/home/roshan/.cargo/bin/cargo",
     "Cargo: Rust package manager. Rust toolchain available at ~/.cargo/bin/.",
     "Cargo Rust package manager. Rust compiler (rustc) at ~/.cargo/bin/."),

    ("supabase-cli", "cli_tool", "custom", "system",
     "Supabase CLI v2.20.12: database migrations, local dev, project management.",
     "Supabase CLI for migrations (db push/pull/link/diff), functions, projects, local dev start/stop."),

    ("aider", "cli_tool", "custom", "/home/roshan/.local/bin/aider",
     "Aider: AI pair programming in terminal. Works with local/cloud LLMs.",
     "Aider CLI for AI-assisted code editing. Works with local Ollama models and cloud providers. Uses git for automatic commits."),

    ("tmux", "cli_tool", "custom", "/usr/bin/tmux",
     "Tmux: terminal multiplexer. Session management with multiple panes/windows.",
     "Tmux terminal multiplexer for managing multiple terminal sessions. Supports panes, windows, detached sessions."),

    # === NEXUS system tools ===
    ("nexus-startup", "command", "nexus", "start_nexus.py",
     "NEXUS v4 startup script. Flags: --cluster, --thompson, --reflection, --mcp-sync, --all, --port, --check-only, --mcp-only.",
     "python start_nexus.py [--cluster] [--thompson] [--reflection] [--mcp-sync] [--all] [--port 8000] [--check-only] [--mcp-only]. Sets env flags, checks dependencies."),

    ("nexus-opencode-connector", "command", "nexus", "opencode_connector.py",
     "NEXUS v3 OpenCode Integration — auto-connector for session startup. Import nexus_connector in OpenCode.",
     "Python module integrating NEXUS with OpenCode. Auto-injects sys.path for NEXUS server/venv. Provides: find_skills(), route_v3(), health()."),

    ("nexus-unified-server", "skill", "nexus", "server/unified.py",
     "NEXUS Unified Server — LLM Gateway + MCP Orchestration on port 8000. /v1/chat/completions, /nexus/route, /nexus/search, /nexus/health.",
     "Unified Server combining LLM Gateway proxy (Ollama Cloud / NVIDIA NIM) with MCP Orchestration (1097 resources, 8 clusters). Feature flags: THOMPSON_ROUTING, CLUSTER_ROUTING, REFLECTION, MCP_SYNC."),
]

inserted = 0
for name, rtype, repo, fpath, desc, body in resources:
    existing = cur.execute("SELECT id FROM resources WHERE name=?", (name,)).fetchone()
    if existing:
        print(f"  SKIP: {name}")
        continue

    text_for_emb = f"{name} {rtype} {desc[:1000]}"
    emb = get_emb(text_for_emb)
    if emb is None:
        print(f"  NO EMB for {name}")
    token_count = len(body) // 3

    cur.execute("""
        INSERT INTO resources(name, resource_type, repo_name, file_path,
                              description, body_text, embedding, token_count,
                              status, tier, times_triggered, times_correct, times_wrong)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'active', 2, 0, 0, 0)
    """, (name, rtype, repo, fpath, desc[:200], body, emb, token_count))
    print(f"  + {name} ({rtype})")
    inserted += 1

conn.commit()

print(f"\nInserted {inserted} new resources")

cur.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type ORDER BY COUNT(*) DESC")
print("=== BY TYPE ===")
for row in cur.fetchall():
    print(f"  {row[0]:20s} {row[1]}")

total = cur.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print(f"\nTOTAL RESOURCES: {total}")
conn.close()
