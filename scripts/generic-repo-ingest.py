#!/usr/bin/env python3
"""
generic-repo-ingest.py — Generic repository ingestion into NEXUS

Scans any codebase, discovers skills, tools, agents, configs, and docs,
then registers them in nexus.db with embeddings + cluster assignments.

Usage:
    cd ~/nexus && python scripts/generic-repo-ingest.py /path/to/repo [--tag myproject]

Features:
    - Recursive *.py, *.ts, *.js, *.sh, *.md discovery
    - Extracts descriptions from docstrings, JSDoc, README headers
    - Generates embeddings via Ollama (nomic-embed-text)
    - Auto-detects resource type: skill, agent, cli_tool, mcp, design_system, doc
    - Auto-assigns to NEXUS clusters based on content keywords
    - Resumable — skips already-registered resources
    - Idempotent — safe to re-run

Architecture:
    ┌─────────────┐     ┌──────────────┐     ┌──────────────┐
    │ FileScanner │ ──▶ │ TypeDetector │ ──▶ │ Embedder     │
    └─────────────┘     └──────────────┘     └──────────────┘
                                                    │
    ┌─────────────┐     ┌──────────────┐     ┌────▼───────────┐
    │ NEXUS DB    │ ◀── │ Inserter     │ ◀── │ ClusterMatcher │
    └─────────────┘     └──────────────┘     └────────────────┘
"""
import sqlite3, sqlite_vec, ollama, re, hashlib, sys, os, argparse, json
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
EMBED_MODEL = 'nomic-embed-text:latest'
EMBED_TIMEOUT = 30
MAX_BODY_LEN = 15000
MAX_DESC_LEN = 500
DEFAULT_TAG = "unknown"

# ── Type Signatures ───────────────────────────────────────────────────────────
SIGS = {
    "skill":      {"files": ["SKILL.md", "skill.py"],           "keywords": ["skill", "abilities", "tools", "orchestration"]},
    "agent":      {"files": ["agent.py", "agents/"],             "keywords": ["agent", "swarm", "persona", "coordinator"]},
    "cli_tool":   {"exts":  [".sh", ".cli", ".bin"],            "keywords": ["cli", "command", "tool", "script", "bash"]},
    "mcp":        {"files": ["mcp_", "mcp-plugin"],             "keywords": ["mcp", "context protocol", "resource", "tool"] },
    "doc":        {"exts":  [".md"],                            "keywords": ["guide", "doc", "tutorial", "reference", "README"]},
    "config":     {"files": ["config", "settings", "yaml", "json"], "keywords": ["config", "settings", "env", "parameters"]},
    "design_system": {"files": ["DESIGN.md", "design-system"],   "keywords": ["design", "component", "ui", "token", "layout"]},
    "code":       {"exts":  [".py", ".ts", ".js", ".rs", ".go"],  "keywords": ["function", "class", "api", "module"]},
}

# ── Cluster Trigger Keywords ────────────────────────────────────────────────
CLUSTER_TRIGGERS = {
    "analyzer_planner":  ["analyze", "plan", "review", "investigate", "debug", "audit", "research", "strategize"],
    "architect":         ["architecture", "design", "spec", "build", "implement", "system", "structure", "scaffold"],
    "frontend_ui":       ["ui", "frontend", "component", "css", "html", "react", "vue", "svelte", "tailwind", "dashboard"],
    "backend_api":       ["api", "database", "auth", "endpoint", "migration", "server", "backend", "sql", "rest"],
    "devops_infra":      ["deploy", "ship", "ci/cd", "docker", "release", "infra", "k8s", "cloud", "terraform"],
    "quality_security":  ["test", "qa", "security", "verify", "tdd", "cve", "vulnerability", "scan", "audit", "fuzz"],
    "knowledge_wiki":    ["document", "wiki", "learn", "memory", "context", "knowledge", "graph", "faq"],
    "general":           ["general", "utility", "helper", "common", "shared"],
}

# ── Helpers ──────────────────────────────────────────────────────────────────
def connect_db():
    db_path = Path.home() / "nexus/db/nexus.db"
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.row_factory = sqlite3.Row
    return conn

def get_emb(text, cache_db=None):
    """Embed text via Ollama nomic-embed-text."""
    h = hashlib.sha256(text.encode()).hexdigest()
    if cache_db:
        row = cache_db.execute("SELECT embedding FROM embeddings_cache WHERE text_hash=?", (h,)).fetchone()
        if row:
            return row[0]
    try:
        resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:12000])
        emb = sqlite_vec.serialize_float32(resp['embedding'])
        if cache_db:
            cache_db.execute("INSERT OR IGNORE INTO embeddings_cache(text_hash,text_snippet,embedding) VALUES(?,?,?)",
                (h, text[:500], emb))
        return emb
    except Exception as e:
        print(f"  [EMBED ERR] {e}")
        return None

def detect_type(path, content):
    """Detect resource type from file path + content. Only return types valid in NEXUS DB."""
    name = path.name.lower()
    content_lower = content.lower()[:2000]
    VALID_TYPES = {'skill','agent','command','cli_tool','mcp','hook','topic','design_system','craft','doc','spec'}
    scores = {}
    for rtype, sig in SIGS.items():
        score = 0
        for f in sig.get("files", []):
            if f in name: score += 3
        for e in sig.get("exts", []):
            if name.endswith(e): score += 2
        for kw in sig.get("keywords", []):
            if kw.lower() in content_lower: score += 1
        scores[rtype] = score
    best = max(scores, key=scores.get)
    
    # Map to valid types
    if best == "config":
        return "spec"
    if best == "code":
        # Determine by content: shell script -> cli_tool, ts/py with mcp -> mcp
        if name.endswith('.sh'):
            return "cli_tool"
        if any(k in content_lower for k in ("mcp", "model context protocol", "resource")):
            return "mcp"
        if any(k in content_lower for k in ("skill", "orchestration", "workflow")):
            return "skill"
        if any(k in content_lower for k in ("agent", "swarm", "persona")):
            return "agent"
        if any(k in content_lower for k in ("hook", "context", "ide")):
            return "hook"
        return "spec"  # Default for code files
    if best not in VALID_TYPES:
        return "spec"
    return best if scores[best] > 0 else "spec"

def extract_description(path, content, rtype):
    """Extract a descriptive text from a file."""
    # Try docstring/JSDoc first
    desc = ""
    if rtype in ("skill", "doc"):
        # Markdown YAML frontmatter
        m = re.search(r'^---\s*\n(.*?\n)?title:\s*(.*?)(\n|$)', content, re.M)
        if m:
            desc = f"{m.group(2).strip()}: "
        # First H1/H2
        m = re.search(r'^#+\s+(.*?)$', content, re.M)
        if m:
            desc += m.group(1).strip()
        else:
            # First paragraph
            paras = [p.strip() for p in content.split('\n\n') if p.strip() and not p.startswith('#')]
            for p in paras[:2]:
                desc = p.strip().replace('\n', ' ')
                if len(desc) > 10: break
    elif path.suffix in ('.py', '.ts', '.js'):
        m = re.search(r'(?s)["\']{3}(.*?)["\']{3}', content)
        if m:
            doc = m.group(1).strip()[:200]
            desc = doc.replace('\n', ' ')
        if not desc:
            # JSDoc style
            m = re.search(r'(?s)/\*\*\s*(.*?)\*/', content)
            if m:
                desc = ' '.join(m.group(1).replace('*', '').split()[:30])
    if not desc:
        desc = f"{rtype} in {path.name} — {content[:100]}" if content.strip() else f"{rtype} in {path.name}"
    return desc[:MAX_DESC_LEN]

def detect_cluster(content):
    """Suggest primary cluster based on content keywords."""
    c_lower = content.lower()
    scores = {}
    for cluster, keywords in CLUSTER_TRIGGERS.items():
        scores[cluster] = sum(1 for kw in keywords if kw.lower() in c_lower)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"

def register_resource(conn, name, rtype, repo_name, file_path, description, body_text, tag=None):
    """Register a single resource in NEXUS DB."""
    cur = conn.cursor()
    # Skip if already exists
    existing = cur.execute(
        "SELECT id FROM resources WHERE name=? AND repo_name=?", (name, repo_name)).fetchone()
    if existing:
        return False, "exists"
    
    # Generate embedding
    emb_text = f"{name} {rtype} {description[:200]}"
    emb = get_emb(emb_text, conn)
    if emb is None:
        return False, "embed_failed"
    
    token_count = len(body_text) // 3
    cluster = detect_cluster(description + " " + body_text[:1000])
    repo_tag = tag or repo_name.split('/')[-1] if '/' in (repo_name or '') else (repo_name or DEFAULT_TAG)
    
    cur.execute("""
        INSERT INTO resources
        (name, resource_type, repo_name, file_path, description, body_text,
         embedding, token_count, status, tier, times_triggered, times_correct, times_wrong)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 2, 5, 0, 0)
    """, (name, rtype, repo_name, str(file_path), description[:MAX_DESC_LEN], body_text[:MAX_BODY_LEN], emb, token_count))
    resource_id = cur.lastrowid
    
    # Assign to cluster (insert into cluster_membership)
    cluster_map = {
        "analyzer_planner": 1, "architect": 2, "frontend_ui": 3,
        "backend_api": 4, "devops_infra": 5, "quality_security": 6,
        "knowledge_wiki": 7, "general": 8
    }
    cluster_id = cluster_map.get(cluster, 8)
    cur.execute("""
        INSERT INTO cluster_membership (cluster_name, resource_id, affinity)
        VALUES (?, ?, 0.7)
        ON CONFLICT(cluster_name, resource_id) DO UPDATE SET affinity=excluded.affinity
    """, (cluster, resource_id))
    
    return True, cluster

def scan_repo(repo_path, repo_name=None, tag=None, max_depth=5):
    """Yield interesting files: docs, plugins, skills, entry points, significant READMEs."""
    repo = Path(repo_path).expanduser().resolve()
    if not repo.exists():
        print(f"ERROR: repo path not found: {repo}")
        return
    
    repo_name = repo_name or repo.name
    tag = tag or repo.name
    
    # Only these extensions and meaningful files
    exts = {'.md', '.sh'}
    code_exts = {'.py', '.ts'}
    meaningful_files = ['README', 'SKILL', 'AGENTS', 'CLAUDE', 'package', 'tsconfig']
    skip_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build',
                 '.next', '.cache', 'coverage', 'tests', 'test', '.github', '.githooks',
                 'node_modules', 'pnpm-lock.yaml', 'package-lock.json'}
    
    # Phase 1: Top-level docs and key configs
    for fpath in sorted(repo.iterdir()):
        if fpath.is_file() and fpath.suffix in exts:
            try:
                content = fpath.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            if len(content) < 100:
                continue
            rtype = detect_type(fpath, content)
            yield fpath, fpath.name, content, rtype, repo_name, tag
    
    # Phase 2: Walk but limit depth, skip boring dirs, only keep significant files
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
        depth = len(Path(root).relative_to(repo).parts)
        if depth > max_depth:
            dirs[:] = []
            continue
        for fname in files:
            fpath = Path(root) / fname
            # Keep markdown, shell scripts
            keep = fpath.suffix.lower() in exts
            # And code files only in important subdirs
            if not keep and fpath.suffix.lower() in code_exts:
                keep = any(k in root.lower() for k in ['plugin', 'agent', 'skill', 'mcp', 'swarm'])
            if not keep:
                continue
            if fpath.stat().st_size < 200:
                continue
            if not (any(m in fname for m in meaningful_files) 
                    or fpath.suffix.lower() in exts
                    or 'plugin' in root.lower() or 'mcp' in root.lower()):
                continue
            try:
                content = fpath.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            if len(content) < 200:
                continue
            rel = str(fpath.relative_to(repo))
            rtype = detect_type(fpath, content)
            yield fpath, rel, content, rtype, repo_name, tag

# ── Main Entry ────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Ingest any repo into NEXUS")
    p.add_argument("repo_path", help="Path to repository to ingest")
    p.add_argument("--tag", default=None, help="Project tag for resources")
    p.add_argument("--name", default=None, help="Override repo name in DB")
    p.add_argument("--max-depth", type=int, default=5, help="Max directory recursion depth")
    p.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    p.add_argument("--batch", type=int, default=20, help="Commit batch size")
    args = p.parse_args()
    
    conn = connect_db()
    
    inserted = 0
    skipped = 0
    errors = 0
    clusters = {}
    batch_count = 0
    
    print(f"Scanning {args.repo_path} ...")
    for fpath, rel, content, rtype, repo_name, tag in scan_repo(args.repo_path, args.name, args.tag, args.max_depth):
        # Generate a unique resource name
        stem = Path(rel).stem
        name = f"{repo_name.replace('/', '-')}-{rtype}-{stem}"
        desc = extract_description(fpath, content, rtype)
        
        if args.dry_run:
            print(f"[DRY] {name:<50s} {rtype:<12s} {rel[:50]}")
            inserted += 1
            continue
        
        try:
            ok, info = register_resource(conn, name, rtype, repo_name, rel, desc, content, tag)
            if ok:
                inserted += 1
                batch_count += 1
                clusters[info] = clusters.get(info, 0) + 1
                if batch_count >= args.batch:
                    conn.commit()
                    print(f"  Committed batch of {batch_count}")
                    batch_count = 0
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            print(f"  [ERR] {name}: {e}")
    
    conn.commit()
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Ingestion Complete for {repo_name}")
    print(f"  Inserted:  {inserted}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print(f"  Clusters:")
    for c, n in sorted(clusters.items(), key=lambda x: -x[1]):
        print(f"    {c:20s} {n}")
    
    total = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    print(f"  TOTAL NEXUS RESOURCES: {total}")
    conn.close()

if __name__ == "__main__":
    main()
