# server/ephemeral_mcp_proxy.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Keep discovery and invocation available throughout a session.

## Required contract and implementation instructions

Add the stable capability_search/get/skill_read/invoke/session operations to baseline discovery surface. Preserve existing context tools.
merge_tool_surface may append task recommendations when the host supports direct tools, but filter_mcp_tool_names must never remove baseline discovery/invoke due to rank, zero recommendations or session changes.
A capability ID is not automatically an MCP tool name. Map only actual registered functions; instruction skills use skill_read.
Server-side visibility filtering remains authoritative for hidden/private catalog entries.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
FILTER_TOOL_SURFACE(registry,session):
  baseline = existing controls UNION stable capability facade tools
  optional = supported registered direct-tool aliases for current recommendations
  return ordered unique registry members in baseline UNION optional
  # additional catalog capabilities remain reachable through baseline facade
```

## Acceptance checks and failure behavior

Empty slate still exposes search/get/invoke. No duplicate names. Arbitrary capability names do not become callable functions. Switching tasks does not erase baseline discovery.

## Dependencies and implementation order

Depends on 11. Impact required on merge_tool_surface and filter_mcp_tool_names.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

