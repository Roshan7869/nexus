#!/usr/bin/env bash
# Start Nexus + OpenCode TUI together
# Nexus MCP is auto-started by opencode (configured in opencode.json).
# This wrapper also starts the Nexus HTTP server (LLM proxy + learning loop).
set -euo pipefail

NEXUS_DIR="/home/roshan/nexus"
NEXUS_PORT="${NEXUS_PORT:-8000}"
NEXUS_PID=""

cleanup() {
  if [ -n "$NEXUS_PID" ] && kill -0 "$NEXUS_PID" 2>/dev/null; then
    echo "   Stopping Nexus server..."
    kill "$NEXUS_PID" 2>/dev/null || true
    wait "$NEXUS_PID" 2>/dev/null || true
  fi
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Load env
if [ -f "$NEXUS_DIR/.env" ]; then
  set -a; source "$NEXUS_DIR/.env"; set +a
fi

# Activate venv
if [ -d "$NEXUS_DIR/.venv" ]; then
  source "$NEXUS_DIR/.venv/bin/activate"
fi

# Start Nexus HTTP server in background
echo "Starting Nexus on port $NEXUS_PORT..."
cd "$NEXUS_DIR"
uvicorn server.unified:app --host 0.0.0.0 --port "$NEXUS_PORT" --log-level warning &
NEXUS_PID=$!
sleep 2

# Verify it started
if ! kill -0 "$NEXUS_PID" 2>/dev/null; then
  echo "Warning: Nexus server failed to start (check .env keys)"
fi

echo "Launching OpenCode..."
opencode "$@"
