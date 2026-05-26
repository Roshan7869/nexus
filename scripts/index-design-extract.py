#!/usr/bin/env python3
"""Index design-extract repo into NEXUS — skills, commands, extractors, formatters, MCP, vocabularies, docs"""
import sqlite3, sqlite_vec, ollama
from pathlib import Path
import hashlib, sys, json, re

DE = Path.home() / 'design-extract'
NEXUS_DB = Path.home() / 'nexus/db/nexus.db'
EMBED_MODEL = 'nomic-embed-text:latest'

NX = sqlite3.connect(NEXUS_DB, timeout=60)
NX.enable_load_extension(True)
sqlite_vec.load(NX)

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

def detect_cluster(desc, body):
    c = (desc + " " + body[:2000]).lower()
    scores = {}
    triggers = {
        "frontend_ui": ["ui", "css", "html", "tailwind", "theme", "color", "typography", "component", "design", "figma", "shadcn", "react", "svelte", "vue", "responsive", "layout", "spacing", "shadow", "border", "font", "style", "token", "brand"],
        "analyzer_planner": ["extract", "analyze", "score", "grade", "audit", "accessibility", "wcag", "a11y", "health", "lint", "review", "diff", "compare", "scoring", "perf", "performance", "seo"],
        "architect": ["architecture", "system", "structure", "pattern", "framework", "stack", "build", "scaffold"],
        "backend_api": ["api", "server", "database", "endpoint", "mcp", "sdk", "cli", "npm", "package"],
        "devops_infra": ["deploy", "ci", "cd", "github-action", "docker", "pipeline", "watch", "sync", "drift", "replay"],
        "quality_security": ["test", "qa", "verify", "remediation", "contrast", "failing", "drift", "lint"],
        "knowledge_wiki": ["doc", "guide", "reference", "learn", "vocabulary", "style", "art-deco", "brutalist", "cyberpunk", "editorial"],
        "general": ["utility", "helper", "widget", "cookie", "chat"],
    }
    for cluster, kws in triggers.items():
        scores[cluster] = sum(1 for kw in kws if kw in c)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"

def process_resource(name, rtype, repo_name, file_path, desc_text, body_text, cluster_hint=None):
    if NX.execute("SELECT id FROM resources WHERE name=? AND repo_name=?", (name, repo_name)).fetchone():
        return False, "exists"
    emb = get_emb(desc_text)
    if emb is None:
        return False, "embed_failed"
    cluster = cluster_hint or detect_cluster(desc_text, body_text)
    token_count = len(body_text) // 3
    cur = NX.cursor()
    cur.execute("""
        INSERT INTO resources(name,resource_type,repo_name,file_path,description,body_text,embedding,token_count,status,tier,times_triggered,times_correct,times_wrong,cluster)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (name, rtype, repo_name, str(file_path), desc_text[:200].replace('\n',' ').strip(), body_text[:15000], emb, token_count, 'active', 2, 5, 0, 0, cluster))
    resource_id = cur.lastrowid
    cluster_map = {
        "analyzer_planner": 1, "architect": 2, "frontend_ui": 3,
        "backend_api": 4, "devops_infra": 5, "quality_security": 6,
        "knowledge_wiki": 7, "general": 8
    }
    cluster_id = cluster_map.get(cluster, 8)
    NX.execute("""
        INSERT INTO cluster_membership(cluster_name,resource_id,affinity)
        VALUES(?,?,0.7)
        ON CONFLICT(cluster_name,resource_id) DO UPDATE SET affinity=excluded.affinity
    """, (cluster, resource_id))
    return True, cluster

def extract_frontmatter_desc(content):
    m = re.search(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if m:
        fm = m.group(1)
        dm = re.search(r'description:\s*"?([^"\n]+)"?', fm)
        if dm:
            return dm.group(1).strip()
    lines = content.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('---') and len(line) > 10:
            return line[:200]
    return ""

batch = 0
total = 0
committed = 0

def maybe_commit():
    global batch, committed
    if batch >= 10:
        NX.commit()
        committed += batch
        print(f"  committed {batch} (running total: {committed})", flush=True)
        batch = 0

# ─── 1. Skill ───
print("\n=== 1. Skill ===", flush=True)
skill_md = DE / 'skills/extract-design/SKILL.md'
if skill_md.exists():
    content = skill_md.read_text(encoding='utf-8', errors='ignore')
    desc = extract_frontmatter_desc(content)
    ok, info = process_resource("extract-design", 'skill', 'design-extract', skill_md,
                                desc or "Extract full design language from any website URL", content)
    if ok:
        total += 1; batch += 1
        print(f"  + extract-design (skill) -> {info}")
maybe_commit()

# ─── 2. Commands ───
print("\n=== 2. Commands ===", flush=True)
cmd_dir = DE / 'commands'
cmd_count = 0
for md in sorted(cmd_dir.glob('*.md')):
    content = md.read_text(encoding='utf-8', errors='ignore')
    desc = extract_frontmatter_desc(content)
    name = f"cmd-{md.stem}"
    ok, info = process_resource(name, 'command', 'design-extract/commands', md,
                                desc or f"CLI command: {md.stem}", content, "frontend_ui")
    if ok:
        total += 1; batch += 1; cmd_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"commands: {cmd_count}", flush=True)

# ─── 3. Extractors ───
print("\n=== 3. Extractors (44 modules) ===", flush=True)
ext_dir = DE / 'src/extractors'
ext_count = 0
for js in sorted(ext_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    # Extract description from comments at top
    desc = ""
    m = re.search(r'(?://|/\*)\s*(.+?)(?:\*/|$)', content)
    if m:
        desc = m.group(1).strip()[:200]
    if not desc:
        desc = f"Design extractor: {js.stem} — extracts {js.stem.replace('-', ' ')} from web pages"
    name = f"extractor-{js.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/extractors', js, desc, content, "frontend_ui")
    if ok:
        total += 1; batch += 1; ext_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"extractors: {ext_count}", flush=True)

# ─── 4. Formatters ───
print("\n=== 4. Formatters (24 output generators) ===", flush=True)
fmt_dir = DE / 'src/formatters'
fmt_count = 0
for js in sorted(fmt_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = ""
    m = re.search(r'(?://|/\*)\s*(.+?)(?:\*/|$)', content)
    if m:
        desc = m.group(1).strip()[:200]
    if not desc:
        desc = f"Output formatter: {js.stem} — generates {js.stem.replace('-', ' ')} format"
    name = f"formatter-{js.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/formatters', js, desc, content, "frontend_ui")
    if ok:
        total += 1; batch += 1; fmt_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"formatters: {fmt_count}", flush=True)

# ─── 5. Classifiers ───
print("\n=== 5. Classifiers ===", flush=True)
cls_dir = DE / 'src/classifiers'
cls_count = 0
for js in sorted(cls_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = f"Smart classifier: {js.stem} — intelligent design classification"
    name = f"classifier-{js.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/classifiers', js, desc, content, "analyzer_planner")
    if ok:
        total += 1; batch += 1; cls_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"classifiers: {cls_count}", flush=True)

# ─── 6. Vocabularies ───
print("\n=== 6. Vocabularies (design style vocabularies) ===", flush=True)
voc_dir = DE / 'src/vocabularies'
voc_count = 0
for js in sorted(voc_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = f"Design vocabulary: {js.stem} — {js.stem.replace('-', ' ')} design style tokens and patterns"
    name = f"vocabulary-{js.stem}"
    ok, info = process_resource(name, 'design_system', 'design-extract/vocabularies', js, desc, content, "knowledge_wiki")
    if ok:
        total += 1; batch += 1; voc_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"vocabularies: {voc_count}", flush=True)

# ─── 7. MCP Server/Tools/Resources ───
print("\n=== 7. MCP ===", flush=True)
mcp_dir = DE / 'src/mcp'
mcp_count = 0
for js in sorted(mcp_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = f"MCP {js.stem}: Model Context Protocol {js.stem} for design-extract"
    name = f"mcp-{js.stem}"
    ok, info = process_resource(name, 'mcp', 'design-extract/mcp', js, desc, content, "backend_api")
    if ok:
        total += 1; batch += 1; mcp_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"mcp: {mcp_count}", flush=True)

# ─── 8. Core Source Modules ───
print("\n=== 8. Core Source Modules ===", flush=True)
src_dir = DE / 'src'
src_count = 0
for js in sorted(src_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = ""
    m = re.search(r'(?://|/\*)\s*(.+?)(?:\*/|$)', content)
    if m:
        desc = m.group(1).strip()[:200]
    if not desc:
        desc = f"Core module: {js.stem}"
    name = f"core-{js.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/src', js, desc, content[:10000], "backend_api")
    if ok:
        total += 1; batch += 1; src_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"core modules: {src_count}", flush=True)

# ─── 9. Utils ───
print("\n=== 9. Utils ===", flush=True)
utils_dir = DE / 'src/utils'
utils_count = 0
for js in sorted(utils_dir.glob('*.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = f"Utility: {js.stem}"
    name = f"utils-{js.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/utils', js, desc, content, "general")
    if ok:
        total += 1; batch += 1; utils_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"utils: {utils_count}", flush=True)

# ─── 10. CLI Binary ───
print("\n=== 10. CLI Binary ===", flush=True)
cli_bin = DE / 'bin/design-extract.js'
if cli_bin.exists():
    content = cli_bin.read_text(encoding='utf-8', errors='ignore')
    desc = "designlang CLI — Extract design languages from any URL. v12.10.0"
    ok, info = process_resource("designlang-cli", 'cli_tool', 'design-extract/bin', cli_bin, desc, content[:10000], "backend_api")
    if ok:
        total += 1; batch += 1
        print(f"  + designlang-cli -> {info}")
maybe_commit()

# ─── 11. Docs ───
print("\n=== 11. Docs ===", flush=True)
docs_dir = DE / 'docs'
doc_count = 0
for md in sorted(docs_dir.rglob('*.md')):
    content = md.read_text(encoding='utf-8', errors='ignore')
    if len(content) < 200:
        continue
    desc = extract_frontmatter_desc(content)
    rel = str(md.relative_to(DE))
    name = f"doc-{md.stem}"
    # Make unique if duplicate stems
    existing = NX.execute("SELECT id FROM resources WHERE name=? AND repo_name=?", (name, 'design-extract/docs')).fetchone()
    if existing:
        name = f"doc-{rel.replace('/', '-')}"
    ok, info = process_resource(name, 'doc', 'design-extract/docs', md,
                                desc or f"Documentation: {md.stem}", content, "knowledge_wiki")
    if ok:
        total += 1; batch += 1; doc_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"docs: {doc_count}", flush=True)

# ─── 12. Superpower Specs/Plans ───
print("\n=== 12. Superpower Specs & Plans ===", flush=True)
sp_dir = DE / 'docs/superpowers'
sp_count = 0
for md in sorted(sp_dir.rglob('*.md')):
    content = md.read_text(encoding='utf-8', errors='ignore')
    if len(content) < 200:
        continue
    desc = extract_frontmatter_desc(content)
    rel = str(md.relative_to(DE))
    name = f"spec-{md.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/superpowers', md,
                                desc or f"Design spec/plan: {md.stem}", content, "architect")
    if ok:
        total += 1; batch += 1; sp_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"specs/plans: {sp_count}", flush=True)

# ─── 13. Extensions ───
print("\n=== 13. Extensions ===", flush=True)
ext_types = ['figma-plugin', 'chrome-extension', 'raycast-extension', 'vscode-extension', 'github-action']
ext_count = 0
for ext_type in ext_types:
    ext_path = DE / ext_type
    if not ext_path.exists():
        continue
    for f in sorted(ext_path.rglob('*')):
        if f.is_file() and f.suffix in ('.js', '.json', '.html', '.md', '.yml'):
            content = f.read_text(encoding='utf-8', errors='ignore')
            if len(content) < 100:
                continue
            rel = str(f.relative_to(DE))
            name = f"{ext_type.replace('-', '-')}-{f.stem}"
            desc = f"{ext_type}: {f.name}"
            ok, info = process_resource(name, 'spec', f'design-extract/{ext_type}', f, desc, content[:10000], "frontend_ui")
            if ok:
                total += 1; batch += 1; ext_count += 1
                print(f"  + {name} -> {info}")
maybe_commit()
print(f"extensions: {ext_count}", flush=True)

# ─── 14. Marketplace listings ───
print("\n=== 14. Marketplace Listings ===", flush=True)
mkt_dir = DE / 'marketplace'
mkt_count = 0
for md in sorted(mkt_dir.glob('*.md')):
    content = md.read_text(encoding='utf-8', errors='ignore')
    desc = extract_frontmatter_desc(content)
    name = f"marketplace-{md.stem}"
    ok, info = process_resource(name, 'doc', 'design-extract/marketplace', md,
                                desc or f"Marketplace listing: {md.stem}", content, "knowledge_wiki")
    if ok:
        total += 1; batch += 1; mkt_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"marketplace: {mkt_count}", flush=True)

# ─── 15. Tests ───
print("\n=== 15. Tests ===", flush=True)
test_dir = DE / 'tests'
test_count = 0
for js in sorted(test_dir.glob('*.test.js')):
    content = js.read_text(encoding='utf-8', errors='ignore')
    desc = f"Test suite: {js.stem}"
    name = f"test-{js.stem}"
    ok, info = process_resource(name, 'spec', 'design-extract/tests', js, desc, content[:8000], "quality_security")
    if ok:
        total += 1; batch += 1; test_count += 1
        print(f"  + {name} -> {info}")
maybe_commit()
print(f"tests: {test_count}", flush=True)

# ─── Final ───
NX.commit()
final = NX.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
de_count = NX.execute("SELECT COUNT(*) FROM resources WHERE repo_name LIKE 'design-extract%'").fetchone()[0]
emb_count = NX.execute("SELECT COUNT(*) FROM resources WHERE embedding IS NOT NULL").fetchone()[0]

print(f"\n{'='*60}", flush=True)
print(f"DESIGN-EXTRACT INGESTION COMPLETE", flush=True)
print(f"  New resources: {total}", flush=True)
print(f"  design-extract total: {de_count}", flush=True)
print(f"  TOTAL NEXUS resources: {final}", flush=True)
print(f"  Embedded resources: {emb_count}", flush=True)
print(f"{'='*60}", flush=True)

NX.close()
