"""S2 — stale-marker linter at ingestion (DISTRACTION-RESISTANT-CONTEXT.md §3).

Scans active resource bodies for claims whose upstream truth can be checked:

  - GitHub issue URLs (full owner/repo refs)  -> resolved via tracker API
  - ``TODO/FIXME(...)`` with a tracker ref    -> resolved when the ref names a
    local clone remote; bare ``b/N`` (Google buganizer) is UNRESOLVABLE by
    design -> pending, never pack-excluded on suspicion alone
  - ``as of <date>`` claims                   -> older than --asof-days
    (default 180) => 'unresolved_claim' (pack-excluded, curation-reviewable)
  - plain ``TODO``/``FIXME`` (no ref)         -> tracked as pending only
  - stub bodies (< --stub-chars, default 40) -> 'stub' (pack-excluded; a
    title-only row has no content to ground on)

Flag semantics (pack filter is flag-type-aware):
  'stale_marker'         confirmed stale (tracker says closed) -> pack-excluded
  'stub'                 title-only row                        -> pack-excluded
  'unresolved_claim'     old date claim                       -> pack-excluded
  'stale_marker_pending' unproven (budget/offline/no-ref)      -> tracked only,
                                                          NEVER pack-excluded
Honesty rules: unproven is not stale; confirmed-stale rows are flagged for
curation review, never auto-deleted (trace-verify-delete; Google's >95%
acceptance pattern). Tracker lookups go through a per-run budget with
exponential backoff and a persistent cache so repeated pulls are free.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from server.context_hygiene import flag_resource, resolve_flag

PACK_BLOCKING = ("stale_marker", "stub", "unresolved_claim")
PENDING = "stale_marker_pending"

ISSUE_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d{1,7})")
TODO_REF = re.compile(
    r"\b(TODO|FIXME|XXX|HACK)\b[^.\n]{0,40}?\(?\s*(?:b/|#|issues?/|issue\s+)(\d{1,7})\)?",
    re.I,
)
PLAIN_TODO = re.compile(r"^\s*(?:[-*]\s*)?(TODO|FIXME)\b", re.M | re.I)
ASOF = re.compile(
    r"\bas of (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})"
    r"|\bas of (\d{4}-\d{2}-\d{2})", re.I,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"))}


def _ensure_cache(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS marker_cache ("
        "    ref TEXT PRIMARY KEY,"
        "    state TEXT NOT NULL,"
        "    checked_at TEXT NOT NULL"
        ")"
    )


def _remote_url(repo_dir: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def _remote_to_owner_repo(url: str) -> tuple[str, str] | None:
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$", url)
    return (m.group(1), m.group(2)) if m else None


@dataclass
class GhResolver:
    """Tracker resolution via the authed gh CLI, budgeted and backoff-safe."""

    budget: int = 200
    calls: int = 0
    failures: list[str] = field(default_factory=list)

    def resolve_issue(self, owner: str, repo: str, number: str) -> str | None:
        """'open' | 'closed' | 'not_found' | None (transient failure only)."""
        if self.calls >= self.budget:
            return None
        self.calls += 1
        for attempt in range(3):
            try:
                out = subprocess.run(
                    ["gh", "api", f"repos/{owner}/{repo}/issues/{number}",
                     "--jq", ".state"],
                    capture_output=True, text=True, timeout=30,
                )
                if out.returncode == 0:
                    state = out.stdout.strip().lower()
                    return state if state in ("open", "closed") else None
                if "Not Found" in out.stderr:
                    # placeholder/typo refs (e.g. 'owner/repo#42' example text):
                    # permanently unknown — cache so weekly runs don't re-call
                    return "not_found"
                return None
            except Exception:
                time.sleep(min(2 ** attempt, 8))
        self.failures.append(f"{owner}/{repo}#{number}")
        return None


def scan_resources(
    con: sqlite3.Connection,
    resolver: GhResolver | None = None,
    *,
    asof_days: int = 180,
    stub_chars: int = 40,
    repo_dirs: dict[str, str] | None = None,
) -> dict:
    """Scan all active rows; write/resolve hygiene flags. Never raises."""
    _ensure_cache(con)
    resolver = resolver or GhResolver()
    repo_dirs = repo_dirs or {}
    summary = {
        "scanned": 0, "issue_urls": 0, "todo_refs": 0, "plain_todos": 0,
        "asof_old": 0, "stubs": 0, "resolved_open": 0, "resolved_closed": 0,
        "pending": 0, "tracker_calls": 0, "cache_hits": 0,
    }
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    today = date.today()

    rows = con.execute(
        "SELECT id, name, repo_name, body_text, description FROM resources "
        "WHERE status='active' AND invalidated_at IS NULL"
    ).fetchall()
    for r in rows:
        summary["scanned"] += 1
        text = f"{r['body_text'] or ''}\n{r['description'] or ''}"

        # ── GitHub issue URLs: full refs, resolvable ──────────────────────
        for m in ISSUE_URL.finditer(text):
            summary["issue_urls"] += 1
            ref = f"{m.group(1)}/{m.group(2)}#{m.group(3)}"
            state = _cached_state(con, ref)
            if state is None:
                state = resolver.resolve_issue(m.group(1), m.group(2), m.group(3))
                if state:
                    con.execute(
                        "INSERT OR REPLACE INTO marker_cache(ref, state, checked_at) "
                        "VALUES(?,?,?)", (ref, state, now))
                    summary["tracker_calls"] = resolver.calls
                    if state == "not_found":
                        summary["pending"] += 1
                        flag_resource(con, r["id"], PENDING,
                                      f"{ref}: placeholder/unresolvable ref")
                        continue
                else:
                    summary["pending"] += 1
                    flag_resource(con, r["id"], PENDING,
                                  f"{ref}: unknown (offline/budget)")
                    continue
            else:
                summary["cache_hits"] += 1
                if state == "not_found":
                    summary["pending"] += 1
                    flag_resource(con, r["id"], PENDING,
                                  f"{ref}: placeholder/unresolvable ref")
                    continue
            _apply_issue_state(con, r["id"], ref, state, summary)

        # ── TODO/FIXME with a tracker ref ─────────────────────────────────
        for m in TODO_REF.finditer(text):
            summary["todo_refs"] += 1
            kind, num = m.group(1).upper(), m.group(2)
            if kind in ("TODO", "FIXME") and m.group(0).lower().find("b/") >= 0:
                # buganizer: not a public tracker — unresolvable by design
                summary["pending"] += 1
                flag_resource(con, r["id"], PENDING, f"b/{num}: buganizer ref, "
                              "unresolvable by design")
                continue
            # '#N' style: resolve against the local clone's origin remote
            root = repo_dirs.get(r["repo_name"])
            if not root:
                summary["pending"] += 1
                flag_resource(con, r["id"], PENDING,
                              f"#{num}: no local clone to resolve against")
                continue
            orp = _remote_to_owner_repo(_remote_url(root) or "")
            if not orp:
                summary["pending"] += 1
                flag_resource(con, r["id"], PENDING, f"#{num}: no github remote")
                continue
            owner, grepo = orp
            ref = f"{owner}/{grepo}#{num}"
            state = _cached_state(con, ref)
            if state is None:
                state = resolver.resolve_issue(owner, grepo, num)
                if state:
                    con.execute(
                        "INSERT OR REPLACE INTO marker_cache(ref, state, checked_at) "
                        "VALUES(?,?,?)", (ref, state, now))
                    summary["tracker_calls"] = resolver.calls
                else:
                    summary["pending"] += 1
                    flag_resource(con, r["id"], PENDING, f"{ref}: unknown")
                    continue
            _apply_issue_state(con, r["id"], ref, state, summary)

        # ── plain TODO/FIXME: tracked only, never pack-excluded ──────────
        if PLAIN_TODO.search(text):
            summary["plain_todos"] += 1
            flag_resource(con, r["id"], PENDING,
                          "plain TODO/FIXME marker (no tracker ref)")

        # ── old 'as of <date>' claims: pack-excluded, curation-reviewable ─
        for m in ASOF.finditer(text):
            if m.group(3):
                d = date.fromisoformat(m.group(3))
            else:
                d = date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
            if (today - d).days > asof_days:
                summary["asof_old"] += 1
                flag_resource(con, r["id"], "unresolved_claim",
                              f"'as of' claim dated {d.isoformat()} "
                              f"({(today - d).days}d old) — curation review")

        # ── stubs: title-only rows ───────────────────────────────────────
        if len(r["body_text"] or "") < stub_chars:
            summary["stubs"] += 1
            flag_resource(con, r["id"], "stub",
                         f"body {len(r['body_text'] or '')} chars < {stub_chars}")

    con.commit()
    summary["tracker_calls"] = resolver.calls
    summary["resolver_failures"] = resolver.failures
    return summary


def _cached_state(con: sqlite3.Connection, ref: str) -> str | None:
    try:
        row = con.execute(
            "SELECT state FROM marker_cache WHERE ref=?", (ref,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _apply_issue_state(con: sqlite3.Connection, resource_id: int,
                       ref: str, state: str, summary: dict) -> None:
    if state == "closed":
        summary["resolved_closed"] += 1
        flag_resource(con, resource_id, "stale_marker",
                      f"{ref} CLOSED — confirmed-stale claim; curation: "
                      "trace file history, verify, then update/delete")
    else:
        summary["resolved_open"] += 1
        # open issue: the marker is legitimate work, not stale context
        resolve_flag(con, resource_id, PENDING)
