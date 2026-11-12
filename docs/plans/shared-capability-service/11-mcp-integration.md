# server/nexus-mcp-enhanced.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Turn new shared-capability functions into thin clients of the daemon so per-harness stdio processes do not start duplicate providers.

## Required contract and implementation instructions

Add capability_search, capability_get, skill_read, capability_recommend, capability_invoke and capability_session_close tools. Add native receipt bridge only for capable authenticated hosts.
Use one bounded HTTP client to the configured daemon URL. Validate URL against the chosen deployment endpoint and fail clearly when unavailable; do not fall back to starting a local provider runtime.
task_start returns recommendations plus discovery handles. Keep visible_tools as a deprecated compatibility field and add recommendations_are_advisory=true. max_tools limits returned recommendations only.
Replace the instruction 'Use only visible_tools' with instructions to search/get additional capabilities at any time. Preserve existing legacy task_complete behavior; new sessions use namespaced session IDs and dispatch completion to the matching owner.
MCP host tool refresh support must be negotiated; generic search/get/invoke works for hosts without dynamic tool listing.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
MCP_CAPABILITY_CALL(method,args):
  derive authenticated harness profile from connection configuration
  translate to /capabilities/v1 request
  use bounded client timeout; propagate typed daemon errors
  return structured MCP content with request and invocation IDs

TASK_START(query,project):
  create shared service session; request advisory recommendations
  return session + slate + capability_search/get/invoke instructions
```

## Acceptance checks and failure behavior

Two stdio harness clients must share the same daemon provider generation. Closing one cannot stop the other. Simulate absent daemon and prove zero fallback process starts. Existing session IDs close through their legacy path exactly once.

## Dependencies and implementation order

Depends on 09,10. Impact analysis required for task_start, task_complete, find_skills and runtime initialization.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

