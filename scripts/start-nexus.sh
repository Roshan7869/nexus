#!/bin/bash
# ~/nexus/scripts/start-nexus.sh

PROJECT="${1:-default}"
LOG="$HOME/nexus/logs"
mkdir -p "$LOG"

sudo sysctl vm.swappiness=10 -q 2>/dev/null
export MALLOC_ARENA_MAX=2

if ! pgrep -x ollama > /dev/null; then
    echo "[nexus] Starting Ollama daemon..."
    ollama serve > "$LOG/ollama.log" 2>&1 &
    sleep 3
fi

AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "$AVAIL" -lt 2000 ]; then
    echo "⚠  Only ${AVAIL}MB free RAM."
fi

export NEXUS_PROJECT="$PROJECT"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " NEXUS  |  Project: $PROJECT  |  RAM: ${AVAIL}MB free"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Ollama Cloud → oc-$PROJECT       (OpenCode)"
echo " Anthropic    → cc                (Claude Code)"
echo ""
echo " Route first: nexus-find '<task description>'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"