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
