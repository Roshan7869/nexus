"""Absorb a repository's agent resources (skills / agents / commands) into NEXUS.

Turns a third-party repo of harness resources into first-class ``resources`` rows
so NEXUS routing, FTS5 retrieval and the capability catalog can see them.

Layouts recognised (all optional; anything absent is simply not absorbed):

    <repo>/skills/<name>/SKILL.md      -> resource_type 'skill'
    <repo>/agents/<name>.md            -> resource_type 'agent'
    <repo>/commands/<name>.md          -> resource_type 'command'
    <repo>/hooks/<name>.md             -> resource_type 'hook'

Frontmatter is parsed with a deliberately small reader (no PyYAML dependency, so
this runs anywhere): top-level ``key: value`` pairs plus ``>-``/``|`` block
scalars. Anything unrecognised is ignored, never guessed at.

Safety rules:

* ``resources.name`` is globally UNIQUE and the repo convention is a bare name
  (see ``scripts/embed-single.py``, ``server/mcp_sync.py``). If a name already
  belongs to a *different* repo, the row is reported as a conflict and left
  untouched — absorbing one repo must never silently steal another's resource.
* Re-absorbing the same repo is idempotent: rows are updated in place and that
  resource's chunks are rewritten.
* Nothing is marked verified or given an outcome. Absorption is registration.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

#: Directory names that never contain first-party resources.
SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "vendor", "site-packages",
    "__pycache__", ".venv", "venv", "target", ".next", ".turbo", "coverage",
})

#: Directory name -> resource_type
LAYOUT: dict[str, str] = {
    "skills": "skill",
    "agents": "agent",
    "commands": "command",
    "hooks": "hook",
}

#: files that are never resources on their own
SKIP_NAMES = {"README.md", "readme.md", "TEST.md", "test.md"}

#: Accepted resource names. Colons are allowed: `research_ingest.py` already names
#: resources `doc:doi:...` / `doc:arxiv:...`, and harness command packs use
#: namespaced names such as `gsd:add-tests`. Everything else stays restricted so a
#: junk YAML value cannot become a resource.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$", re.I)


@dataclass
class DiscoveredResource:
    name: str
    resource_type: str
    file_path: str          # relative to the repo root, forward slashes
    description: str
    body: str
    metadata: dict = field(default_factory=dict)

    @property
    def token_count(self) -> int:
        return max(1, len(self.body.split()))


# ── frontmatter ──────────────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). Tolerates a missing or malformed header."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return {}, text

    meta: dict = {}
    raw = lines[1:end]
    body = "\n".join(lines[end + 1:]).strip()

    i = 0
    while i < len(raw):
        line = raw[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        if value in (">-", ">", "|", "|-", ">+", "|+"):
            # block scalar: gather the following indented lines
            block: list[str] = []
            i += 1
            while i < len(raw) and (not raw[i].strip() or raw[i].startswith((" ", "\t"))):
                block.append(raw[i].strip())
                i += 1
            joined = ("\n" if value.startswith("|") else " ").join(
                b for b in block if b)
            meta[key] = joined.strip()
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        meta[key] = value
        i += 1

    return meta, body


# ── discovery ────────────────────────────────────────────────────────────────

def discover(repo_root: Path) -> list[DiscoveredResource]:
    """Walk the recognised layouts and return every resource found.

    Layouts are searched at ANY depth, because packagers move them: vercel-labs/
    agent-skills keeps its skills under ``packages/**/skills/*/SKILL.md`` now, not
    top-level ``skills/``. A fixed top-level lookup silently missed 200+ skills and
    is why a re-absorb reported "9 new, 0 updated" against a 200-file repo.

    A ``SKILL.md`` anywhere is a skill (that filename is the convention, not the
    parent directory). Other recognised types are found by their directory name.
    """
    found: list[DiscoveredResource] = []
    repo_root = Path(repo_root)
    seen_paths: set[Path] = set()

    def _skipped(path: Path) -> bool:
        return any(part in SKIP_DIRS for part in path.parts)

    def _emit(path: Path, rtype: str, fallback_name: str, whole_file: bool) -> None:
        if path in seen_paths or _skipped(path):
            return
        if path.name in SKIP_NAMES:
            return
        seen_paths.add(path)
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("absorb: cannot read %s: %s", rel, exc)
            return
        meta, _body = parse_frontmatter(text)
        name = str(meta.get("name") or "").strip() or fallback_name
        name = name.strip()
        if not _NAME_RE.match(name):
            logger.warning("absorb: skipping %s (unusable name %r)", rel, name)
            return
        description = str(meta.get("description") or "").strip()
        if not description:
            description = _first_paragraph(_body)
        found.append(DiscoveredResource(
            name=name, resource_type=rtype, file_path=rel,
            description=description[:500], body=text.strip(), metadata=meta,
        ))

    # 1) SKILL.md anywhere is a skill, named by frontmatter or its directory.
    for path in sorted(repo_root.rglob("SKILL.md")):
        _emit(path, "skill", path.parent.name, whole_file=True)

    # 2) Other types by directory name, at any depth.
    for dirname, rtype in LAYOUT.items():
        if dirname == "skills":
            continue  # covered by SKILL.md discovery above
        for base in sorted(p for p in repo_root.rglob(dirname)
                           if p.is_dir() and not _skipped(p.relative_to(repo_root))):
            for path in sorted(base.rglob("*.md")):
                _emit(path, rtype, path.stem, whole_file=True)

    return found


def _first_paragraph(body: str) -> str:
    for chunk in re.split(r"\n\s*\n", body):
        text = " ".join(line.strip() for line in chunk.splitlines() if line.strip())
        if text and not text.startswith("#"):
            return text
    return ""


# ── persistence ──────────────────────────────────────────────────────────────

def _repo_name(repo_root: Path) -> str:
    return repo_root.resolve().name


def _git_head(repo_root: Path) -> tuple[str, str] | None:
    """(full_sha, iso_date) of a repo's HEAD, or None when not a git checkout."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        date = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%cI", "HEAD"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()[:10]
        return (sha, date) if sha else None
    except Exception:
        return None


def _connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.row_factory = sqlite3.Row
    return con


def _ensure_source(con: sqlite3.Connection, source_path: str, meta: dict) -> int:
    row = con.execute(
        "SELECT id FROM ingestion_sources WHERE source_type='repo_resources' AND source_path=?",
        (source_path,),
    ).fetchone()
    if row:
        con.execute(
            "UPDATE ingestion_sources SET status='ready', metadata=?, "
            "updated_at=datetime('now') WHERE id=?",
            (json.dumps(meta), int(row["id"])),
        )
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO ingestion_sources(source_type, source_path, content_hash, status, metadata) "
        "VALUES('repo_resources', ?, ?, 'ready', ?)",
        (source_path, "repo", json.dumps(meta)),
    )
    return int(cur.lastrowid)


def absorb(
    repo_root: str | Path,
    db_path: str | Path,
    *,
    repo_name: str | None = None,
    cluster: str | None = None,
    tier: int = 2,
    dry_run: bool = False,
) -> dict:
    """Absorb one repo. Returns a summary dict; never raises on a per-row problem."""
    repo_root = Path(repo_root)
    if not repo_root.is_dir():
        raise FileNotFoundError(f"repo root not found: {repo_root}")
    repo = repo_name or _repo_name(repo_root)
    resources = discover(repo_root)

    summary: dict = {
        "repo": repo, "repo_root": str(repo_root), "db": str(db_path),
        "dry_run": dry_run, "discovered": len(resources),
        "inserted": 0, "updated": 0, "unchanged": 0, "conflicts": [], "errors": [],
        "by_type": {}, "names": [],
    }
    for r in resources:
        summary["by_type"][r.resource_type] = summary["by_type"].get(r.resource_type, 0) + 1

    if dry_run:
        summary["names"] = [f"{r.resource_type}:{r.name}" for r in resources]
        return summary
    # NOTE: no early return when `resources` is empty — the S1 tombstone pass
    # must still run (with its group guard) so a zero-discovery pass can never
    # silently mass-archive, and legit upstream deletions still close intervals.

    con = _connect(db_path)
    try:
        source_id = _ensure_source(con, str(repo_root.resolve()),
                                   {"repo": repo, "resources": len(resources)})
        head = _git_head(repo_root)
        for r in resources:
            try:
                action = _upsert(con, r, repo=repo, source_id=source_id,
                                 cluster=cluster, tier=tier, head=head)
            except _Conflict as exc:
                summary["conflicts"].append({"name": r.name, "owner": exc.owner})
                logger.warning("absorb: %s", exc)
                continue
            except sqlite3.Error as exc:
                summary["errors"].append(f"{r.name}: {exc}")
                logger.warning("absorb: %s failed: %s", r.name, exc)
                continue
            summary[action] += 1
            summary["names"].append(f"{r.resource_type}:{r.name}")

        # S1 tombstone pass: rows of this repo whose file_path was NOT rediscovered
        # are gone upstream -> close their validity interval + audit (atomic with the
        # upserts: same transaction, one commit below).
        # Guard is per layout group (the first path component, e.g. 'skills/'):
        # a missing file is tombstoned only when its layout group still has
        # discovered survivors; a group that vanished wholesale means a partial
        # checkout / broken pull -> skip that group (fail-open, logged).
        # Foreign path conventions (Linux-absolute '/home/.../x' rows written by
        # enrichment/recovery scripts outside absorb) are UNPROVABLE tombstones:
        # never archive what you cannot prove gone. When the row's path suffix
        # matches a discovered file, self-heal the row's file_path to the
        # discovered literal so future absorbs compare exactly.
        summary["tombstoned"] = summary.get("tombstoned", 0)
        summary["selfhealed_paths"] = summary.get("selfhealed_paths", 0)
        summary["foreign_path_skipped"] = summary.get("foreign_path_skipped", 0)
        active = con.execute(
            "SELECT id, file_path FROM resources "
            "WHERE repo_name=? AND status='active' AND invalidated_at IS NULL",
            (repo,),
        ).fetchall()
        if active and resources:
            def _clean(fp: str) -> tuple[str, ...]:
                return tuple(
                    p for p in fp.replace("\\", "/").split("/")
                    if p and p not in ("home", "roshan")
                )

            seen_paths = {r.file_path for r in resources}
            seen_keys = {_clean(p): p for p in seen_paths}
            seen_groups = {dk[0] for dk in seen_keys if dk}
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            skipped_groups: set[str] = set()
            for row in active:
                fp = row["file_path"]
                if fp in seen_paths:
                    continue
                key = _clean(fp)
                # suffix self-heal: the file exists upstream; the row's stored
                # path is just a foreign/absolute convention
                healed = None
                for dk, literal in seen_keys.items():
                    if len(dk) < 2 or len(key) < 2:
                        continue
                    if key == dk or key[-len(dk):] == dk or dk[-len(key):] == key:
                        healed = literal
                        break
                if healed is not None and healed != fp:
                    con.execute(
                        "UPDATE resources SET file_path=? WHERE id=?",
                        (healed, row["id"]),
                    )
                    summary["selfhealed_paths"] += 1
                    continue
                if healed is not None:
                    continue
                norm = fp.replace("\\", "/")
                if norm.startswith("/") or "home/roshan" in norm or ":" in norm[:3]:
                    summary["foreign_path_skipped"] += 1
                    continue
                group = norm.split("/")[0] if norm.count("/") > 0 else ""
                if group and group not in seen_groups:
                    skipped_groups.add(group)
                    continue
                con.execute(
                    "UPDATE resources SET status='archived', invalidated_at=? "
                    "WHERE id=?",
                    (now, row["id"]),
                )
                con.execute(
                    "INSERT INTO invalidation_audit(resource_id, reason, invalidated_at) "
                    "VALUES(?, 'upstream_delete', ?)",
                    (row["id"], now),
                )
                summary["tombstoned"] += 1
            if skipped_groups:
                summary["errors"].append(
                    "tombstone skipped (possible partial checkout) for groups: "
                    + ", ".join(sorted(skipped_groups))
                )
                logger.warning("absorb %s: %s", repo, summary["errors"][-1])
        elif active:
            summary["errors"].append(
                "tombstone skipped: no resources discovered this pass "
                "(possible partial checkout)"
            )
            logger.warning("absorb %s: %s", repo, summary["errors"][-1])
        con.commit()
    finally:
        con.close()
    return summary


def _upsert(con: sqlite3.Connection, r: DiscoveredResource, *, repo: str,
            source_id: int, cluster: str | None, tier: int,
            head: tuple[str, str] | None = None) -> str:
    """Insert/update one resource + chunks. Returns 'inserted'|'updated'|'unchanged'.

    Raises _Conflict (a sqlite3.Error subclass) when the name is owned elsewhere.

    S1: every write stamps absorbed_at_commit/date from the repo HEAD and mirrors
    the content version into resource_versions (validity interval ledger).
    """
    existing = con.execute(
        "SELECT id, repo_name, resource_type, file_path, description, body_text "
        "FROM resources WHERE name=?", (r.name,)
    ).fetchone()
    if existing and existing["repo_name"] not in (repo, "unknown"):
        raise _Conflict(r.name, existing["repo_name"], repo)

    tokens = r.token_count
    sha, stamp_date = head if head else (None, None)
    # microsecond resolution: two absorbs in the same wall-clock second must not
    # collide on resource_versions' UNIQUE(resource_id, valid_from)
    version_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _open_version(rid: int, body_hash: str) -> None:
        con.execute(
            "UPDATE resource_versions SET invalidated_at=? "
            "WHERE resource_id=? AND invalidated_at IS NULL",
            (version_stamp, rid),
        )
        stamp = version_stamp
        for attempt in range(6):
            try:
                con.execute(
                    "INSERT INTO resource_versions(resource_id, body_hash, "
                    "absorbed_at_commit, absorbed_at_date, valid_from) VALUES(?,?,?,?,?)",
                    (rid, body_hash, sha, stamp_date, stamp),
                )
                return
            except sqlite3.IntegrityError:
                # same resource absorbed twice inside one OS clock tick
                # (duplicate frontmatter names across files): bump the stamp
                stamp = f"{version_stamp}#{attempt + 1}"

    content_hash = hashlib.sha256(r.body.encode("utf-8", "replace")).hexdigest()[:16]

    if existing:
        rid = int(existing["id"])
        unchanged = (
            existing["resource_type"] == r.resource_type
            and existing["file_path"] == r.file_path
            and existing["description"] == r.description
            and existing["body_text"] == r.body
        )
        if unchanged:
            return "unchanged"
        con.execute(
            """UPDATE resources SET resource_type=?, repo_name=?, file_path=?,
                   description=?, body_text=?, status='active', tier=?,
                   token_count=?, cluster=COALESCE(?, cluster),
                   absorbed_at_commit=?, absorbed_at_date=?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (r.resource_type, repo, r.file_path, r.description, r.body,
             tier, tokens, cluster, sha, stamp_date, rid),
        )
        # supersession ledger: close the previous version's interval, open this one
        con.execute(
            "UPDATE resources SET invalidated_at=NULL WHERE id=?", (rid,),
        )
        _open_version(rid, content_hash)
        action = "updated"
    else:
        cur = con.execute(
            """INSERT INTO resources(name, resource_type, repo_name, file_path,
                   description, body_text, status, tier, token_count,
                   times_triggered, times_correct, times_wrong, cluster,
                   absorbed_at_commit, absorbed_at_date)
               VALUES(?,?,?,?,?,?,'active',?,?,0,0,0,?,?,?)""",
            (r.name, r.resource_type, repo, r.file_path, r.description,
             r.body, tier, tokens, cluster, sha, stamp_date),
        )
        rid = int(cur.lastrowid)
        _open_version(rid, content_hash)
        action = "inserted"

    # retrieval: one section chunk per resource, mirrored into FTS5
    con.execute("DELETE FROM ingestion_chunks WHERE source_id=? AND resource_id=?",
                (source_id, rid))
    cur = con.execute(
        """INSERT INTO ingestion_chunks(source_id, resource_id, chunk_index,
               chunk_type, heading, content, content_hash, token_count)
           VALUES(?,?,0,'section',?,?,?,?)""",
        (source_id, rid, r.name, r.body, content_hash, tokens),
    )
    chunk_id = int(cur.lastrowid)
    try:
        con.execute("DELETE FROM chunks_fts WHERE rowid=?", (chunk_id,))
        con.execute("INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
                    "chunk_type, source_id) VALUES(?,?,?,?,?,?)",
                    (chunk_id, r.body, r.name, rid, r.resource_type, source_id))
    except sqlite3.OperationalError as exc:
        logger.debug("absorb: chunks_fts insert skipped for %s: %s", r.name, exc)

    if cluster:
        try:
            con.execute(
                "INSERT INTO cluster_membership(cluster_name, resource_id, affinity) "
                "VALUES(?,?,0.75) ON CONFLICT DO NOTHING",
                (cluster, rid),
            )
        except sqlite3.Error as exc:
            logger.debug("absorb: membership skipped for %s: %s", r.name, exc)

    return action


class _Conflict(sqlite3.Error):
    """Raised when a resource name already belongs to another repo."""

    def __init__(self, name: str, owner: str, incoming: str) -> None:
        super().__init__(f"name {name!r} already owned by {owner!r} (absorbing {incoming!r})")
        self.name, self.owner, self.incoming = name, owner, incoming
