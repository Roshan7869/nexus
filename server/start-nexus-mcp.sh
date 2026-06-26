#!/bin/bash
export PYTHONPATH="/home/roshan/nexus:$PYTHONPATH"
export NEXUS_DB_PATH="/home/roshan/nexus/db/nexus.db"
exec python3 /home/roshan/nexus/server/mcp_v2_server.py
