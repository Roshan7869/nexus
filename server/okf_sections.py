"""Gap 5: resource_sections enrichment for cold / thin resources.

Two paths:
  1. File OKF — parse SKILL.md / markdown via ingest_okf
  2. Catalog synthesis — split description+body_text into sections when no file

Used by scripts/run_okf_residual.py and cold campaign.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

SECTION_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def resolve_skill_path(file_path: str | None, name: str = "", roots: list[Path] | None = None) -> str | None:
    if not file_path and not name:
        return None
    roots = roots or [
        Path.home(),
        Path.home() / "nexus",
        Path.home() / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".grok" / "skills",
        Path.home() / ".claude" / "plugins",
    ]
    candidates: list[Path] = []
    if file_path:
        p = Path(file_path)
        candidates.append(p)
        if not p.is_absolute():
            for base in roots:
                candidates.append(base / file_path)
                candidates.append(base / Path(file_path).name)
    if name:
        for base in roots:
            candidates.append(base / name / "SKILL.md")
            candidates.append(base / name)
            candidates.append(base / f"{name}.md")

    for cand in candidates:
        try:
            if cand.is_file() and cand.suffix.lower() in (".md", ".txt", ".rst", ".markdown"):
                return str(cand.resolve())
            if cand.is_dir() and (cand / "SKILL.md").is_file():
                return str((cand / "SKILL.md").resolve())
        except OSError:
            continue
    return None


def _split_body_sections(text: str) -> list[tuple[str | None, str]]:
    text = (text or "").strip()
    if not text:
        return []
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [(None, text)]
    out: list[tuple[str | None, str]] = []
    # preamble before first heading
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            out.append(("Overview", pre))
    for i, m in enumerate(matches):
        heading = m.group(0).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            out.append((heading, content))
    return out


def write_sections(
    conn: sqlite3.Connection,
    resource_id: int,
    sections: list[tuple[str | None, str]],
) -> int:
    if not sections:
        return 0
    conn.execute("DELETE FROM resource_sections WHERE resource_id=?", (resource_id,))
    n = 0
    for i, (heading, content) in enumerate(sections):
        tokens = max(1, len(content.split()))
        conn.execute(
            """
            INSERT INTO resource_sections
              (resource_id, section_order, depth, heading, content, token_count)
            VALUES (?,?,0,?,?,?)
            """,
            (resource_id, i, heading, content[:8000], tokens),
        )
        n += 1
    return n


def synthesize_from_catalog(
    conn: sqlite3.Connection,
    resource_id: int,
    *,
    name: str = "",
    description: str = "",
    body_text: str = "",
) -> int:
    """Build resource_sections from catalog text when no SKILL.md is available."""
    parts: list[str] = []
    if description and description.strip():
        parts.append(f"## Description\n\n{description.strip()}")
    if body_text and body_text.strip():
        # avoid duplicating description if body starts with it
        body = body_text.strip()
        if description and body.startswith(description.strip()[:80]):
            pass
        parts.append(body)
    if not parts and name:
        parts.append(f"## {name}\n\nCatalog resource without file body.")
    blob = "\n\n".join(parts)
    sections = _split_body_sections(blob)
    if not sections:
        return 0
    return write_sections(conn, resource_id, sections)


def backfill_cold_sections(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Enrich active cold resources missing resource_sections."""
    from server.ingest.okf_ingest import ingest_okf

    rows = conn.execute(
        """
        SELECT r.id, r.name, r.file_path, r.description, r.body_text, r.resource_type
        FROM resources r
        WHERE r.status='active' AND COALESCE(r.times_triggered,0)=0
          AND NOT EXISTS (
            SELECT 1 FROM resource_sections rs WHERE rs.resource_id=r.id
          )
        ORDER BY COALESCE(length(r.body_text),0) DESC
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]

    file_ok = synth_ok = fail = skip = sections = 0
    for r in rows:
        rid = r["id"]
        resolved = resolve_skill_path(r["file_path"], r["name"] or "")
        try:
            if resolved and db_path:
                try:
                    ingest_okf(rid, resolved, db_path=db_path)
                    # Prefer OKF chunks if present, else fall through to synth
                    n_sec = conn.execute(
                        "SELECT COUNT(*) FROM resource_sections WHERE resource_id=?",
                        (rid,),
                    ).fetchone()[0]
                    if n_sec:
                        # okf_ingest may not write resource_sections — copy from chunks
                        pass
                except Exception:
                    resolved = None

            # After file ingest, ensure resource_sections exist
            n_sec = conn.execute(
                "SELECT COUNT(*) FROM resource_sections WHERE resource_id=?",
                (rid,),
            ).fetchone()[0]
            if n_sec:
                file_ok += 1
                sections += n_sec
                continue

            if resolved:
                # Pull from ingestion_chunks linked by source path
                sid = conn.execute(
                    "SELECT id FROM ingestion_sources WHERE source_path=? AND source_type='okf'",
                    (resolved,),
                ).fetchone()
                if sid:
                    chunks = conn.execute(
                        "SELECT chunk_index, heading, content, token_count "
                        "FROM ingestion_chunks WHERE source_id=? ORDER BY chunk_index",
                        (sid["id"],),
                    ).fetchall()
                    if chunks:
                        conn.execute(
                            "DELETE FROM resource_sections WHERE resource_id=?", (rid,)
                        )
                        for ch in chunks:
                            conn.execute(
                                "INSERT INTO resource_sections"
                                "(resource_id, section_order, depth, heading, content, token_count) "
                                "VALUES (?,?,0,?,?,?)",
                                (
                                    rid,
                                    ch["chunk_index"],
                                    ch["heading"],
                                    ch["content"],
                                    ch["token_count"],
                                ),
                            )
                        sections += len(chunks)
                        file_ok += 1
                        conn.commit()
                        continue

            n = synthesize_from_catalog(
                conn,
                rid,
                name=r["name"] or "",
                description=r["description"] or "",
                body_text=r["body_text"] or "",
            )
            if n:
                synth_ok += 1
                sections += n
                conn.commit()
            else:
                skip += 1
        except Exception:
            fail += 1
            conn.rollback()

    remaining = conn.execute(
        """
        SELECT COUNT(*) FROM resources r
        WHERE r.status='active' AND COALESCE(r.times_triggered,0)=0
          AND NOT EXISTS (SELECT 1 FROM resource_sections rs WHERE rs.resource_id=r.id)
        """
    ).fetchone()[0]
    return {
        "candidates": len(rows),
        "file_okf": file_ok,
        "synthesized": synth_ok,
        "failed": fail,
        "skipped": skip,
        "sections_written": sections,
        "cold_without_sections_remaining": remaining,
        "okf_residual_gate_pass": remaining <= 20,
    }


def backfill_section_embeddings(
    conn: sqlite3.Connection,
    batch_size: int = 256,
    report: Any = None,
) -> dict[str, int]:
    """Populate section-grain embeddings (decision D1 vec arm, gap 5 follow-on).

    Encodes every ``resource_sections.content`` lacking a row in
    ``section_embeddings`` and writes both stores:

    * ``section_embeddings`` — float32[384] blob per section (cosine rerank)
    * ``vec_sections_content`` — vec0 bit[384] row per section (Hamming scan,
      created/repaired by :class:`router_v2.chunk_index.SectionIndex`)

    Encoding is text-hash cached in the shared ``chunk_embed_cache``, so text
    already embedded as a chunk is reused — only genuinely new text hits the
    model. Re-running is a no-op once coverage is complete.

    Args:
        conn: open connection to nexus.db (committed by caller afterwards;
            encode_batch may commit the shared embed cache on this conn).
        batch_size: sections per model encode call.
        report: optional callable ``(phase: str, done: int, total: int)``.

    Returns: ``{candidates, embedded, skipped_empty}``.
    """
    import sys
    from pathlib import Path

    _server = Path(__file__).resolve().parent
    if str(_server) not in sys.path:
        sys.path.insert(0, str(_server))
    from router_v2.chunk_embedder import ChunkEmbedder
    from router_v2.chunk_index import SectionIndex

    # SectionIndex first: its __init__ lazily creates section_embeddings and
    # repairs the legacy plain vec_sections_content into a vec0 virtual table.
    db = str(_default_db(conn))
    embedder = ChunkEmbedder(db)
    with SectionIndex(db, embedder=embedder) as idx:
        # Missing in EITHER store counts as pending: a partial crash can leave
        # floats without vec rows (or vice versa) — the re-run repairs both.
        # Each membership set is read on a connection that can see its table
        # (vec_sections_content is vec0 — only readable where the extension
        # is loaded, i.e. on idx.con, not the caller's conn).
        have_floats = {
            int(r[0]) for r in conn.execute(
                "SELECT section_id FROM section_embeddings"
            ).fetchall()
        }
        have_vec = {
            int(r[0]) for r in idx.con.execute(
                "SELECT rowid FROM vec_sections_content"
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT id, content FROM resource_sections"
        ).fetchall()
        todo = [
            (int(r[0]), (r[1] or "")) for r in rows
            if int(r[0]) not in have_floats or int(r[0]) not in have_vec
        ]
        skipped_empty = sum(1 for _, txt in todo if not txt.strip())
        todo = [(sid, txt) for sid, txt in todo if txt.strip()]
        total = len(todo)
        if report:
            report("start", 0, total)

        if not total:
            return {"candidates": 0, "embedded": 0, "skipped_empty": skipped_empty}

        embedded = 0
        for start in range(0, total, batch_size):
            batch = todo[start:start + batch_size]
            vecs = embedder.encode_batch([txt for _, txt in batch], con=conn)
            idx.store_floats(
                [(sid, txt, vecs[i]) for i, (sid, txt) in enumerate(batch)]
            )
            idx.add_many(
                [(sid, vecs[i]) for i, (sid, _) in enumerate(batch)]
            )
            embedded += len(batch)
            if report:
                report("embed", embedded, total)
    return {"candidates": total, "embedded": embedded, "skipped_empty": skipped_empty}


def _default_db(conn: sqlite3.Connection) -> Path:
    """Path of the database ``conn`` is attached to (fallback: repo db)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if row and row[2]:
        return Path(row[2])
    return Path(__file__).resolve().parent.parent / "db" / "nexus.db"
