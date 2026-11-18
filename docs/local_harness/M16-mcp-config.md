# M16 — `.mcp.json` (MODIFY, local-harness env)

## GitNexus queries
```
query({search_query: "mcp server spawn env config"})
```
Used: `read .mcp.json` (43 lines: gitnexus + ruflo + nexus stdio entries; nexus env has PYTHONPATH, NEXUS_DB_PATH, NEXUS_DAEMON_URL, USE_UNIFIED_ROUTER=2, THOMPSON/CLUSTER/REFLECTION=true, NEXUS_IDE=claude-code, HOME).

## Current state
`NEXUS_IDE=claude-code` even for OpenCode sessions; no local-harness flag.

## Blast radius: LOW (config only; IDE re-spawns MCP per session)

## Pseudocode (config, not code)
```json
"nexus": {
  "command": ".../venv/bin/python3",
  "args": [".../server/nexus-mcp-enhanced.py"],
  "env": {
    "...": "(all existing keys unchanged)",
    "NEXUS_IDE": "claude-code",          // KEEP default; opencode passes ide per-call
    "NEXUS_LOCAL_HARNESS": "1",          // NEW: enable M5 tools (0 = legacy task_start only)
    "NEXUS_HARNESS_REDIS": "0",          // NEW: explicit L4 off (M12/M13 no-op)
    "NEXUS_V5_W_SHARED": "0.0",          // NEW: M10 default-off pin
    "NEXUS_V5_CLUSTER_CAP": "0",         // NEW: recycle disabled
    "NEXUS_V5_CHIMERA": "0"              // NEW: M11 default-off pin
  }
}
```

## Build steps
1. Add the five keys; change nothing else (whitespace-stable edit).
2. Spawn smoke: MCP tool list contains `local_harness_*`; `NEXUS_LOCAL_HARNESS=0` hides them (M5 compat branch).
3. Note: per-call `ide=opencode` (M5/M8) is the session identity — do NOT flip global `NEXUS_IDE` (other IDEs share this file).

## Tests
Manual smoke (config has no unit suite); record tool-list diff in PR.
