#!/usr/bin/env python3
"""Index open-design repo into NEXUS — one file at a time, resumable"""
import sqlite3, sqlite_vec, ollama
from pathlib import Path
import hashlib, sys, time

OD = Path.home() / 'open-design'
NEXUS_DB = Path.home() / 'nexus/db/nexus.db'
EMBED_MODEL = 'nomic-embed-text:latest'
EMBED_TIMEOUT = 30

NX = sqlite3.connect(NEXUS_DB, timeout=60)
NX.enable_load_extension(True)
sqlite_vec.load(NX)

# In-memory embed cache
emb_cache = {}
def get_emb(text):
    h = hashlib.sha256(text.encode()).hexdigest()
    if h in emb_cache:
        return emb_cache[h]
    row = NX.execute("SELECT embedding FROM embeddings_cache WHERE text_hash=?", (h,)).fetchone()
    if row:
        emb_cache[h] = row[0]
        return row[0]
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:8000])
        emb = sqlite_vec.serialize_float32(resp['embedding'])
        NX.execute("INSERT OR IGNORE INTO embeddings_cache(text_hash,text_snippet,embedding) VALUES(?,?,?)", (h, text[:500], emb))
        emb_cache[h] = emb
        return emb
    except Exception as e:
        print(f"  [EMBED ERROR] {e}", flush=True)
        return None

def process_file(path, name, rtype, repo_name, desc_text, body_text):
    if NX.execute("SELECT id FROM resources WHERE name=? AND repo_name=?", (name, repo_name)).fetchone():
        return False
    emb = get_emb(desc_text)
    if emb is None:
        return False
    NX.execute("""
        INSERT OR IGNORE INTO resources(name,resource_type,repo_name,file_path,description,body_text,embedding,token_count)
        VALUES(?,?,?,?,?,?,?,?)
    """, (name, rtype, repo_name, str(path), desc_text[:200].replace('\n',' ').strip(), body_text[:15000], emb, len(body_text)//3))
    return True

batch = 0
total = 0

# --- Skills ---
print("Indexing skills...", flush=True)
skills_dir = OD / 'skills'
skill_dirs = sorted([d for d in skills_dir.iterdir() if d.is_dir()])
for i, subdir in enumerate(skill_dirs):
    name = subdir.name
    for fname in ['SKILL.md', 'README.md', 'index.md']:
        readme = subdir / fname
        if readme.exists():
            break
    else:
        continue
    try:
        content = readme.read_text(encoding='utf-8', errors='ignore')
    except:
        continue
    if process_file(readme, f"skill-{name}", 'skill', 'open-design/skills',
                    f"{name} skill {content[:1000]}", content):
        total += 1
        batch += 1
    if batch >= 10:
        NX.commit()
        print(f"  +{batch} (total {total}/{len(skill_dirs)})", flush=True)
        batch = 0
    sys.stdout.flush()
NX.commit()
print(f"skills: {total}", flush=True)

# --- Design systems ---
print("Indexing design-systems...", flush=True)
ds_dir = OD / 'design-systems'
ds_total = 0
ds_dirs = sorted([d for d in ds_dir.iterdir() if d.is_dir() and d.name != 'README.md'])
for subdir in ds_dirs:
    for fname in ['DESIGN.md', 'README.md', 'index.md']:
        readme = subdir / fname
        if readme.exists():
            break
    else:
        continue
    try:
        content = readme.read_text(encoding='utf-8', errors='ignore')
    except:
        continue
    if process_file(readme, f"ds-{subdir.name}", 'design_system', 'open-design/design-systems',
                    f"{subdir.name} design-system {content[:1000]}", content):
        ds_total += 1
        batch += 1
    if batch >= 10:
        NX.commit()
        print(f"  +{batch}", flush=True)
        batch = 0
    sys.stdout.flush()
NX.commit()
print(f"design-systems: {ds_total}", flush=True)
total += ds_total

# --- Craft ---
print("Indexing craft...", flush=True)
craft_dir = OD / 'craft'
craft_count = 0
for md in sorted(craft_dir.glob('*.md')):
    try:
        content = md.read_text(encoding='utf-8', errors='ignore')
    except:
        continue
    name = f"craft-{md.stem}"
    if process_file(md, name, 'craft', 'open-design/craft',
                    f"{md.stem} craft {content[:500]}", content):
        craft_count += 1
NX.commit()
print(f"craft: {craft_count}", flush=True)
total += craft_count

# --- Docs ---
print("Indexing docs...", flush=True)
docs_dir = OD / 'docs'
doc_count = 0
for md in sorted(docs_dir.glob('*.md')):
    try:
        content = md.read_text(encoding='utf-8', errors='ignore')
    except:
        continue
    name = f"doc-{md.stem}"
    if process_file(md, name, 'doc', 'open-design/docs',
                    f"{md.stem} doc {content[:500]}", content):
        doc_count += 1
NX.commit()
print(f"docs: {doc_count}", flush=True)
total += doc_count

# --- Specs ---
print("Indexing specs...", flush=True)
spec_dir = OD / 'specs' / 'current'
spec_count = 0
for md in sorted(spec_dir.rglob('*.md')):
    try:
        content = md.read_text(encoding='utf-8', errors='ignore')
    except:
        continue
    name = f"spec-{md.stem}"
    if process_file(md, name, 'spec', 'open-design/specs',
                    f"{md.stem} spec {content[:500]}", content):
        spec_count += 1
NX.commit()
print(f"specs: {spec_count}", flush=True)
total += spec_count

final = NX.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
od_count = NX.execute("SELECT COUNT(*) FROM resources WHERE repo_name LIKE 'open-design%'").fetchone()[0]
emb_count = NX.execute("SELECT COUNT(*) FROM resources WHERE embedding IS NOT NULL").fetchone()[0]
print(f"\nDONE: {od_count} open-design resources (total NEXUS: {final}, {emb_count} embedded)", flush=True)
NX.close()
