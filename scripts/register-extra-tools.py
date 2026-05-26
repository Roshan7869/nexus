#!/usr/bin/env python3
"""Register additional system tools found during audit."""
import sqlite3, sqlite_vec, ollama

NEXUS_DB = '/home/roshan/nexus/db/nexus.db'
conn = sqlite3.connect(NEXUS_DB)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
cur = conn.cursor()

EMBED_MODEL = 'nomic-embed-text:latest'

def get_emb(text):
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:12000])
        return sqlite_vec.serialize_float32(resp['embedding'])
    except:
        return None

more_resources = [
    ("curl", "cli_tool", "custom", "/usr/bin/curl",
     "curl: command-line data transfer tool. HTTP/HTTPS/FTP. Essential for API testing and automation.",
     "curl is a command-line tool for transferring data with URL syntax. Supports HTTP, HTTPS, FTP, and many protocols. Essential for API testing, web scraping, and automation scripts."),
    ("wget", "cli_tool", "custom", "/usr/bin/wget",
     "wget: command-line file downloader with recursive download and resume support.",
     "wget downloads files from the web. Supports recursive downloads, download resume, mirroring, and bandwidth throttling."),
    ("npx", "cli_tool", "custom", "/usr/bin/npx",
     "npx: Node.js package runner. Runs npm packages without global install.",
     "npx executes npm package binaries directly without global install. Useful for create-next-app, prisma, tsx, etc."),
    ("medusa-commerce-platform", "skill", "medusa", "medusa/",
     "Medusa: Node.js/TypeScript headless commerce engine. Products, orders, customers, payments.",
     "Full-stack commerce engine. Features: product catalog, order processing, customer management, payment integration, inventory tracking, shipping, multi-tenant. PostgreSQL, Express, Redis. Turbo repo with Jest."),
    ("optillm-cepo", "skill", "optillm", "optillm/cepo/",
     "CePO (Cerebras Planning & Optimization): multi-step planning combining MCTS with verifier-guided search.",
     "Combines MCTS with verifier-guided search for multi-step planning. +18.6 points on Math-L5 from Llama 3.3 70B."),
    ("optillm-mcp-plugin", "skill", "optillm", "optillm/plugins/mcp_plugin.py",
     "OptiLLM MCP plugin: connects proxy to MCP servers for tool-using capabilities during inference.",
     "MCP client plugin. Lets OptiLLM connect to MCP servers and use their tools. Configured via ~/.optillm/mcp_config.json."),
    ("optillm-memory-plugin", "skill", "optillm", "optillm/plugins/memory_plugin.py",
     "OptiLLM memory plugin: unbounded context via chunking and retrieval for long conversations.",
     "Short-term memory for unbounded context. Chunks conversation history and retrieves relevant chunks."),
    ("optillm-plugins", "skill", "optillm", "optillm/plugins/",
     "OptiLLM plugin system: MCP, memory, privacy, code execution, JSON, SPL, DeepThink, LongCePO plugins.",
     "Plugin architecture. Plugins intercept requests/responses. Key: mcp_plugin, memory_plugin, privacy_plugin, executecode_plugin, json_plugin, spl/, deepthink/, longcepo/."),
]

inserted = 0
for name, rtype, repo, fpath, desc, body in more_resources:
    existing = cur.execute("SELECT id FROM resources WHERE name=?", (name,)).fetchone()
    if existing:
        print(f"SKIP: {name}")
        continue
    text_for_emb = "{0} {1} {2}".format(name, rtype, desc[:1000])
    emb = get_emb(text_for_emb)
    token_count = len(body) // 3
    cur.execute("""
        INSERT INTO resources(name, resource_type, repo_name, file_path,
                              description, body_text, embedding, token_count,
                              status, tier, times_triggered, times_correct, times_wrong)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'active', 2, 0, 0, 0)
    """, (name, rtype, repo, fpath, desc[:200], body, emb, token_count))
    print("+ {0} ({1})".format(name, rtype))
    inserted += 1

conn.commit()

cur.execute("SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type ORDER BY COUNT(*) DESC")
print("\n=== FINAL COUNTS ===")
for row in cur.fetchall():
    print("  {0:20s} {1}".format(row[0], row[1]))

total = cur.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print("\nTOTAL RESOURCES: {0}".format(total))
conn.close()
