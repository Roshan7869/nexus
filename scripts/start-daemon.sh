#!/usr/bin/env bash
# NEXUS Daemon Wrapper — activates venv then runs the HTTP daemon
cd /home/roshan/nexus || exit 1
source venv/bin/activate
PYTHONUNBUFFERED=1 exec python -m server.nexus_http_daemon "$@"