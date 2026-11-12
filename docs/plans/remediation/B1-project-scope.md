# B1 — Project-scoped ranking; reject `default` in production

Source: plan §B1 · Commit: `B1: project-scoped ranking; reject default project` · Depends on: A1 · Gate: G6, G7

## Problem (verified)

- `find_matching_skills(task, project="default", …)` at `server/router.py:415` accepts `project` but the plan says it does not filter by it. **Verify** by reading the SQL around `router.py:552–680` before editing.
- No `project_resources` table exists.
- `RouteRequest` with `project="default"` is accepted at `server/unified.py:143` and `server/nexus_http_daemon.py:300`.
- MCP `find_skills(task, project="default", …)` at `server/nexus-mcp-enhanced.py:79` — also defaults to `default`.
- `router_v2/v5.py:unified_route_v5` exists; plan says it has no project boost. **Verify** signature first.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| Migration in `server/migrations/002_project_resources.sql` | Use **`db/migrations/022_project_resources.sql`**. |
| `sqlite3 db/nexus.db < …` to apply | Use `scripts/run_migration_*.py` pattern or `server/db.py` migrator — check which applies migrations here. |
| B1.4 edits `nexus_http_daemon.py` for `/nexus/route`, `/nexus/search`, `/nexus/autopilot/run` | These routes are in **`unified.py`**. Apply `_require_project` there. |
| `filtered_ids`, `conn` in pseudocode | Not verified in `find_matching_skills`. Re-read before editing. |
| Scope of "reject default" | MCP `find_skills` also defaults to `default`. Decide whether MCP is in scope (see open decisions). |

## Pseudocode (corrected)

```sql
-- db/migrations/022_project_resources.sql
CREATE TABLE IF NOT EXISTS project_resources (
    project      TEXT    NOT NULL,
    resource_id  INTEGER NOT NULL REFERENCES resources(id),
    priority     INTEGER NOT NULL DEFAULT 50,
    PRIMARY KEY (project, resource_id)
);
CREATE INDEX IF NOT EXISTS idx_project_resources_project ON project_resources(project);
```

```python
# server/router.py  — inside find_matching_skills, after filtered_ids is known
if project and project != "default":
    rows = conn.execute("SELECT resource_id FROM project_resources WHERE project=?",
                        (project,)).fetchall()
    project_ids = {r[0] for r in rows}
    if project_ids:
        boost_ids = project_ids           # boost, do not hard-filter (see decision)
```

Use a boost, not a hard filter, unless the decision below says otherwise. A hard filter over-narrows when `project_resources` is sparse (plan's own risk matrix B1).

```python
# server/unified.py and server/nexus_http_daemon.py
def _require_project(project: str | None) -> str:
    p = (project or "").strip()
    if not p or p == "default":
        raise HTTPException(400, "project is required; 'default' is not allowed")
    return p
```

Call it in `/nexus/route` and `/nexus/search` (and `/nexus/autopilot/run`).

## Steps

1. Read `router.py` 415–680 and record: where `project` is used today, and where `filtered_ids` is set.
2. Write and apply `db/migrations/022_project_resources.sql`. Back up `db/nexus.db` first.
3. Add the boost in `find_matching_skills`. Add the boost in `unified_route_v5` (verify its scoring function first).
4. Add `_require_project` to `unified.py` and the daemon. Replace `project` default in `RouteRequest` with a required field, or validate in the handler.
5. Tests and benchmark.

## Tests

New `tests/test_project_boost.py` (G7) and `tests/test_project_required.py` (G6):

```python
from fastapi.testclient import TestClient
from server.unified import app

def test_default_project_rejected():                       # G6
    c = TestClient(app)
    r = c.post("/nexus/route", json={"task": "fix bug", "project": "default"})
    assert r.status_code == 400

def test_project_resources_rank_higher(seeded_project_db):  # G7
    # seeded_project_db: in-memory DB with resource X in project_resources for 'probe'
    from server.router import find_matching_skills
    base = [r["name"] for r in find_matching_skills("probe task", "other")]
    boosted = [r["name"] for r in find_matching_skills("probe task", "probe")]
    assert boosted.index("X") <= base.index("X") if "X" in base else True
```

Use in-memory fixtures; the G7 test must not depend on live DB rows.

Benchmark: run golden eval per project in `evals/golden/routing_cases.yaml` (cases have a `project` field — verify). Expect pass rate to change; record before/after.

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/route -H "Content-Type: application/json" \
  -d '{"task":"fix bug","project":"default"}' -w "\n%{http_code}\n"     # expect 400
```

## Rollback

Set `project_boost` to 0 (config constant) and drop the `_require_project` call. Table can stay.

## Open decisions

- **Boost vs hard filter.** Recommend boost (0.25) per plan rollback note.
- **MCP `find_skills` default.** Recommend: reject `default` there too, but only after the HTTP path is stable — the MCP server is used by every IDE session.
