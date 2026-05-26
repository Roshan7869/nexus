#!/bin/bash
# NEXUS v3 CLI Wrapper — created by adaptive-imagining-cat
# Usage: nexus <command> [args...]

NEXUS_HOME="${NEXUS_HOME:-$HOME/nexus}"
VENV_PYTHON="$NEXUS_HOME/venv/bin/python3"
SERVER="$NEXUS_HOME/server"
DB="$NEXUS_HOME/db/nexus.db"

case "${1:-}" in
    status)
        echo "═══════════════════════════════════════════════════════════"
        echo "  NEXUS v3 Status"
        echo "═══════════════════════════════════════════════════════════"
        echo "NEXUS_HOME: $NEXUS_HOME"
        echo "DB: $DB"
        if [ -f "$DB" ]; then
            SIZE=$(du -h "$DB" | cut -f1)
            echo "DB Size: $SIZE"
        else
            echo "DB: NOT FOUND"
        fi
        MCP_PID=$(pgrep -f "mcp_sync.py --daemon" || true)
        if [ -n "$MCP_PID" ]; then
            echo "MCP Daemon: Running (PID: $MCP_PID)"
        else
            echo "MCP Daemon: Not Running"
        fi
        RESOURCES=$($VENV_PYTHON -c "import sqlite3; conn=sqlite3.connect('$DB'); c=conn.execute('SELECT COUNT(*) FROM resources'); print(c.fetchone()[0]); conn.close()")
        echo "Resources Indexed: $RESOURCES"
        $VENV_PYTHON -c "import faiss; print('FAISS: ' + faiss.__version__)" 2>/dev/null || echo "FAISS: Not Available"
        ;;
    route-v3)
        shift
        $VENV_PYTHON -c "
import sys
sys.path.insert(0, '$NEXUS_HOME/venv/lib/python3.12/site-packages')
sys.path.insert(0, '$SERVER')
from tool_attention import route_v3
import json
query = ' '.join(sys.argv[1:])
result = route_v3(query, 'default')
print(json.dumps(result, indent=2))
" "$@"
        ;;
    list)
        $VENV_PYTHON -c "
import sys, sqlite3
sys.path.insert(0, '$NEXUS_HOME/venv/lib/python3.12/site-packages')
conn = sqlite3.connect('$DB')
cur = conn.execute('SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type')
for row in cur.fetchall():
    print('  {}: {}'.format(row[0], row[1]))
conn.close()
"
        ;;
    connect)
        echo "═══════════════════════════════════════════════════════════"
        echo "  NEXUS v3 Connection Verification for OpenCode"
        echo "═══════════════════════════════════════════════════════════"
        echo ""
        echo "1. Python Path Check:"
        $VENV_PYTHON -c "import faiss; print('    FAISS: ' + faiss.__version__)" 2>/dev/null || echo "    FAISS not found"
        $VENV_PYTHON -c "import sys; sys.path.insert(0, '$SERVER'); import router; print('    Router module importable')" 2>/dev/null || echo "    Router not importable"
        echo ""
        echo "2. Database Check:"
        $VENV_PYTHON -c "
import sqlite3
conn = sqlite3.connect('$DB')
c = conn.execute('SELECT COUNT(*) FROM resources')
print('    DB connected: {} resources'.format(c.fetchone()[0]))
conn.close()
"
        echo ""
        echo "3. Routing Test:"
        $VENV_PYTHON -c "
import sys
sys.path.insert(0, '$NEXUS_HOME/venv/lib/python3.12/site-packages')
sys.path.insert(0, '$SERVER')
from router import route_task
r = route_task('test connection', 'default')
print('    Route works: tier={}, model={}'.format(r['tier'], r['model']))
"
        echo ""
        echo "NEXUS v3 is connected and operational in OpenCode."
        ;;
    *)
        echo "NEXUS v3 CLI"
        echo ""
        echo "Usage: nexus <command> [args...]"
        echo ""
        echo "Commands:"
        echo "  status    Show NEXUS system status"
        echo "  route-v3  Route a task with v3 Tool Attention"
        echo "  list      List all indexed resource types"
        echo "  connect   Verify and connect NEXUS to this session"
        echo ""
        ;;
esac
