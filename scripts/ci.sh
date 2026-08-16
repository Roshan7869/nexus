#!/usr/bin/env bash
# NEXUS CI — three gates: tests, offline eval, (optional) smoke.
# Exit codes wired for CI; smoke skipped without an API key (post-pivot).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=./venv/bin/python
FAIL=0

echo "== gate 1: test suite =="
$PY -m pytest tests/ -q || FAIL=1

echo "== gate 2: offline eval (route_match gate vs baseline when present) =="
BASELINE=$(ls -t evals/results/*_baseline*.jsonl 2>/dev/null | head -1)
if [ -n "$BASELINE" ]; then
  THRESH=$($PY -c "import json,sys; print(json.loads(open('$BASELINE').readline())['pass_rate'])" 2>/dev/null || echo "")
  if [ -n "$THRESH" ]; then
    echo "baseline pass_rate=$THRESH — enforcing --fail-below"
    $PY evals/run_eval.py --tag ci --fail-below "$THRESH" || FAIL=1
  else
    $PY evals/run_eval.py --tag ci || FAIL=1
  fi
else
  echo "no baseline archived yet — running eval without threshold"
  $PY evals/run_eval.py --tag ci || FAIL=1
fi

echo "== gate 3: smoke (optional — needs OLLAMA_CLOUD_API_KEY + gateway) =="
if [ -n "${OLLAMA_CLOUD_API_KEY:-}" ]; then
  ./scripts/smoke_e2e.sh || FAIL=1
else
  echo "SKIP: no OLLAMA_CLOUD_API_KEY (optional since pivot)"
fi

[ "$FAIL" -eq 0 ] && echo "CI GREEN" || echo "CI RED"
exit "$FAIL"
