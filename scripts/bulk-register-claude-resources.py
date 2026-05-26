#!/usr/bin/env python3
"""
bulk-register-claude-resources.py — Fast bulk ingestion of Claude/Hermes resources

Scans and registers into NEXUS (no embeddings computed here — inserted NULL,
backfilled later by maintenance or on demand):
  - ~/.claude/skills/*        → skills
  - ~/.claude/commands/*      → commands  
  - ~/.claude/CLAUDE.md       → doc (global context)
  - ~/.aider-orchestrator/*    → doc
  - ~/.hermes/skills/*        → skills
  - ai-swarm-workspace/       → project context
  - career-ops/               → project context

Usage:
    cd ~/nexus && venv/bin/python scripts/bulk-register-claude-resources.py
"""
import os, sys, sqlite3, re
from pathlib import Path

DB = Path.home() / "nexus/db/nexus.db"
conn = sqlite3.connect(str(DB), timeout=60)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def guess_cluster(name, rtype, content_lower=""):
    signals = {
        "analyzer_planner": ["analyze", "plan", "investigate", "debug", "audit", "strategy", "retro", "health"],
        "architect":      ["architecture", "design", "implement", "build", "structure", "model", "spec"],
        "frontend_ui":    ["ui", "frontend", "css", "dashboard", "component", "visual", "web"],
        "backend_api":    ["api", "database", "auth", "endpoint", "migration", "backend", "infra"],
        "devops_infra":   ["deploy", "ship", "ci/cd", "docker", "release", "infra", "test"],
        "quality_security":["test", "qa", "security", "verify", "tdd", "review", "scan", "bottleneck"],
        "knowledge_wiki": ["doc", "wiki", "learn", "memory", "context", "note", "remember", "tutorial"],
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
    if best_score == 0:
        mapping = {"command":"general","spec":"architect","design_system":"frontend_ui",
                   "craft":"frontend_ui","doc":"knowledge_wiki"}
        best = mapping.get(rtype, "general")
    return best

def register(name, rtype, desc, body, repo, fpath, cluster=None):
    cluster = cluster or guess_cluster(name, rtype, body.lower()[:2000])
    tokens = len(body.split())
    try:
        cur.execute("""
            INSERT INTO resources (name, resource_type, description, body_text, repo_name, file_path,
                                   status, tier, cluster, embedding, token_count, times_triggered, times_correct, times_wrong)
            VALUES (?, ?, ?, ?, ?, ?, 'active', 2, ?, NULL, ?, 5, 0, 0)
            ON CONFLICT(name) DO UPDATE SET
                body_text=excluded.body_text,
                description=excluded.description,
                resource_type=excluded.resource_type,
                repo_name=excluded.repo_name,
                file_path=excluded.file_path,
                cluster=excluded.cluster,
                status='active',
                token_count=excluded.token_count,
                tier=2,
                times_triggered=5,
                updated_at=datetime('now')
            RETURNING id
        """, (name, rtype, desc, body, repo, fpath, cluster, tokens))
        rid = cur.fetchone()[0]
        
        # cluster_membership
        cur.execute("""
            INSERT INTO cluster_membership (cluster_name, resource_id, affinity)
            VALUES (?, ?, 0.7)
            ON CONFLICT DO NOTHING
        """, (cluster, rid))
        conn.commit()
        return True, rid
    except Exception as e:
        conn.rollback()
        return False, str(e)

# ── Scanners ─────────────────────────────────────────────────────────────────

def scan_claude_skills():
    base = Path.home() / ".claude/skills"
    if not base.exists(): return
    for d in sorted(base.iterdir()):
        if not d.is_dir(): continue
        skill_md = d / "SKILL.md"
        if skill_md.exists():
            body = skill_md.read_text(errors='ignore')
            desc = (re.search(r'^#\s+(.+)', body) or re.search(r'^##\s+(.+)', body))
            desc = desc.group(1).strip()[:120] if desc else d.name.replace('-',' ')
            yield f"claude-skill-{d.name}", "skill", desc, body, "claude-skills", str(skill_md)
        for sub in d.glob('*.md'):
            if sub.name in ("SKILL.md","README.md"):
                continue  # Already handled or generic
            sbody = sub.read_text(errors='ignore')
            sdesc = re.search(r'^#\s+(.+)', sbody)
            sdesc = sdesc.group(1).strip()[:120] if sdesc else sub.stem.replace('-',' ')
            suffix = f"-{sub.stem}" if sub.stem != d.name else ""
            yield f"claude-skill-{d.name}{suffix}", "doc", sdesc, sbody, "claude-skills", str(sub)

def scan_claude_commands():
    base = Path.home() / ".claude/commands"
    if not base.exists(): return
    seen_readme = set()
    for f in sorted(base.rglob('*.md')):
        rel = f.relative_to(base)
        cat = rel.parts[0]
        if len(rel.parts) < 1: continue
        body = f.read_text(errors='ignore')
        desc = re.search(r'^#\s+(.+)', body)
        desc = desc.group(1).strip()[:120] if desc else f.stem.replace('-',' ')
        if f.name == "README.md":
            repo = f"claude-cmd-{cat}"
            if repo in seen_readme: continue
            seen_readme.add(repo)
            yield repo, "doc", desc, body, "claude-commands", str(f)
        else:
            yield f"claude-cmd-{cat}-{f.stem}", "command", desc, body, "claude-commands", str(f)

def scan_global_docs():
    """Top-level context files"""
    items = [
        (Path.home()/".claude/CLAUDE.md", "claude-global-context", "doc"),
        (Path.home()/".aider-orchestrator/README.md", "aider-orchestrator", "doc"),
        (Path.home()/".aider-orchestrator" / "aliases.sh", "aider-orchestrator-aliases", "cli_tool"),
        (Path.home()/"CLAUDE.md", "roshan-global-claude-context", "doc"),
        (Path.home()/"ai-swarm-workspace/CLAUDE.md", "ai-swarm-workspace-context", "doc"),
        (Path.home()/"ai-swarm-workspace/README.md", "ai-swarm-workspace-readme", "doc"),
        (Path.home()/"career-ops/CLAUDE.md", "career-ops-context", "doc"),
        (Path.home()/"career-ops/AGENTS.md", "career-ops-agents", "agent"),
    ]
    for fpath, name, rtype in items:
        if fpath.exists():
            body = fpath.read_text(errors='ignore')
            desc = re.search(r'^#\s+(.+)', body)
            desc = desc.group(1).strip()[:120] if desc else name.replace('-',' ')
            yield name, rtype, desc, body, "global-context", str(fpath)

# Also scan Hermes skills (non-built-in ones in .hermes/skills)
def scan_hermes_skills():
    base = Path.home() / ".hermes/skills"
    if not base.exists(): return
    for d in sorted(base.iterdir()):
        if not d.is_dir(): continue
        # Look for SKILL.md, DESCRIPTION.md, or sub-SKILL.md inside subdirs
        for md_file in chain_single(d.glob('SKILL.md'), d.rglob('SKILL.md')):
            if md_file.parent != d:  # It's a deep SKILL.md
                name = f"hermes-skill-{d.name}-{md_file.parent.name}"
            else:
                name = f"hermes-skill-{d.name}"
            body = md_file.read_text(errors='ignore')
            desc = re.search(r'^#\s+(.+)', body)
            desc = desc.group(1).strip()[:120] if desc else name
            yield name, "skill", desc, body, "hermes-skills", str(md_file)

def chain_single(*iterables):
    for it in iterables:
        yield from it

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    scanners = [
        scan_claude_skills(),
        scan_claude_commands(),
        scan_global_docs(),
        scan_hermes_skills(),
    ]
    total = ok = fail = 0
    for scanner in scanners:
        for name, rtype, desc, body, repo, fpath in scanner:
            total += 1
            success, payload = register(name, rtype, desc, body, repo, fpath)
            if success:
                ok += 1
            else:
                fail += 1
                print(f"  ERROR {name}: {payload[:80]}")
            if total % 20 == 0:
                print(f"  Progress: {ok}/{total} OK, {fail} failed")
    
    print(f"\n{'='*50}")
    print(f"BULK INGESTION: {ok}/{total} registered, {fail} failed")
    print(f"{'='*50}")
    
    # Summary by repo
    print("\n--- By repo ---")
    for repo, count in cur.execute("SELECT repo_name, COUNT(*) AS cnt FROM resources WHERE times_triggered=5 GROUP BY repo_name ORDER BY cnt DESC").fetchall():
        print(f"  {repo:30s}: {count}")
    
    print("\n--- New total resources in NEXUS ---")
    print(f"  {cur.execute('SELECT COUNT(*) FROM resources').fetchone()[0]} total")
    conn.close()

if __name__ == "__main__":
    main()
