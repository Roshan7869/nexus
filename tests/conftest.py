# conftest.py — set up sys.path BEFORE any test imports run
#
# CRITICAL: Only add NEXUS_ROOT to sys.path. Python resolves:
#   `from core.server import app`  →  NEXUS_ROOT/core/server.py   ✓
#   `from server.router import ..` →  NEXUS_ROOT/server/router.py ✓
#   `from providers.registry`      →  NEXUS_ROOT/providers/..     ✓
#
# Do NOT add nexus/core/ or nexus/server/ to sys.path — doing so makes
# Python treat 'core' as a namespace package (directory) and breaks
# `from core.xxx` imports (Python then looks for core/xxx/ not core/xxx.py).

import sys
from pathlib import Path

NEXUS_ROOT = Path("/home/roshan/nexus")
sys.path.insert(0, str(NEXUS_ROOT))   # nexus root only — all packages resolved from here

import os
os.environ.setdefault("NEXUS_THOMPSON_ROUTING", "false")
os.environ.setdefault("NEXUS_CLUSTER_ROUTING", "false")
os.environ.setdefault("NEXUS_REFLECTION", "false")
os.environ.setdefault("NEXUS_LEARNING", "false")

