#!/usr/bin/env bash
# Start Nexus Unified Server (LLM Gateway + MCP Bridge + Learning)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Load API keys
if [ -f .env ]; then
  set -a; source .env; set +a
fi

export NEXUS_API_KEY="${NEXUS_API_KEY:-nexus-secret}"

# Activate venv
[ -d .venv ] && source .venv/bin/activate

echo "Starting Nexus Unified Server on port ${NEXUS_PORT:-8000}..."
exec uvicorn server.unified:app --host 127.0.0.1 --port "${NEXUS_PORT:-8000}" --log-level info
