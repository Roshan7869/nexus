# server/tool_session_broker.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Preserve session compatibility while removing recommendation membership as a permission boundary.

## Required contract and implementation instructions

Keep ToolSession persistence and legacy session close contract. Introduce service_version, shared_session_id, recommendation_ids and catalog_revision in a versioned context envelope; migrate existing table only if actually needed after inspecting its schema, preferably use context JSON.
For new sessions, call new recommender through injected facade. Do not use MAX_SESSION_TOOLS as a universe restriction. Maintain pagination discovery handles.
Select execution-capable tools by catalog kind; do not label skill instruction names as executable MCP tools. The deprecated tools_loaded field stays a recommendation snapshot only.
Session close records task-level feedback and releases owned leases. Never award the same task result to every recommended skill.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
START_SESSION(query,project,ide):
  open shared session through service
  obtain recommendations(k=max_tools)
  preserve broker compatibility fields
  attach full catalog handles and advisory instructions

END_SESSION(session,task_outcome):
  idempotently close shared session and archive context
  release owned leases
  retain task feedback without bulk capability reward
  return closure receipt
```

## Acceptance checks and failure behavior

Nonrecommended capability remains discoverable and invocable if allowed. Restart reconstructs session identity. Double close causes no duplicate reward or lease release. Legacy sessions still resolve.

## Dependencies and implementation order

Depends on 09,11. Impact required on ToolSession.to_dict, start_session, end_session, _select_tools and _record_learning.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

