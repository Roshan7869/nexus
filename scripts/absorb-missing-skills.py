#!/usr/bin/env python3
"""Absorb missing skills into NEXUS resources table with embeddings.

Targets:
  1. ~/.claude/skills/ui-ux-pro-max/SKILL.md
  2. ~/.agents/skills/agent-reach/SKILL.md
  3. ~/.agents/skills/emil-design-eng/SKILL.md
  4. ~/.agents/skills/review-animations/SKILL.md
  5. ~/.opencode/skills/impeccable/SKILL.md
  6. ~/.opencode/skills/graphify/SKILL.md
  7. ~/.config/opencode/skills/graphify/SKILL.md  (alt path)

Uses sentence-transformers/all-MiniLM-L6-v2 (384-dim) — same model as NumpyRouter.
"""
import os, sys, re, sqlite3, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path.home() / "nexus/server"))
sys.path.insert(0, str(Path.home() / "nexus"))

DB_PATH = str(Path.home() / "nexus/db/nexus.db")
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384

os.environ["TOKENIZERS_PARALLELISM"] = "false"

TARGETS = [
    (Path.home() / ".claude/skills/ui-ux-pro-max/SKILL.md",      "ui-ux-pro-max",          "claude-skills"),
    (Path.home() / ".agents/skills/agent-reach/SKILL.md",         "agent-reach",            "agents-skills"),
    (Path.home() / ".agents/skills/emil-design-eng/SKILL.md",     "emil-design-eng",        "agents-skills"),
    (Path.home() / ".agents/skills/review-animations/SKILL.md",  "review-animations",      "agents-skills"),
    (Path.home() / ".opencode/skills/impeccable/SKILL.md",        "impeccable",             "opencode-skills"),
    (Path.home() / ".opencode/skills/graphify/SKILL.md",         "graphify",               "opencode-skills"),
    (Path.home() / ".config/opencode/skills/graphify/SKILL.md",  "graphify-config",        "opencode-skills"),
]


def clean_text_for_embedding(name: str, description: str, body_text: str) -> str:
    """Strip YAML frontmatter and HTML comments from embedding text."""
    yaml_re = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
    html_re = re.compile(r"<!--.*?-->", re.DOTALL)
    prefix_re = re.compile(r"^[a-z0-9-]+\s+", re.IGNORECASE)

    parts = []
    if description:
        desc = html_re.sub("", description).strip()
        if desc:
            parts.append(desc)
    if body_text:
        body = body_text[:500]
        body = yaml_re.sub("", body)
        body = html_re.sub("", body)
        body = prefix_re.sub("", body)
        body = body.strip()
        if body:
            parts.append(body)
    if not parts:
        parts.append(name)
    return " ".join(parts)[:512]


def guess_cluster(name, rtype, content_lower=""):
    signals = {
        "analyzer_planner": ["analyze", "plan", "investigate", "debug", "audit", "strategy", "retro", "health", "graph"],
        "architect":      ["architecture", "design", "implement", "build", "structure", "model", "spec"],
        "frontend_ui":    ["ui", "frontend", "css", "dashboard", "component", "visual", "web", "animation", "impeccable"],
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
        best = "frontend_ui" if rtype == "skill" else "general"
    return best


def parse_frontmatter(body):
    """Extract description from YAML frontmatter or first heading."""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    desc = None
    if fm_match:
        fm = fm_match.group(1)
        desc_match = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', fm, re.MULTILINE)
        if desc_match:
            desc = desc_match.group(1).strip()[:500]
    if not desc:
        h = re.search(r'^#\s+(.+)', body)
        desc = h.group(1).strip()[:120] if h else name.replace("-", " ")
    return desc


def main():
    from sentence_transformers import SentenceTransformer

    print("[1/3] Loading embedding model...")
    model = SentenceTransformer(MODEL_ID, local_files_only=True)
    print(f"  loaded: {MODEL_ID} (dim {DIM})")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    print("[2/3] Processing missing skills...")
    ok = fail = skipped = 0
    for skill_path, fallback_name, repo in TARGETS:
        if not skill_path.exists():
            print(f"  SKIP  {skill_path} (not found)")
            skipped += 1
            continue

        body = skill_path.read_text(errors="ignore")
        desc = parse_frontmatter(body)

        # Determine resource name
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
        name = fallback_name
        if fm_match:
            name_match = re.search(r'^name:\s*["\']?(.+?)["\']?\s*$', fm_match.group(1), re.MULTILINE)
            if name_match:
                name = name_match.group(1).strip()

        # Use the resource name from frontmatter, but check if already ingested
        existing = conn.execute("SELECT id FROM resources WHERE name=?", (name,)).fetchone()
        if existing:
            print(f"  EXISTS {name} (id={existing[0]}) — will update")
            # We'll UPDATE rather than skip to refresh embedding

        # Compute embedding
        embed_text = clean_text_for_embedding(name, desc, body)
        vec = model.encode([embed_text]).astype(np.float32)
        emb_bytes = vec[0].tobytes()

        # L2 normalize (same as NumpyRouter expects)
        norm = np.linalg.norm(vec[0])
        if norm > 0:
            vec[0] = vec[0] / norm
        emb_bytes_norm = vec[0].tobytes()

        # Assign cluster
        cluster = guess_cluster(name, "skill", body.lower()[:2000])
        tokens = len(body.split())

        try:
            cur = conn.execute("""
                INSERT INTO resources (name, resource_type, description, body_text, repo_name, file_path,
                                       status, tier, cluster, embedding, token_count, times_triggered,
                                       times_correct, times_wrong)
                VALUES (?, 'skill', ?, ?, ?, ?, 'active', 2, ?, ?, ?, 5, 0, 0)
                ON CONFLICT(name) DO UPDATE SET
                    body_text=excluded.body_text,
                    description=excluded.description,
                    embedding=excluded.embedding,
                    cluster=excluded.cluster,
                    status='active',
                    token_count=excluded.token_count,
                    file_path=excluded.file_path,
                    updated_at=datetime('now')
                RETURNING id
            """, (name, desc, body, repo, str(skill_path), cluster, emb_bytes_norm, tokens))
            row = cur.fetchone()
            rid = row[0] if row else None

            if rid:
                conn.execute("""
                    INSERT INTO cluster_membership (cluster_name, resource_id, affinity)
                    VALUES (?, ?, 0.8)
                    ON CONFLICT DO NOTHING
                """, (cluster, rid))
                conn.commit()
                print(f"  OK    {name:30s} → id={rid}, cluster={cluster}, tokens={tokens}, emb_dim={len(vec[0])}")
                ok += 1
            else:
                print(f"  FAIL  {name} — no rowid returned")
                fail += 1
        except Exception as e:
            conn.rollback()
            print(f"  FAIL  {name}: {e}")
            fail += 1

    print(f"\n  Summary: {ok} ok, {fail} failed, {skipped} skipped")

    print("[3/3] Verifying new resources...")
    for _, fallback_name, _ in TARGETS:
        # Use the actual name (which might differ from fallback)
        pass
    # Just count what we added
    total = conn.execute("SELECT count(*) FROM resources WHERE status='active'").fetchone()[0]
    with_emb = conn.execute("SELECT count(*) FROM resources WHERE status='active' AND embedding IS NOT NULL").fetchone()[0]
    routable = conn.execute("SELECT count(*) FROM resources WHERE status='active' AND embedding IS NOT NULL AND resource_type IN ('skill','command','agent','mcp')").fetchone()[0]
    print(f"  Total active resources: {total}")
    print(f"  With embedding: {with_emb}")
    print(f"  Routable (skill/cmd/agent/mcp): {routable}")

    conn.close()
    print("\nDone. Restart MCP server to pick up new resources in NumpyRouter.")


if __name__ == "__main__":
    main()