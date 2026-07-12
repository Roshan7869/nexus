#!/usr/bin/env python3
"""Absorb agent-reach platforms and gcode-harness modules into NEXUS.

Phase 1 of Context Harnessing Engine: Create indexed resources with
384-dim L2-normalized embeddings for semantic routing.

Agent-reach: 13 platform resources (web, twitter, github, etc.)
  linked to existing 'agent-reach' skill (id=2856)
Gcode-harness: 8 module resources (memory_graph, mcp_pool, etc.)
  with cross-links between interdependent modules
"""
import os, sys, re, sqlite3, time, json
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path.home() / "nexus/server"))
sys.path.insert(0, str(Path.home() / "nexus"))

DB_PATH = str(Path.home() / "nexus/db/nexus.db")
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
os.environ["TOKENIZERS_PARALLELISM"] = "false"

AGENT_REACH_SKILL_ID = 2856  # existing agent-reach skill resource

RESOURCES = [
    # ── agent-reach platforms ──────────────────────────────────────────
    {
        "name": "agent-reach-web",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Web page reader via Jina Reader API — zero-config URL content extraction for any public webpage",
        "body": "Agent Reach web channel. Backend: Jina Reader (https://r.jina.ai). Tier 0 (zero-config). Reads any public URL and returns clean markdown content. No login or API key needed. Usage: curl -s 'https://r.jina.ai/URL'",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-youtube",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "YouTube subtitle and metadata extraction via yt-dlp — zero-config transcript and video info retrieval",
        "body": "Agent Reach YouTube channel. Backend: yt-dlp. Tier 0 (zero-config). Extracts subtitles, metadata, and thumbnails from YouTube videos. No login needed for public videos. Usage: yt-dlp --write-sub --skip-download -o '/tmp/%(id)s' URL",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-rss",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "RSS/Atom feed reader via feedparser — zero-config feed parsing and content aggregation",
        "body": "Agent Reach RSS channel. Backend: feedparser (Python). Tier 0 (zero-config). Parses RSS 2.0 and Atom feeds. Returns entries with title, link, summary, published date. No auth needed for public feeds.",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-exa",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Exa AI web and code search via mcporter MCP — zero-config semantic search with AI-powered results",
        "body": "Agent Reach Exa Search channel. Backend: mcporter + Exa MCP. Tier 0 (zero-config). Semantic web and code search. Supports natural language queries, returns ranked results with snippets. Usage: mcporter call 'exa.web_search_exa(query: ..., numResults: 5)'",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-github",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "devops_infra",
        "description": "GitHub CLI integration — zero-config repository search, read, and management via gh CLI",
        "body": "Agent Reach GitHub channel. Backend: gh CLI. Tier 0 (zero-config). Search repos, read files, manage PRs and issues. Public repos work without auth; gh auth login unlocks private repos. Usage: gh search repos 'query' --sort stars --limit 10",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-v2ex",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "V2EX community API — zero-config access to hot topics, latest posts, and user content",
        "body": "Agent Reach V2EX channel. Backend: V2EX public HTTP API. Tier 0 (zero-config). Access hot topics, latest posts, node content, and user profiles. No auth required. Usage: curl -s 'https://www.v2ex.com/api/topics/hot.json'",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-twitter",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Twitter/X social media search and read — requires twitter-cli login with cookies",
        "body": "Agent Reach Twitter/X channel. Backends: twitter-cli (preferred) > OpenCLI > bird CLI. Tier 1 (login required). Search tweets, read user posts, get trending topics. Requires TWITTER_AUTH_TOKEN and TWITTER_CT0 cookies. Retry chain: retry -> upgrade -> switch backend -> workaround.",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-bilibili",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Bilibili video platform search and metadata — Chinese video platform with rich API",
        "body": "Agent Reach Bilibili channel. Backends: bili-cli (preferred) > OpenCLI > Search API. Tier 1 (login optional). Search videos, get metadata, download subtitles. Read-only search works without login. Usage: bili search 'query' --type video -n 5",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-xiaohongshu",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "XiaoHongShu (RED) social platform — Chinese lifestyle and shopping platform with rich user content",
        "body": "Agent Reach XiaoHongShu channel. Backends: OpenCLI (desktop) > xiaohongshu-mcp (server) > xhs-cli. Tier 1 (login required). Desktop requires Chrome + OpenCLI extension; server uses headless browser via Docker. Search notes, get user profiles, browse content.",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-reddit",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Reddit community search and browsing — requires logged-in session via OpenCLI or rdt-cli",
        "body": "Agent Reach Reddit channel. Backends: OpenCLI (desktop) > rdt-cli (server). Tier 1 (login required). No zero-config path exists. Desktop: OpenCLI + Chrome extension. Server: rdt-cli with cookie auth. Search subreddits, get hot posts, user content.",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-linkedin",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "LinkedIn professional network scraper — profile search, job listings, company data (complex setup)",
        "body": "Agent Reach LinkedIn channel. Backends: linkedin-scraper-mcp (preferred) > Jina Reader. Tier 2 (complex setup). Requires running linkedin-scraper-mcp as MCP server on localhost:3000. Profile search, job listings, company information. Fallback: Jina Reader for public profile pages.",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-xiaoyuzhou",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Xiaoyuzhou FM podcast transcription — Chinese podcast platform audio-to-text via Whisper",
        "body": "Agent Reach Xiaoyuzhou channel. Backend: groq-whisper + ffmpeg. Tier 1 (free API key required). Downloads podcast episodes and transcribes via Groq Whisper API. Requires free Groq API key (gsk_...) and ffmpeg for audio processing. Usage: agent-reach transcribe URL",
        "parents": ["agent-reach"],
    },
    {
        "name": "agent-reach-xueqiu",
        "rtype": "cli_tool",
        "repo": "agent-reach",
        "cluster": "knowledge_wiki",
        "description": "Xueqiu (Snowball) Chinese stock market social platform — real-time stock discussions and financial data",
        "body": "Agent Reach Xueqiu channel. Backend: HTTP API with browser cookies. Tier 1 (login required). Chinese stock market social platform. Access stock discussions, portfolio data, market analysis. Requires xq_a_token cookie from browser session. Usage: Python urllib + cookie auth.",
        "parents": ["agent-reach"],
    },
    # ── gcode-harness modules ─────────────────────────────────────────
    {
        "name": "gcode-memory-graph",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness memory graph — petgraph-based persistent knowledge graph with ONNX embeddings, cascade retrieval, and confidence decay",
        "body": "Gcode Harness memory module. Uses petgraph DiGraph for knowledge representation. Memory types: Fact, Preference, Procedure, Correction, Negative. Local embeddings via all-MiniLM-L6-v2 (ONNX, tract). Cascade retrieval: embedding hits trigger BFS graph traversal. Confidence decay system with category-specific half-lives (Correction: 365d, Preference: 90d, Fact: 30d). Provenance tracking and reinforcement breadcrumbs. Async non-blocking pipeline. Sidecar relevance verification via LLM.",
        "parents": ["gcode-mcp-pool", "gcode-tool-system"],
    },
    {
        "name": "gcode-mcp-pool",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness MCP server connection pool — shared pool of MCP subprocesses with JSON-RPC over stdio, dynamic tool registration",
        "body": "Gcode Harness MCP module. Manages a shared pool of MCP server subprocesses. Communication via JSON-RPC over stdio. Dynamic tool registration from external servers. Per-session MCP tool access. Configurable server list in config.toml. Global singleton pattern (OnceCell) — all sessions share one MCP pool. Hot-reloadable via /reload.",
        "parents": [],
    },
    {
        "name": "gcode-tool-system",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness extensible tool registry — 30+ built-in tools with dynamic registration, typed parameters, and safety hooks",
        "body": "Gcode Harness tool module. Arc-based registry of 30+ built-in tools (read, write, bash, edit, search, etc.) plus dynamic registration from MCP servers. Each tool has typed parameters, description, and category. Safety hooks for autonomous mode. Global BASE_TOOLS cache (OnceLock). Tools are Arc<dyn Tool> for shared access.",
        "parents": ["gcode-mcp-pool"],
    },
    {
        "name": "gcode-swarm-core",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness swarm coordination — DAG-based task plans with git worktree isolation, inter-agent messaging, and role hierarchy",
        "body": "Gcode Harness swarm coordination module. Coordinator / Worktree Manager / Agent role hierarchy. DAG-based task plans with dependency resolution. Agent lifecycle: spawned, ready, running, blocked, completed, failed, stopped, crashed. Inter-agent communication via DMs, channels, broadcasts, shared context. Git worktree isolation for parallel work on same repo. TUI widgets for swarm and plan visualization.",
        "parents": ["gcode-tool-system"],
    },
    {
        "name": "gcode-agent-runtime",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness agent execution runtime — turn-based agent loop with streaming, tool calls, response recovery, and auto-poke",
        "body": "Gcode Harness agent module. Manages turn execution: send message -> receive stream -> extract tool calls -> execute -> continue. Supports both streaming (text_delta events) and capture (full response) modes. Auto-poke: re-prompts agent when incomplete todos remain. Response recovery on errors. Compaction trigger at 80% context window. Session persistence and crash recovery.",
        "parents": ["gcode-tool-system", "gcode-memory-graph"],
    },
    {
        "name": "gcode-socket-protocol",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness client-server protocol — Unix socket NDJSON transport with 50+ message types for multi-client architecture",
        "body": "Gcode Harness transport and protocol modules. Single-server, multi-client architecture. Unix domain sockets (Linux) or named pipes (Windows). Newline-delimited JSON wire format. 50+ message types covering: messages, cancellation, history, state, debug, subscriptions, session management, inter-agent communication, swarm coordination. Client auto-reconnect with exponential backoff.",
        "parents": [],
    },
    {
        "name": "gcode-ambient-mode",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "architect",
        "description": "Gcode Harness autonomous background agent — self-scheduling memory gardening, proactive scouting, and sleep consolidation",
        "body": "Gcode Harness ambient module. Autonomous background agent that runs without user prompting. Gardens memory graph (consolidate, prune, verify). Scouts for proactive work opportunities. Self-scheduling with adaptive resource-aware interval calculation. Sleep consolidation inspired by brain memory consolidation during sleep. Respects rate limits and user activity. Safety system for HITL permission on autonomous operations.",
        "parents": ["gcode-memory-graph"],
    },
    {
        "name": "gcode-safety-system",
        "rtype": "hook",
        "repo": "gcode-harness",
        "cluster": "quality_security",
        "description": "Gcode Harness safety and permissions — two-tier action classification, review queue, notification channels, custom classification rules",
        "body": "Gcode Harness safety module. Two-tier action classification: auto-allowed vs requires-permission. Review queue with persistent storage. Notification channels: email, desktop, webhook, TUI. Session transcript and summary generation. Custom classification rules via config. Designed for safe autonomous agent operation with human oversight.",
        "parents": ["gcode-agent-runtime", "gcode-ambient-mode"],
    },
]


def clean_text_for_embedding(name, description, body_text):
    yaml_re = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
    html_re = re.compile(r"<!--.*?-->", re.DOTALL)

    parts = []
    if description:
        desc = html_re.sub("", description).strip()
        if desc:
            parts.append(desc)
    if body_text:
        body = body_text[:500]
        body = yaml_re.sub("", body)
        body = html_re.sub("", body)
        body = body.strip()
        if body:
            parts.append(body)
    return " ".join(parts)[:512]


def guess_cluster(name, rtype, content_lower=""):
    signals = {
        "analyzer_planner": ["analyze", "plan", "investigate", "debug", "audit", "strategy", "retro", "health"],
        "architect": ["architecture", "design", "implement", "build", "structure", "model", "spec", "system", "protocol", "core", "runtime", "pool", "registry"],
        "frontend_ui": ["ui", "frontend", "css", "dashboard", "component", "visual", "web", "animation"],
        "backend_api": ["api", "database", "auth", "endpoint", "migration", "backend", "infra"],
        "devops_infra": ["deploy", "ship", "ci/cd", "docker", "release", "infra", "github"],
        "quality_security": ["test", "qa", "security", "verify", "tdd", "review", "scan", "safety", "permissions"],
        "knowledge_wiki": ["doc", "wiki", "learn", "memory", "note", "tutorial", "search", "feed", "social", "video", "news", "platform", "channel"],
    }
    name_l = name.lower()
    best = "general"
    best_score = 0
    for cluster, keywords in signals.items():
        score = sum(name_l.count(k) for k in keywords)
        score += sum(content_lower.count(k) for k in keywords) * 2
        if score > best_score:
            best_score = score
            best = cluster
    return best


def ensure_cluster_membership(conn, resource_id, cluster):
    conn.execute("""
        INSERT OR IGNORE INTO cluster_membership (cluster_name, resource_id, affinity)
        VALUES (?, ?, 0.8)
    """, (cluster, resource_id))


def create_resource_edges(conn, source_name, target_names):
    """Create edges from source to each target by name lookup."""
    src = conn.execute("SELECT id FROM resources WHERE name=?", (source_name,)).fetchone()
    if not src:
        return
    src_id = src[0]
    for tgt in target_names:
        tgt_row = conn.execute("SELECT id FROM resources WHERE name=?", (tgt,)).fetchone()
        if tgt_row:
            tgt_id = tgt_row[0]
            conn.execute("""
                INSERT OR IGNORE INTO resource_edges (source_id, target_id, edge_type, weight)
                VALUES (?, ?, 'depends_on', 0.7)
            """, (src_id, tgt_id))


def main():
    from sentence_transformers import SentenceTransformer

    print("[1/3] Loading embedding model...")
    model = SentenceTransformer(MODEL_ID, local_files_only=True)
    print(f"  loaded: {MODEL_ID} (dim {DIM})")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    print("[2/3] Processing resources...")
    ok = fail = skipped = 0

    parent_map = {}  # name -> list of parent names for edge creation

    for r in RESOURCES:
        name = r["name"]
        rtype = r["rtype"]
        repo = r["repo"]
        cluster = r.get("cluster") or guess_cluster(name, rtype, r["body"].lower()[:2000])
        desc = r["description"]
        body = r["body"]

        # Check existing
        existing = conn.execute("SELECT id FROM resources WHERE name=?", (name,)).fetchone()
        if existing:
            print(f"  EXISTS {name:35s} (id={existing[0]}) — will update")

        # Compute embedding
        embed_text = clean_text_for_embedding(name, desc, body)
        vec = model.encode([embed_text]).astype(np.float32)
        norm = np.linalg.norm(vec[0])
        if norm > 0:
            vec[0] = vec[0] / norm
        emb_bytes = vec[0].tobytes()
        tokens = len(body.split())

        parent_map[name] = r.get("parents", [])

        try:
            cur = conn.execute("""
                INSERT INTO resources (name, resource_type, description, body_text, repo_name, file_path,
                                       status, tier, cluster, embedding, token_count, times_triggered,
                                       times_correct, times_wrong)
                VALUES (?, ?, ?, ?, ?, '/context-harness/absorbed', 'active', 2, ?, ?, ?, 3, 0, 0)
                ON CONFLICT(name) DO UPDATE SET
                    body_text=excluded.body_text,
                    description=excluded.description,
                    embedding=excluded.embedding,
                    cluster=excluded.cluster,
                    status='active',
                    token_count=excluded.token_count,
                    updated_at=datetime('now')
                RETURNING id
            """, (name, rtype, desc, body, repo, cluster, emb_bytes, tokens))
            row = cur.fetchone()
            rid = row[0] if row else None

            if rid:
                ensure_cluster_membership(conn, rid, cluster)
                prefix = "agent-reach" if repo == "agent-reach" else "gcode"
                conn.execute("""
                    INSERT OR IGNORE INTO resource_edges (source_id, target_id, edge_type, weight)
                    VALUES (?, (
                        SELECT id FROM resources WHERE name=? AND resource_type='skill' LIMIT 1
                    ), 'part_of', 0.9)
                """, (rid, prefix))
                conn.commit()
                print(f"  OK    {name:35s} → id={rid}, cluster={cluster}, tokens={tokens}")
                ok += 1
            else:
                print(f"  FAIL  {name} — no rowid")
                fail += 1
        except Exception as e:
            conn.rollback()
            print(f"  FAIL  {name}: {e}")
            fail += 1

    # Create dependency edges between gcode modules
    print("\n  Creating dependency edges...")
    for name, parents in parent_map.items():
        for parent in parents:
            create_resource_edges(conn, name, [parent])
            print(f"    edge: {name} -> {parent}")

    conn.commit()
    print(f"\n  Summary: {ok} ok, {fail} failed, {skipped} skipped")

    print("[3/3] Verifying...")
    total = conn.execute("SELECT count(*) FROM resources WHERE status='active'").fetchone()[0]
    with_emb = conn.execute("SELECT count(*) FROM resources WHERE status='active' AND embedding IS NOT NULL").fetchone()[0]
    routable = conn.execute("SELECT count(*) FROM resources WHERE status='active' AND embedding IS NOT NULL AND resource_type IN ('skill','command','agent','mcp','hook','cli_tool')").fetchone()[0]
    print(f"  Total active resources: {total}")
    print(f"  With embedding: {with_emb}")
    print(f"  Routable: {routable}")

    conn.close()
    print("\nDone. Restart MCP server to pick up new resources.")


if __name__ == "__main__":
    main()
