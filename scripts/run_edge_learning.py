#!/usr/bin/env python3
"""Gap 3: co-trigger edge learning stats.

  python scripts/run_edge_learning.py
  python scripts/run_edge_learning.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.learning import edge_learning_stats  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Gap 3 edge learning stats")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    stats = edge_learning_stats()
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print("=== Gap 3 Edge Learning ===")
        print(stats)
        print(
            f"gate co_triggered_with_count={stats.get('co_triggered_with_count')} "
            f"(target ≥10) pass={stats.get('edge_learning_gate_pass')}"
        )
    return 0 if stats.get("edge_learning_gate_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())