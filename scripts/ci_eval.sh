#!/usr/bin/env bash
# NEXUS P5 CI eval — unit tests + ops gates (+ optional AB)
# Usage:
#   ./scripts/ci_eval.sh              # unit + ops metrics (no AB)
#   ./scripts/ci_eval.sh --ab         # + quick AB suite
#   ./scripts/ci_eval.sh --full-ab    # + full 20-query AB (slow)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/venv/bin/python3"
if [[ ! -x "$PY" ]]; then PY=python3; fi

echo "== unit tests =="
$PY -m pytest tests/test_execution_plane_p4.py \
  tests/test_harness_engine_p3.py \
  tests/test_catalog_lifecycle_p2.py \
  tests/test_p0_p1_learning_contract.py \
  tests/test_ops_metrics_p5.py \
  tests/test_router_cutover_phase7.py \
  -q --tb=line

echo "== ops eval =="
EXTRA=()
for a in "$@"; do
  EXTRA+=("$a")
done
# Default: fail on structural gates except never_triggered (campaign metric)
$PY scripts/run_ops_eval.py --json --out logs/ops_eval_ci.json "${EXTRA[@]}"

# Soft check: always require router_mode_match + zero_token + divergence
$PY - <<'PY'
import json, sys
from pathlib import Path
r = json.loads(Path("logs/ops_eval_ci.json").read_text())
gates = r["gates"]
hard = ["router_mode_match", "zero_token_active", "cluster_divergence", "db_ok"]
# terminal_close is hard if we have enough data
if r["routing_coverage"]["total_routes"] >= 100:
    hard.append("terminal_close_rate")
failed = [k for k in hard if not gates.get(k, {}).get("pass")]
print("hard gates:", {k: gates[k] for k in hard if k in gates})
if failed:
    print("FAILED hard gates:", failed)
    sys.exit(1)
print("CI hard gates PASS")
PY

echo "ci_eval OK"
