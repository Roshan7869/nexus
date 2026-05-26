#!/usr/bin/env python3
"""~/nexus/scripts/index-vault.py — Optimized indexer"""
import sqlite3, sqlite_vec, ollama
from pathlib import Path
import hashlib, time

KG_DB = Path.home() / 'wiki/vault/.db/claude_code_knowledge.db'
NEXUS_DB = Path.home() / 'nexus/db/nexus.db'
EMBED_MODEL = 'nomic-embed-text:latest'

TYPE_MAP = {
    'resource': 'skill', 'skill': 'skill', 'agent': 'agent',
    'hook': 'hook', 'command': 'command', 'cli': 'cli_tool',
    'cli_tool': 'cli_tool', 'mcp_server': 'mcp', 'mcp': 'mcp',
    'plugin': 'skill', 'topic': 'topic',
}

NX = sqlite3.connect(str(NEXUS_DB))
NX.enable_load_extension(True)
sqlite_vec.load(NX)

KG = sqlite3.connect(str(KG_DB))

def get_emb(text):
    h = hashlib.sha256(text.encode()).hexdigest()
    row = NX.execute("SELECT embedding FROM embeddings_cache WHERE text_hash=?", (h,)).fetchone()
    if row:
        return row[0]
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:12000])
        emb = sqlite_vec.serialize_float32(resp['embedding'])
        NX.execute("INSERT OR IGNORE INTO embeddings_cache(text_hash,text_snippet,embedding) VALUES(?,?,?)", (h, text[:500], emb))
        return emb
    except:
        return None

def index(name, rtype, desc, path, repo):
    if not name or NX.execute("SELECT id FROM resources WHERE name=?", (name,)).fetchone():
        return False
    desc_str = str(desc or '')
    emb = get_emb(f"{name} {rtype} {desc_str[:1000]}")
    NX.execute("INSERT INTO resources(name,resource_type,repo_name,file_path,description,body_text,embedding,token_count) VALUES(?,?,?,?,?,?,?,?)",
               (str(name), rtype, str(repo or 'unknown'), str(path or ''), desc_str[:200], desc_str, emb, len(desc_str)//3))
    return True

total = 0

# skills
for r in KG.execute("SELECT name, description, path, repo FROM skills"):
    if index(r[0], 'skill', r[1], r[2], r[3]): total += 1
    if total % 25 == 0: NX.commit(); print(f"  skills: {total}", flush=True)
NX.commit()
print(f"  skills: {total}", flush=True)

# agents
n = 0
for r in KG.execute("SELECT name, description, path, repo FROM agents"):
    if index(r[0], 'agent', r[1], r[2], r[3]): n += 1
    if (total+n) % 25 == 0: NX.commit(); print(f"  agents: {n}", flush=True)
NX.commit()
total += n
print(f"  agents: {n} (total: {total})", flush=True)

# hooks
n = 0
for r in KG.execute("SELECT name, description, path, repo FROM hooks"):
    if index(r[0], 'hook', r[1], r[2], r[3]): n += 1
    if (total+n) % 25 == 0: NX.commit(); print(f"  hooks: {n}", flush=True)
NX.commit()
total += n
print(f"  hooks: {n} (total: {total})", flush=True)

# commands
n = 0
for r in KG.execute("SELECT name, type, description, path, repo FROM commands"):
    if index(r[0], 'command', r[2], r[3], r[4]): n += 1
    if (total+n) % 25 == 0: NX.commit(); print(f"  commands: {n}", flush=True)
NX.commit()
total += n
print(f"  commands: {n} (total: {total})", flush=True)

# cli_tools
n = 0
for r in KG.execute("SELECT name, path, description, repo FROM cli_tools"):
    if index(r[0], 'cli_tool', r[2], r[1], r[3]): n += 1
    if (total+n) % 25 == 0: NX.commit(); print(f"  cli_tools: {n}", flush=True)
NX.commit()
total += n
print(f"  cli_tools: {n} (total: {total})", flush=True)

# mcp_servers
n = 0
for r in KG.execute("SELECT name, type, description, config_path, repo FROM mcp_servers"):
    if index(r[0], 'mcp', r[2], r[3], r[4]): n += 1
    if (total+n) % 25 == 0: NX.commit(); print(f"  mcp: {n}", flush=True)
NX.commit()
total += n
print(f"  mcp: {n} (total: {total})", flush=True)

# resources (with type column)
n = 0
for r in KG.execute("SELECT name, type, description, path, repo FROM resources"):
    raw_type = str(r[1]) if r[1] else 'skill'
    mapped_type = TYPE_MAP.get(raw_type, 'skill')
    if index(r[0], mapped_type, r[2], r[3], r[4]): n += 1
    if (total+n) % 25 == 0: NX.commit(); print(f"  resources: {n}", flush=True)
NX.commit()
total += n
print(f"  resources: {n} (total: {total})", flush=True)

count = NX.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
emb_count = NX.execute("SELECT COUNT(*) FROM resources WHERE embedding IS NOT NULL").fetchone()[0]
print(f"\nFinal: {count} resources, {emb_count} with embeddings")
NX.close()
KG.close()