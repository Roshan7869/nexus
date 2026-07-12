#!/usr/bin/env bash
# Hermes OS adapter — open/report NEXUS execution sessions (P4)
# Usage:
#   export NEXUS_URL=http://127.0.0.1:8080
#   ./nexus_session.sh open "fix login bug" [project]
#   ./nexus_session.sh report <session_id> correct "tests passed"
set -euo pipefail
BASE="${NEXUS_URL:-http://127.0.0.1:8080}"
CMD="${1:-}"
shift || true

case "$CMD" in
  health)
    curl -sS "$BASE/v1/health" | python3 -m json.tool
    ;;
  open)
    TASK="${1:?task required}"
    PROJECT="${2:-default}"
    curl -sS -X POST "$BASE/v1/session/open" \
      -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json; print(json.dumps({'task':'''$TASK''','project':'''$PROJECT''','ide':'hermes','budget':8}))")" \
      | python3 -m json.tool
    ;;
  report)
    SID="${1:?session_id}"
    OUTCOME="${2:?outcome}"
    NOTES="${3:-}"
    curl -sS -X POST "$BASE/v1/session/$SID/report" \
      -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json; print(json.dumps({'outcome':'''$OUTCOME''','notes':'''$NOTES'''}))")" \
      | python3 -m json.tool
    ;;
  *)
    echo "Usage: $0 {health|open <task> [project]|report <session_id> <outcome> [notes]}"
    exit 1
    ;;
esac
