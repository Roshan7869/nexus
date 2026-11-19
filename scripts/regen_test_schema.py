"""Generate tests/schema_production.sql: DDL for tables the test DB lacked.

Source of truth = the live DB's sqlite_master (what production actually has).
Idempotent (CREATE ... IF NOT EXISTS) so it is safe to apply after SCHEMA_SQL.
Skips FTS/vec virtual tables and their shadow tables; skips anything conftest
already declares.
"""
import re
import sqlite3
from pathlib import Path

ROOT = Path(r"C:\Users\khatr\OneDrive\Documents\deepseek-harness\default-workspace\nexus")
OUT = ROOT / "tests" / "schema_production.sql"

src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
m = re.search(r'SCHEMA_SQL = """(.*?)"""', src, re.S)
have = set(re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", m.group(1) if m else "", re.I))

con = sqlite3.connect(r"file:D:\nexus-data\db\nexus.db?mode=ro", uri=True)
tables = con.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
    "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()

SKIP = ("chunks_fts", "sections_fts", "resources_fts", "vec_")
lines = [
    "-- Production-table parity for the test DB (generated 2026-11-19).",
    "-- WHY: conftest.py's hand-written SCHEMA_SQL declared only 21 of the live",
    "-- DB's 98 tables. Tests exercising newer features died on 'no such table:",
    "-- outcome_log / tool_sessions / note_ledger / hygiene_flags / resource_edges'",
    "-- — 86 of the 90 pre-existing suite failures. This file carries the missing",
    "-- definitions verbatim from the live schema so the test DB matches production.",
    "-- Regenerate with: python scripts/regen_test_schema.py",
    "--",
    "-- Applied by tests/conftest.py::_init_test_database after SCHEMA_SQL.",
    "",
]
count = 0
for name, ddl in tables:
    if name in have or name.startswith(SKIP):
        continue
    if not ddl.upper().lstrip().startswith("CREATE TABLE"):
        continue
    ddl = re.sub(r"^CREATE TABLE\s+", "CREATE TABLE IF NOT EXISTS ", ddl.strip(), flags=re.I)
    lines.append(ddl.rstrip(";") + ";")
    for (isql,) in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
            "AND tbl_name=?", (name,)).fetchall():
        lines.append(re.sub(r"^CREATE INDEX\s+", "CREATE INDEX IF NOT EXISTS ",
                            re.sub(r"^CREATE UNIQUE INDEX\s+", "CREATE UNIQUE INDEX IF NOT EXISTS ",
                                   isql.strip()), flags=re.I).rstrip(";") + ";")
    lines.append("")
    count += 1
con.close()

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {OUT.name}: {count} tables, {len(lines)} lines")
