#!/bin/bash
# Start NEXUS MCP Sync Daemon (Phase 8)
# Installed: /home/roshan/nexus/scripts/start-mcp-sync.sh
VENV_PYTHON="/home/roshan/nexus/venv/bin/python3"
SYNC_SCRIPT="/home/roshan/nexus/server/mcp_sync.py"
PID_FILE="/tmp/nexus-mcp-sync.pid"
LOG_FILE="/home/roshan/nexus/logs/mcp-sync.log"

mkdir -p "$(dirname "$LOG_FILE")"

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "MCP Sync already running (PID: $(cat "$PID_FILE"))"
        exit 0
    fi
    echo "Starting NEXUS MCP Sync Daemon..."
    nohup "$VENV_PYTHON" "$SYNC_SCRIPT" --daemon >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Started (PID: $(cat "$PID_FILE"))"
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "No PID file found"
        exit 0
    fi
    PID=$(cat "$PID_FILE")
    echo "Stopping MCP Sync (PID: $PID)..."
    kill "$PID" 2>/dev/null
    rm -f "$PID_FILE"
    echo "Stopped"
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "MCP Sync is running (PID: $(cat "$PID_FILE"))"
        tail -5 "$LOG_FILE" 2>/dev/null
    else
        echo "MCP Sync is not running"
    fi
}

case "${1:-status}" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" ;;
esac
