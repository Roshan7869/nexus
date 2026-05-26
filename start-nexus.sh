#!/bin/bash
# start-nexus.sh — Start NEXUS gateway with virtualenv

cd "$HOME/nexus" || exit 1

# Ensure venv exists
if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt 2>/dev/null || echo "(no requirements.txt)"
fi

# Set env
export HOME="$HOME"
export PYTHONPATH="$HOME/nexus"
export NEXUS_MCP_SYNC=true
export OLLAMA_HOST=${OLLAMA_HOST:-localhost:11434}

# Run
exec "$HOME/nexus/venv/bin/python" -m uvicorn server.unified:app \
    --host 0.0.0.0 --port 9999 --workers 1 --reload 2>&1 | tee -a "$HOME/nexus/nexus.log"
