# server/unified.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Preserve search and chat semantics while routing shared capability calls through the common service.

## Required contract and implementation instructions

Keep /nexus/search response shape {query,method,results}; serve read-only catalog search through compatibility conversion when the new flag is enabled. It must not open a routing session or record success.
Keep /nexus/route response shape with skills and router metadata. Share recommendation implementation with daemon route adapters.
Audit /v1/chat/completions task classification independently: model/task-type routing remains harness/gateway responsibility unless explicitly replaced. Do not replace a classification call with a skill-only response lacking task_type.
The existing /mcp/{tool_name} proxy imports nexus-mcp-enhanced.py. New capability functions must dispatch directly to capability service facade; do not create HTTP->MCP->HTTP recursion.
Do not instantiate another runtime in unified.py when mounted into the daemon; use injected handles. Standalone unified mode must be explicitly legacy or explicitly configured as service owner, never silently dual-own.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
SEARCH(req):
  descriptors=catalog.search(query,project,read_only=True)
  return legacy query/method/results shape

MCP_PROXY(name,args):
  if name in new_capability_facade: invoke injected service directly
  else retain existing legacy handling

CLASSIFY_FOR_CHAT(req):
  preserve task_type classifier contract
  classify caller uses separately from skill recommendation
```

## Acceptance checks and failure behavior

Read-only search writes zero recommendation/session/reward rows. Compatibility schemas remain valid. Proxy recursion test expects a single service call. Classification test proves task_type survives. Endpoint failure returns an error rather than an empty success.

## Dependencies and implementation order

Depends on 09,10. Impact required on nexus_search, nexus_route, chat_completions, mcp_proxy and startup.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

