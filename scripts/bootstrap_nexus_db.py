#!/usr/bin/env python3
"""Create db/nexus.db from db/schema.sql plus every numbered migration.

CLAUDE.md documents `python scripts/init_db.py`, but that script does not exist in
this checkout. This is the reproducible replacement: it applies the base schema,
then every `NNN_*.sql` migration in numeric order across both migration
directories (`db/migrations/` and `migrations/`), tolerating statements that are
already satisfied so the script is idempotent and re-runnable.

`BASELINE.sql` is deliberately skipped: it is a partial dump pinned to a 624 MB
live database (89 tables) and is not a forward migration.

Usage:
    python scripts/bootstrap_nexus_db.py                    # -> db/nexus.db
    python scripts/bootstrap_nexus_db.py --db /tmp/test.db  # elsewhere
    python scripts/bootstrap_nexus_db.py --fresh            # delete + rebuild
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "db" / "nexus.db"
MIGRATION_DIRS = (ROOT / "db" / "migrations", ROOT / "migrations")


def _migrations() -> list[Path]:
    """All numbered migrations across both directories, sorted numerically."""
    found: list[tuple[int, str, Path]] = []
    for directory in MIGRATION_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.glob("*.sql"):
            if path.name.upper() == "BASELINE.SQL":
                continue
            m = re.match(r"^(\d+)", path.name)
            if not m:
                continue
            found.append((int(m.group(1)), path.name, path))
    found.sort(key=lambda t: (t[0], t[1]))
    return [p for _, _, p in found]


def _apply(con: sqlite3.Connection, sql: str, label: str) -> tuple[int, list[str]]:
    """Run a whole script via SQLite's own parser (understands BEGIN…END triggers).

    `executescript` aborts at the first failing statement, so on failure we fall
    back to a best-effort statement split to salvage what we can and report the
    rest. A clean fresh run should never take the fallback path.
    """
    if not sql.strip():
        return 0, []
    try:
        con.executescript(sql)
        return 1, []
    except sqlite3.Error as first:
        errors: list[str] = [f"{label}: {first}"]
        ok = 0
        for stmt in _split_statements(sql):
            try:
                con.execute(stmt)
                ok += 1
            except sqlite3.Error as exc:
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate column" in msg:
                    continue
                errors.append(f"{label}: {exc} :: {stmt.strip()[:90]}")
        return ok, errors


def _split_statements(sql: str) -> list[str]:
    """Split on semicolons outside string literals / comments (SQLite has no nested blocks)."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        buf.append(sql[i + 1])
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def bootstrap(db_path: Path, fresh: bool = False, verbose: bool = False) -> int:
    if fresh and db_path.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
        print(f"[fresh] removed {db_path.name}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA foreign_keys=OFF")  # migrations are not FK-ordered
    all_errors: list[str] = []

    schema = ROOT / "db" / "schema.sql"
    if not schema.exists():
        print(f"FATAL: {schema} missing", file=sys.stderr)
        return 2
    ok, errs = _apply(con, schema.read_text(encoding="utf-8"), "schema.sql")
    all_errors += errs
    print(f"schema.sql           {ok:4d} statements ok, {len(errs)} errors")

    for path in _migrations():
        ok, errs = _apply(con, path.read_text(encoding="utf-8"), path.name)
        all_errors += errs
        if verbose or errs or ok:
            print(f"{path.name:28} {ok:4d} ok, {len(errs)} errors")
    con.commit()

    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    fts = [t for t in tables if "_fts" in t]
    con.close()

    print(f"\ndatabase: {db_path}")
    print(f"tables:   {len(tables)}")
    print(f"fts5:     {', '.join(fts) if fts else 'none'}")
    if all_errors:
        print(f"\n{len(all_errors)} non-benign statement errors (first 10):", file=sys.stderr)
        for e in all_errors[:10]:
            print("  " + e, file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--fresh", action="store_true", help="delete and rebuild")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    return bootstrap(Path(args.db), fresh=args.fresh, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
