# M17 — `opencode-with-nexus.sh` (MODIFY, OpenCode identity)

## GitNexus queries
```
query({search_query: "opencode launch nexus http server wrapper"})
```
Used: `read opencode-with-nexus.sh` (44 lines: `NEXUS_DIR`, `NEXUS_PORT=8000`, trap cleanup, source `.env`, venv activate, `uvicorn server.unified:app` bg + `sleep 2` + liveness, `opencode "$@"`).

## Current state
No `NEXUS_IDE` export (relies on `.mcp.json` default `claude-code`); no harness probe.

## Blast radius: LOW (dev launcher script; not used in CI gate)

## Pseudocode (bash)
```bash
NEXUS_IDE="${NEXUS_IDE:-opencode}"          # NEW: session identity for M5/M6/M8
NEXUS_LOCAL_HARNESS="${NEXUS_LOCAL_HARNESS:-1}"  # NEW: default on for interactive use
export NEXUS_IDE NEXUS_LOCAL_HARNESS
# ... existing uvicorn bg + liveness unchanged ...
if [[ "${1:-}" == "--harness-status" ]]; then   # NEW probe
  venv/bin/python3 -c "from server.local_harness.session import status_summary; print(status_summary())"
  exit 0
fi
echo "Launching OpenCode (NEXUS_IDE=$NEXUS_IDE, LOCAL_HARNESS=$NEXUS_LOCAL_HARNESS)..."
opencode "$@"
```

## Build steps
1. Insert exports before uvicorn start; append probe branch before final launch.
2. Smoke: `./opencode-with-nexus.sh --harness-status` prints JSON; normal launch passes through.
3. Keep `set -euo pipefail` + trap semantics untouched.

## Tests
Shell smoke only; paste output in PR.
