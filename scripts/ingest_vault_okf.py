#!/usr/bin/env python3
"""Ingest ~/wiki/vault/topics into NEXUS as OKF-structured resources.

For each markdown note:
  1. Upsert a resources row (repo_name=wiki-vault, cluster=knowledge_wiki)
  2. Run ingest_okf() → ingestion_chunks + chunks_fts
  3. Mirror chunks → resource_sections (section-level routing/search)
  4. Optionally compute 384-dim embeddings for vec routing

Usage:
    python3 scripts/ingest_vault_okf.py
    python3 scripts/ingest_vault_okf.py --limit 50 --no-embed
    python3 scripts/ingest_vault_okf.py --vault ~/wiki/vault/topics
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.ingest.okf_ingest import ingest_okf, _parse_frontmatter  # noqa: E402

DEFAULT_VAULT = Path.home() / "wiki" / "vault" / "topics"
DEFAULT_DB = ROOT / "db" / "nexus.db"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".obsidian"}

CLUSTER_SIGNALS = {
    "quality_security": ["security", "aidefence", "pii", "injection", "audit", "scan"],
    "architect": ["architecture", "adr", "ddd", "system-design", "load-balancer", "v3-"],
    "devops_infra": ["deploy", "docker", "kubernetes", "ci", "github", "kaari-deployment"],
    "frontend_ui": ["design-system", "ui", "mobile", "shop-page", "dashboard"],
    "analyzer_planner": ["review", "plan", "investigate", "debug", "orchestrat"],
    "backend_api": ["api", "backend", "supabase", "database", "migration"],
    "knowledge_wiki": ["wiki", "learn", "primer", "roadmap", "tutorial", "commands"],
}


def _guess_cluster(name: str, content: str) -> str:
    blob = f"{name} {content[:1500]}".lower()
    best, score = "knowledge_wiki", 0
    for cluster, keys in CLUSTER_SIGNALS.items():
        s = sum(blob.count(k) for k in keys)
        if s > score:
            score, best = s, cluster
    return best


def _resource_name(rel: Path, frontmatter: dict) -> str:
    if frontmatter.get("name"):
        return frontmatter["name"].strip()
    stem = str(rel.with_suffix("")).replace("/", "-").replace("\\", "-")
    return stem


def _resolve_description(frontmatter: dict, body: str) -> str:
    for key in ("description", "title"):
        val = (frontmatter.get(key) or "").strip()
        if val:
            return val[:500]
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:500]
    return ""


def _walk_notes(vault: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(vault.rglob("*.md")):
        if any(part in SKIP_DIRS or part.startswith(".") for part in path.parts):
            continue
        out.append(path)
    return out


def _serialize_embedding(vec) -> bytes:
    import sqlite_vec
    import numpy as np

    arr = np.asarray(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return sqlite_vec.serialize_float32(arr.tolist())


def _upsert_resource(
    con: sqlite3.Connection,
    *,
    name: str,
    file_path: str,
    description: str,
    resource_type: str,
    cluster: str,
    embedding: bytes | None,
) -> int:
    row = con.execute("SELECT id, file_path FROM resources WHERE name=?", (name,)).fetchone()
    if row:
        rid = row["id"]
        con.execute(
            """
            UPDATE resources SET file_path=?, description=?, repo_name='wiki-vault',
              resource_type=?, cluster=?, status='active',
              embedding=COALESCE(?, embedding), updated_at=datetime('now')
            WHERE id=?
            """,
            (file_path, description, resource_type, cluster, embedding, rid),
        )
        return rid

    cur = con.execute(
        """
        INSERT INTO resources
          (name, resource_type, repo_name, file_path, description, body_text,
           cluster, status, embedding, token_count)
        VALUES (?, ?, 'wiki-vault', ?, ?, '', ?, 'active', ?, 0)
        """,
        (name, resource_type, file_path, description, cluster, embedding),
    )
    return int(cur.lastrowid)


def _ensure_cluster(con: sqlite3.Connection, resource_id: int, cluster: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO cluster_membership (cluster_name, resource_id, affinity) "
        "VALUES (?, ?, 0.85)",
        (cluster, resource_id),
    )
    con.execute("UPDATE resources SET cluster=? WHERE id=?", (cluster, resource_id))


def _mirror_sections(con: sqlite3.Connection, resource_id: int, source_path: str) -> int:
    sid = con.execute(
        "SELECT id FROM ingestion_sources WHERE source_path=? AND source_type='okf'",
        (source_path,),
    ).fetchone()
    if not sid:
        return 0
    chunks = con.execute(
        "SELECT chunk_index, heading, content, token_count FROM ingestion_chunks "
        "WHERE source_id=? ORDER BY chunk_index",
        (sid["id"],),
    ).fetchall()
    con.execute("DELETE FROM resource_sections WHERE resource_id=?", (resource_id,))
    for ch in chunks:
        con.execute(
            "INSERT INTO resource_sections "
            "(resource_id, section_order, depth, heading, content, token_count) "
            "VALUES (?,?,0,?,?,?)",
            (resource_id, ch["chunk_index"], ch["heading"], ch["content"], ch["token_count"]),
        )
    return len(chunks)


def ingest_vault(
    vault: Path = DEFAULT_VAULT,
    db_path: Path = DEFAULT_DB,
    *,
    limit: int | None = None,
    embed: bool = True,
    force: bool = False,
) -> dict:
    if not vault.is_dir():
        raise FileNotFoundError(f"vault not found: {vault}")

    notes = _walk_notes(vault)
    if limit:
        notes = notes[:limit]

    encoder = None
    if embed:
        try:
            import os
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer

            encoder = SentenceTransformer(EMBED_MODEL, local_files_only=True)
        except Exception as exc:
            print(f"  [warn] embeddings disabled: {exc}")

    con = sqlite3.connect(str(db_path), timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=120000")

    ok = skip = fail = sections = 0
    t0 = time.perf_counter()

    for i, path in enumerate(notes, 1):
        rel = path.relative_to(vault)
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"  SKIP {rel}: {exc}")
            skip += 1
            continue

        fm, body = _parse_frontmatter(raw)
        name = _resource_name(rel, fm)
        abs_path = str(path.resolve())
        desc = _resolve_description(fm, body)
        rtype = (fm.get("type") or "topic").strip()
        if rtype not in {
            "skill", "agent", "command", "cli_tool", "mcp", "hook",
            "topic", "design_system", "craft", "doc", "spec",
        }:
            rtype = "topic"
        cluster = _guess_cluster(name, raw)

        existing = con.execute(
            "SELECT id FROM resources WHERE name=? AND file_path=? AND repo_name='wiki-vault'",
            (name, abs_path),
        ).fetchone()
        if existing and not force:
            n_sec = con.execute(
                "SELECT COUNT(*) FROM resource_sections WHERE resource_id=?",
                (existing["id"],),
            ).fetchone()[0]
            if n_sec > 0:
                skip += 1
                continue

        emb_blob = None
        if encoder is not None:
            text = f"{name} {desc} {body[:800]}"
            vec = encoder.encode([text])[0]
            emb_blob = _serialize_embedding(vec)

        try:
            rid = _upsert_resource(
                con,
                name=name,
                file_path=abs_path,
                description=desc,
                resource_type=rtype,
                cluster=cluster,
                embedding=emb_blob,
            )
            _ensure_cluster(con, rid, cluster)
            con.commit()
            con.close()
            # ingest_okf opens its own connection — must not hold ours
            ingest_okf(rid, abs_path, db_path=str(db_path))
            con = sqlite3.connect(str(db_path), timeout=120)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=120000")
            n = _mirror_sections(con, rid, abs_path)
            con.commit()
            ok += 1
            sections += n
            if i % 25 == 0 or i == len(notes):
                print(f"  [{i}/{len(notes)}] {name} → {n} sections", flush=True)
        except Exception as exc:
            try:
                con.rollback()
            except Exception:
                pass
            fail += 1
            print(f"  FAIL {rel}: {exc}")
            try:
                con.close()
            except Exception:
                pass
            con = sqlite3.connect(str(db_path), timeout=120)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=120000")

    vault_count = con.execute(
        "SELECT COUNT(*) FROM resources WHERE repo_name='wiki-vault'"
    ).fetchone()[0]
    sec_count = con.execute(
        """
        SELECT COUNT(*) FROM resource_sections rs
        JOIN resources r ON r.id = rs.resource_id
        WHERE r.repo_name='wiki-vault'
        """
    ).fetchone()[0]
    con.close()

    elapsed = round(time.perf_counter() - t0, 1)
    return {
        "vault": str(vault),
        "notes_scanned": len(notes),
        "ingested": ok,
        "skipped": skip,
        "failed": fail,
        "sections_written": sections,
        "wiki_vault_resources": vault_count,
        "wiki_vault_sections": sec_count,
        "elapsed_s": elapsed,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest wiki vault topics as OKF resources")
    ap.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--force", action="store_true", help="Re-ingest even if sections exist")
    args = ap.parse_args()

    print(f"Ingesting OKF from {args.vault} → {args.db}")
    stats = ingest_vault(
        args.vault,
        args.db,
        limit=args.limit,
        embed=not args.no_embed,
        force=args.force,
    )
    print("\nSummary:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()