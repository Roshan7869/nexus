#!/usr/bin/env python3
"""Gap 6: backfill chunk_embeddings.resource_id from ingestion_chunks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.chunk_resource_link import (  # noqa: E402
    backfill_chunk_resource_ids,
    chunk_resource_stats,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    before = chunk_resource_stats()
    out = {"before": before}
    if args.apply:
        out["backfill"] = backfill_chunk_resource_ids()
        out["after"] = chunk_resource_stats()
    else:
        out["after"] = before
        out["note"] = "dry_run — pass --apply to ALTER + UPDATE"
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print("=== Gap 6 chunk → resource_id ===")
        print(json.dumps(out, indent=2, default=str))
    a = out["after"]
    return 0 if a.get("chunk_resource_link_gate_pass") or not args.apply else 1


if __name__ == "__main__":
    raise SystemExit(main())
