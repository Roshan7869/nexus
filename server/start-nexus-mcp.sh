#!/bin/bash
export PYTHONPATH="/home/roshan/nexus:/home/roshan/nexus/server${PYTHONPATH:+:$PYTHONPATH}"
export NEXUS_DB_PATH="${NEXUS_DB_PATH:-/home/roshan/nexus/db/nexus.db}"
export NEXUS_DAEMON_URL="${NEXUS_DAEMON_URL:-http://127.0.0.1:8080}"
export USE_UNIFIED_ROUTER="${USE_UNIFIED_ROUTER:-2}"
export NEXUS_THOMPSON_ROUTING="${NEXUS_THOMPSON_ROUTING:-true}"
export NEXUS_CLUSTER_ROUTING="${NEXUS_CLUSTER_ROUTING:-true}"
export NEXUS_REFLECTION="${NEXUS_REFLECTION:-true}"
exec /home/roshan/nexus/venv/bin/python3 /home/roshan/nexus/server/nexus-mcp-enhanced.py
