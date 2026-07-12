# DB Connection Factory Migration Guide

**Status**: Factory exists in `server/db.py` but only 7 files use it.
**Problem**: 67+ files use raw `sqlite3.connect()` directly, bypassing the
factory's WAL mode, busy_timeout, and connection management.

## Standard Pattern (use this everywhere)

```python
from server.db import db_conn

with db_conn() as conn:
    rows = conn.execute("SELECT * FROM resources WHERE id = ?", (id,)).fetchall()
    # conn is committed on success, rolled back on exception, always closed
```

## One-off Connections (acceptable)

```python
from server.db import get_connection, close_connection

conn = get_connection()
try:
    rows = conn.execute("SELECT ...").fetchall()
    conn.commit()
finally:
    close_connection(conn)
```

## Anti-Pattern (avoid — needs migration)

```python
import sqlite3
conn = sqlite3.connect("/home/roshan/nexus/db/nexus.db")
# ... no WAL mode, no busy_timeout, no connection management ...
conn.close()
```

## Migration Plan

**Phase 1** (done): Factory in `server/db.py` with WAL, busy_timeout, foreign_keys.

**Phase 2** (in progress): Identify high-traffic files using raw `sqlite3.connect`:
- `server/router.py` — main routing path
- `server/learning.py` — every outcome recording
- `server/broker_runtime.py` — broker transactions
- `server/reflection.py` — reflection loop
- `server/cluster_faiss.py` — cluster indexing

**Phase 3** (future): Refactor these 5 files to use `db_conn()`. Don't
refactor 67+ files at once — too risky, too much surface area.

## Files Using Raw `sqlite3.connect` (as of 2026-07-01)

```
server/hallucination.py
server/maintenance/resource_validator.py
server/broker_runtime.py
server/research_pipeline.py
server/cluster_faiss.py
server/lazy_loader.py
server/rl/thompson.py
server/rl/outcome_log.py
server/cluster_classifier.py
server/metrics/graded.py
+ 57 more files
```

## Why NOT Refactor Everything

Each file using raw `sqlite3.connect` may have:
- Custom row factories
- Long-lived connections (e.g., background threads)
- Read-only mode for performance
- Specific isolation levels

A blanket refactor risks breaking:
- Background schedulers
- MCP tool hot paths
- Test fixtures

## Recommended Action

For new code: always use `from server.db import db_conn`.

For existing code: refactor only when:
1. The file has connection lifecycle bugs (leaks, races)
2. You're already editing the file for other reasons
3. The file is in the hot path of a critical operation

Otherwise, the factory's existence is enough — new code is correct, old
code works (just doesn't get the benefits of WAL + busy_timeout).
