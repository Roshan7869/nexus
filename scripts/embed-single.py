#!/usr/bin/env python3
"""~/nexus/scripts/embed-single.py — Embed and index a single file"""
import sys, sqlite3, sqlite_vec, ollama
from pathlib import Path

NEXUS_DB = Path.home() / 'nexus/db/nexus.db'
EMBED_MODEL = 'nomic-embed-text:latest'

def index_file(filepath: str):
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {filepath}")
        return
    
    text = path.read_text()[:12000]
    name = path.stem
    
    resp = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    emb_bytes = sqlite_vec.serialize_float32(resp['embedding'])
    
    conn = sqlite3.connect(str(NEXUS_DB))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    
    conn.execute("""
        INSERT OR REPLACE INTO resources(name, resource_type, repo_name, file_path, 
                                         description, body_text, embedding, token_count)
        VALUES(?,?,?,?,?,?,?,?)
    """, (name, 'skill', 'inbox', str(path), text[:200], text, emb_bytes, len(text)//3))
    conn.commit()
    conn.close()
    print(f"Indexed: {name}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: embed-single.py <filepath>")
        sys.exit(1)
    index_file(sys.argv[1])