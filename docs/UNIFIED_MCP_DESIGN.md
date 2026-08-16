# NEXUS Unified MCP Server — Cross-IDE Project Continuity Design (v1, 2026-08-17)

**Goal:** one MCP server that every AI coding IDE (OpenCode, Claude Code, ZCode,
Codex, ...) attaches to, so that switching IDE preserves: project learnings,
project context, and the ability to execute operations on the project. The brain
lives in NEXUS; IDEs are interchangeable faces.

## Current state (measured 2026-08-17 — from the functional audit)

| Capability | State | Evidence |
|---|---|---|
| One server, many IDEs | ✅ mostly | `nexus-mcp-enhanced.py` FastMCP, ~60 tools; OpenCode + Claude Code both connect (own configs) |
| Shared state substrate | ✅ | all state in `db/nexus.db` — stdio MCP is one process PER IDE, continuity already DB-backed |
| Feedback events per IDE | ✅ | `routing_log.ide`: opencode 1,912 / claude-code 522 rows; events flow via `/v1/event` → `record_outcome` |
| Context scoping | ⚠️ partial | cache keyed `{project}:{task_type}:{key}` (harness_mcp_bridge:184); but learnings tagged `default`/blank leak into all projects; resource catalog global |
| Learning per project | ❌ | beliefs key `(provider, model, task_type)` — NO project dimension (learning.py:682-720): project A outcomes shift project B routing |
| Project identity | ⚠️ implicit | `project` is a free string from callers; 4,760/7,911 routing rows say `default` — identity barely used |
| Resume/handoff | ❌ | no "what was I doing in this project" tool; sessions not continuable across IDEs |
| Uniform MCP registration | ⚠️ | gitnexus MCP: OpenCode ✅ / Claude Code project `.mcp.json` ❌ / ZCode ❌ |

**Design conclusion:** NEXUS is already the unified server. The work is a
**continuity layer**: explicit project identity, per-project beliefs, handoff
tools, and uniform registration. No new server process.

## Architecture

```
 OpenCode ──┐                          ┌─ resources + clusters (global catalog)
Claude Code ─┼─ stdio MCP (1 proc each) │  user_learnings        (per project)
 ZCode ─────┤                          │  model_beliefs         (per project ← U2)
 Codex ─────┘─ nexus-mcp-enhanced.py ───┤  context_store         (per project:task)
   IDE events (all IDEs) ── /v1/event ─┤  routing_log           (per project+ide)
                                         └  sessions             (per project, any IDE)
                    ops daemon :8080 ── cluster_route / ide / harness endpoints
```
Continuity rule: **nothing that must survive an IDE switch may live in MCP
process memory.** Everything continuity-related keys off `project` in the DB.

## Continuity layer — five layers

### L1. Project identity (U1) — `projects` table + attach handshake
```sql
CREATE TABLE projects (
  name TEXT PRIMARY KEY,            -- 'nexus', 'kaari', 'trading'
  git_root TEXT, git_branch TEXT,   -- auto-detected from cwd at attach
  last_ide TEXT, last_active_at TEXT,
  notes TEXT
);
```
```python
# new MCP tool — every IDE session calls this FIRST
@mcp_tool("project_attach")
def project_attach(project: str, git_root: str = None) -> dict:
    root = git_root or detect_git_root()          # from cwd of the IDE session
    upsert_projects(project, root, ide=IDE_TAG)    # last_ide = who touched it last
    return {"project": project, "resume_hint": project_resume(project, preview=True)}
# Server keeps the attached project in a per-connection context var; every other
# tool inherits it (no repeated project args; kills the 'default' epidemic).
```

### L2. Context continuity (U1) — explicit get/set + resume
Existing: `context_store` cache `{project}:{task_type}:{key}`. Add:
```
project_context_set(key, content)      # write-through to DB (project-scoped)
project_context_get(key)               # any IDE, any time
project_resume()                       # THE switching tool:
    → last 10 routing_log rows for project (what was I doing)
    → open sessions (tool_session_broker, any IDE can pick them up)
    → latest user_learnings (project-scoped)
    → active context block (harness context_store)
    → beliefs snapshot (L3)
```
Fix the leak: `_build_context_block` (tool_session_broker:336) — restrict
`default`-project learnings to exact match unless `include_default=True`.

### L3. Learning continuity (U2) — per-project beliefs with global fallback
```python
# belief key gains project: (provider, model, task_type, project)
# cold start: project row missing → sample from the GLOBAL belief
def thompson_sample(candidates, task_type, project):
    beliefs = [get_belief(c, task_type, project) or get_belief(c, task_type, "*")
               for c in candidates]
    return sample(beliefs)          # project-specific evidence wins once it exists

def record_outcome(skill, outcome, project, ...):
    _ensure_belief(conn, "nexus", f"resource:{skill}", task_type, project)  # + "*"
    update both rows; weight project row 1.0, global row 0.5
    # migration: existing 5,504 rows become the "*" (global) population
```
Result: skills that work in `kaari` stop distorting routing in `trading`, while
new projects still inherit everything learned globally.

### L4. Operations continuity (U3) — sessions any IDE can resume
Execution already flows through `tool_session_broker` / `execution_plane` with
DB-backed sessions. Add `session_handoff(session_id)` → returns the session
token + state so another IDE continues the same operation stream; tie to the
existing `nexus_session_open`/`task_start` tools (they gain project from L1).

### L5. Event parity (mostly done) — one feedback path, tagged
All IDEs: `ide_event` MCP tool or `POST /v1/event`; both land in
`record_outcome` with `ide` + `project` columns. No change needed beyond L1
making `project` reliable.

## Tool surface delta (7 new tools, ~60 existing unchanged)

| Tool | Layer | Purpose |
|---|---|---|
| `project_attach` | L1 | handshake; sets project for the connection |
| `project_resume` | L2 | cross-IDE "where was I" |
| `project_context_set/get` | L2 | durable, project-scoped scratchpad |
| `project_beliefs` | L3 | per-project belief stats (dashboard parity) |
| `session_handoff` | L4 | resume an operation stream in another IDE |
| `project_stats` | L1 | routing/outcome history per project |

## Uniform MCP registration (activate everywhere)

Claude Code — add to project `.mcp.json` (DONE in this change):
```json
"gitnexus": { "command": "/home/roshan/.npm-global/bin/gitnexus", "args": ["mcp"] }
```
ZCode — user MCP config (client-side, add manually):
`gitnexus`: same command; `nexus`: venv python3 + server/nexus-mcp-enhanced.py.
OpenCode — already has nexus + gitnexus + agentation (verified in
~/.config/opencode/opencode.json).

## Phasing

| Phase | Scope | Exit gate |
|---|---|---|
| U1 (1 day) | projects table, project_attach/resume/context_set/get, default-leak fix, registration matrix | switch OpenCode→Claude Code mid-task, `project_resume` returns the working state |
| U2 (1-2 days) | per-project beliefs + migration to `*` population + tests | eval harness shows project A outcomes no longer move project B routing |
| U3 (1 day) | session_handoff + task_start project binding | an operation stream started in one IDE completes in another |
| U4 (½ day) | dashboard: per-project brain view | human-visible continuity |

## Risks

- **stdio = per-IDE process**: connection-scoped project state must never be
  relied on for continuity — DB only (design rule above).
- **Belief migration**: 5,504 existing rows → global `*`; per-project rows start
  empty (fallback prevents cold-start blindness). Reversible via project='' key.
- **Project string discipline**: tools called without attach still work
  (project='default') — resume/learning simply degrade, never break.
- **Secrets**: opencode.json currently embeds a provider API key in plaintext —
  move to env expansion during U1 config work (hygiene, not blocking).
