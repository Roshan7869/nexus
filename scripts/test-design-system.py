#!/usr/bin/env python3
"""Test-design-system.py: isolate the issue"""
import sqlite3, sqlite_vec, ollama, time
from pathlib import Path

OD = Path('/home/roshan/open-design')
NEXUS_DB = '/home/roshan/nexus/db/nexus.db'
EMBED_MODEL = 'nomic-embed-text:latest'

NX = sqlite3.connect(NEXUS_DB, timeout=60)
NX.enable_load_extension(True)
sqlite_vec.load(NX)

# Check if this specific item exists
SUBDIR = 'agentic'
NAME = f"ds-{SUBDIR}"

row = NX.execute("SELECT id FROM resources WHERE name=?", (NAME,)).fetchone()
print(f"Pre-check: {NAME} exists? {row is not None}")

# Read the file
readme = OD / 'design-systems' / SUBDIR / 'DESIGN.md'
content = readme.read_text(encoding='utf-8', errors='ignore')
print(f"Read {len(content)} chars from {readme}")

# Get embedding
try:
    t_start = time.time()
    resp = ollama.embeddings(model=EMBED_MODEL, prompt=content[:1000])
    t_end = time.time()
    emb = sqlite_vec.serialize_float32(resp['embedding'])
    print(f"Embedding OK ({len(emb)} bytes, {t_end-t_start:.2f}s)")
except Exception as e:
    print(f"Embed FAIL: {e}")
    NX.close()
    exit(1)

# Insert
try:
    NX.execute("""
        INSERT INTO resources(name,resource_type,repo_name,file_path,description,body_text,embedding,token_count)
        VALUES(?,?,?,?,?,?,?,?)
    """, (NAME, 'design_system', 'open-design/design-systems', str(readme), content[:200].replace('\n',' ').strip(), content[:15000], emb, len(content)//3))
    print("Inserted (not committed yet)")
except sqlite3.IntegrityError as e:
    print(f"IntegrityError: {e}")
    NX.close()
    exit(1)
except Exception as e:
    print(f"InsertError: {e}")
    NX.close()
    exit(1)

# Commit
try:
    NX.commit()
    print("Committed")
except Exception as e:
    print(f"CommitError: {e}")
    NX.close()
    exit(1)

# Verify
count = NX.execute("SELECT COUNT(*) FROM resources WHERE name=?", (NAME,)).fetchone()[0]
print(f"Post-verify: {count} row(s) for {NAME}")

# Also check total
total = NX.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print(f"Total resources: {total}")

NX.close()
print("Done.")
