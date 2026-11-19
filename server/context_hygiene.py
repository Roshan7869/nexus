"""Context hygiene — S1/S2 pack-time filters (DISTRACTION-RESISTANT-CONTEXT.md).

Everything here is deterministic, CPU-only, and fail-open: a hygiene failure must
never block a route, it merely passes the pack through unfiltered (the pre-change
behavior) with a logged warning.

Pack-time rules (S1):
  - rows with invalidated_at set (upstream-deleted / curation-invalidated) never pack
  - rows with an unresolved hygiene_flags row never pack (stale markers, stubs, ...)
  - user_learnings notes with a closed note_ledger interval never pack

Also provides:
  - HYGIENE_SQL: injectable WHERE fragment so existing queries filter at SQL level
  - reopen_orphaned_invalidations(): the post-pull consistency check (design §3 S1
    Recovery): an 'upstream_change' invalidation whose (repo, file_path) has no
    valid replacement row is re-opened and audited as 'suspect_reopen'.
    Tombstones ('upstream_delete') are exempt — they are computed from the
    discovery set itself and are correct without a replacement by construction.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# S2 flag semantics: pack-blocking vs tracked-only. A flag proves nothing by
# itself — 'stale_marker_pending' is suspicion (budget exhausted, offline,
# unresolvable ref) and must NEVER hide a resource; the pack-blocking types
# are backed by confirmed evidence (tracker-closed, title-only body, aged
# date-claim).
PACK_BLOCKING = ("stale_marker", "stub", "unresolved_claim")
PENDING_FLAG = "stale_marker_pending"

HYGIENE_SQL = (
    "r.invalidated_at IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM hygiene_flags hf "
    "                WHERE hf.resource_id = r.id AND hf.resolved_at IS NULL "
    f"                  AND hf.flag IN ({','.join(repr(f) for f in PACK_BLOCKING)}))"
)

NOTE_HYGIENE_SQL = (
    "NOT EXISTS (SELECT 1 FROM note_ledger nl "
    "            WHERE nl.note_id = u.id AND nl.invalidated_at IS NOT NULL)"
)


def drop_superseded(rows: list[dict]) -> list[dict]:
    """Post-filter for already-fetched resource dicts: drops invalidated rows.

    Defense in depth for code paths that build packs from rows fetched elsewhere.
    Missing keys are treated as unknown-but-pass (fail-open).
    """
    try:
        return [r for r in rows if not r.get("invalidated_at")]
    except Exception:
        logger.warning("context_hygiene: drop_superseded failed; passing through")
        return rows


def drop_flagged(con: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Drop rows with unresolved hygiene flags; fail-open on any error."""
    try:
        flagged = {
            int(f[0]) for f in con.execute(
                "SELECT resource_id FROM hygiene_flags WHERE resolved_at IS NULL"
            )
        }
        return [r for r in rows if int(r.get("id", -1)) not in flagged]
    except Exception:
        logger.warning("context_hygiene: drop_flagged failed; passing through")
        return rows


def flag_resource(con: sqlite3.Connection, resource_id: int, flag: str,
                  detail: str = "") -> None:
    """Add an unresolved hygiene flag (idempotent per (resource_id, flag))."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "INSERT INTO hygiene_flags(resource_id, flag, detail, found_at) "
        "VALUES(?,?,?,?) ON CONFLICT(resource_id, flag) "
        "DO UPDATE SET detail=excluded.detail, resolved_at=NULL",
        (resource_id, flag, detail, now),
    )


def resolve_flag(con: sqlite3.Connection, resource_id: int, flag: str) -> None:
    con.execute(
        "UPDATE hygiene_flags SET resolved_at=? "
        "WHERE resource_id=? AND flag=?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
         resource_id, flag),
    )


def invalidate_resource(con: sqlite3.Connection, resource_id: int,
                        reason: str) -> None:
    """Curation-path invalidation: close validity interval + audit row."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "UPDATE resources SET invalidated_at=? WHERE id=?", (now, resource_id))
    con.execute(
        "INSERT INTO invalidation_audit(resource_id, reason, invalidated_at) "
        "VALUES(?,?,?)", (resource_id, reason, now),
    )


# ── S4 provenance stamps ────────────────────────────────────────────────

def provenance_stamp(repo_name: str | None, commit: str | None,
                     absorbed_date: str | None) -> str | None:
    """Honest per-passage stamp: 'repo @shortSHA · absorbed YYYY-MM-DD'.

    Never invents a signal: NULL commit -> no SHA shown; NULL date -> no stamp
    (a stamp we cannot date is worse than none). No 'verified' claim — no
    stage verifies body correctness (design §3 S4, goldfish finding 5).
    """
    repo = (repo_name or "").strip()
    sha = (commit or "").strip()[:8]
    date = (absorbed_date or "").strip()[:10]
    if not date:
        return None
    bits = []
    if repo:
        bits.append(f"{repo} @{sha}" if sha else repo)
    elif sha:
        bits.append(f"@{sha}")
    return " · ".join(bits + [f"absorbed {date}"])


def attach_provenance(con: sqlite3.Connection, entries: list[dict],
                      name_key: str = "name") -> None:
    """Attach an honest 'provenance' stamp to each entry, in place.

    One batched name lookup (resources.name is UNIQUE-indexed); entries keep
    any existing provenance. Missing names get no field (fail-open).
    ~1-3 ms for 10 names — inside the S4 pack budget.
    """
    if not entries:
        return
    try:
        names = [e.get(name_key) for e in entries if e.get(name_key)]
        if not names:
            return
        ph = ",".join("?" * len(names))
        rows = con.execute(
            f"SELECT name, repo_name, absorbed_at_commit, absorbed_at_date "
            f"FROM resources WHERE name IN ({ph})", names,
        ).fetchall()
        stamps = {
            r[0]: provenance_stamp(r[1], r[2], r[3]) for r in rows
        }
        for e in entries:
            n = e.get(name_key)
            if n and n in stamps and "provenance" not in e:
                stamp = stamps[n]
                if stamp:
                    e["provenance"] = stamp
    except Exception:
        logger.warning("context_hygiene: attach_provenance failed; unstamped")


def drop_pack_blocked(con: sqlite3.Connection, entries: list[dict],
                      name_key: str = "name") -> list[dict]:
    """Drop entries carrying unresolved PACK-BLOCKING flags from a routed list.

    Tracked-only flags (stale_marker_pending) keep packing — suspicion never
    hides a resource. Fail-open on any error.
    """
    if not entries:
        return entries
    try:
        names = [e.get(name_key) for e in entries if e.get(name_key)]
        if not names:
            return entries
        ph = ",".join("?" * len(names))
        rows = con.execute(
            f"SELECT r.name FROM resources r JOIN hygiene_flags hf "
            f"ON hf.resource_id = r.id "
            f"WHERE r.name IN ({ph}) AND hf.resolved_at IS NULL "
            f"AND hf.flag IN ({','.join(repr(f) for f in PACK_BLOCKING)})",
            names,
        ).fetchall()
        blocked = {row[0] for row in rows}
        return [e for e in entries if e.get(name_key) not in blocked]
    except Exception:
        logger.warning("context_hygiene: drop_pack_blocked failed; passing through")
        return entries


def reopen_orphaned_invalidations(db_path: str | Path) -> dict:
    """Post-pull consistency check (design §3 S1 Recovery).

    An 'upstream_change'-reason invalidation whose (repo_name, file_path) has no
    active replacement row means the replacement write died mid-pull — re-open the
    row and audit it as 'suspect_reopen' rather than leaving silently deleted
    knowledge. Returns a summary dict.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        orphans = con.execute(
            """
            SELECT a.id AS audit_id, r.id AS resource_id, r.repo_name, r.file_path
            FROM invalidation_audit a
            JOIN resources r ON r.id = a.resource_id
            WHERE a.reason = 'upstream_change'
              AND a.reopened_at IS NULL
              AND r.invalidated_at IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1 FROM resources r2
                    WHERE r2.repo_name = r.repo_name
                      AND r2.file_path = r.file_path
                      AND r2.id != r.id
                      AND r2.invalidated_at IS NULL
                  )
            """
        ).fetchall()
        reopened = 0
        for o in orphans:
            con.execute(
                "UPDATE resources SET invalidated_at=NULL WHERE id=?",
                (o["resource_id"],))
            con.execute(
                "UPDATE invalidation_audit SET reopened_at=? WHERE id=?",
                (now, o["audit_id"]))
            con.execute(
                "INSERT INTO invalidation_audit(resource_id, reason, invalidated_at) "
                "VALUES(?, 'suspect_reopen', ?)",
                (o["resource_id"], now))
            reopened += 1
        con.commit()
        return {"orphaned": len(orphans), "reopened": reopened}
    finally:
        con.close()
