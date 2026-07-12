#!/usr/bin/env python3
"""CLI entry: python scripts/run_catalog_lifecycle.py [--apply] [--no-faiss]"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.catalog_lifecycle import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
