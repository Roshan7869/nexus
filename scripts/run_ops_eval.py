#!/usr/bin/env python3
"""P5 ops eval CLI — coverage, gates, optional A/B suite.

Usage:
  python scripts/run_ops_eval.py                  # metrics only
  python scripts/run_ops_eval.py --ab             # + quick 5-query AB
  python scripts/run_ops_eval.py --full-ab        # + full 20-query AB
  python scripts/run_ops_eval.py --fail-on-gate   # CI exit 1 on gate fail
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.ops_metrics import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
