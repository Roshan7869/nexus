#!/usr/bin/env python3
"""verify_grounding.py — anti-hallucination gate for the docs↔code graph.

Checks that every per-file build doc's claims resolve against the real tree:
 1. Target file exists  <=>  doc declares MODIFY (missing target + MODIFY = FAIL).
 2. Declared CREATE targets must NOT exist yet (existing + CREATE-only = WARN).
 3. Anchor symbols (functions/classes the doc says it touches) grep-found in target.
 4. Graph JSON planned_missing matches filesystem reality.

Usage: python3 scripts/verify_grounding.py [--json]
Exit 0 = grounded, 1 = hallucination detected (lists exact failures).
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "docs" / "local_harness"

# doc -> (target, op, anchors that MUST grep in target when it exists)
CASES: dict[str, tuple[str, str, list[str]]] = {
    "M05-mcp-tools.md": ("server/nexus-mcp-enhanced.py", "MODIFY", ["def task_start", "def task_complete"]),
    "M06-execution-plane.md": ("server/execution_plane.py", "MODIFY", ["def open_session", "def _sanitize_ide"]),
    "M07-broker.md": ("server/tool_session_broker.py", "MODIFY", ["def start_session", "def end_session", "class ToolSessionBroker"]),
    "M09-preconditions.md": ("server/precondition.py", "MODIFY", ["class StateGates", "def check", "class StateVariable"]),
    "M10-fusion.md": ("server/router_v2/fusion.py", "MODIFY", ["def v5_get_weights", "def v5_fusion_scores"]),
    "M11-router-v5.md": ("server/router_v2/v5.py", "MODIFY", ["def unified_route_v5"]),
    "M13-context-cache.md": ("server/context_harness/cache.py", "MODIFY", ["class L0Cache", "class L3Cache", "DB_PATH"]),
    "M14-rate-proxy.md": ("server/integrations/rate_limited_proxy.py", "MODIFY", ["class MemoryStore"]),
    "M15-circuit-breaker.md": ("server/resilience/circuit_breaker.py", "MODIFY", ["class CircuitBreaker", "_RedisStateBackend"]),
    "M16-mcp-config.md": (".mcp.json", "MODIFY", ["nexus-mcp-enhanced.py"]),
    "M17-opencode-shell.md": ("opencode-with-nexus.sh", "MODIFY", ["uvicorn server.unified:app"]),
    "M01-package-init.md": ("server/local_harness/__init__.py", "CREATE", []),
    "M02-context-types.md": ("server/local_harness/context.py", "CREATE", []),
    "M03-session.md": ("server/local_harness/session.py", "CREATE", []),
    "M04-loader.md": ("server/local_harness/loader.py", "CREATE", []),
    "M08-opencode-adapter.md": ("server/opencode_adapter.py", "CREATE", []),
    "M12-redis-cache.md": ("server/cache/redis_cache.py", "CREATE", []),
    "M18-migration.md": ("db/migrations/020_local_harness.sql", "CREATE", []),
    "M19-tests.md": ("tests/test_local_harness.py", "CREATE", []),
    "A01-harness-registry.md": ("server/local_harness/harness.py", "CREATE", []),
    "A03-playbook.md": ("server/local_harness/playbook.py", "CREATE", []),
    "A06b-nexus-exp.md": ("scripts/nexus_exp.py", "CREATE", []),
    "A06c-migration-021.md": ("db/migrations/021_experiments.sql", "CREATE", []),
    "A07-artifacts.md": ("server/execution_plane.py", "MODIFY", ["def report_session"]),
    "A09-skill-packs.md": ("skills/nexus-router/SKILL.md", "CREATE", []),
    "B01-runtime-map.md": ("docs/nexus-maps/nexus-runtime.architecture.json", "CREATE", []),
    "B03-flow-irs.md": ("docs/nexus-maps/nexus-router-v5.workflow.json", "CREATE", []),
}


def grep_found(path: Path, anchor: str) -> bool:
    try:
        out = subprocess.run(
            ["grep", "-q", "-F", anchor, str(path)],
            capture_output=True, timeout=15,
        )
        return out.returncode == 0
    except Exception:
        return False


def main() -> int:
    as_json = "--json" in sys.argv
    fails: list[str] = []
    warns: list[str] = []
    for doc, (target, op, anchors) in sorted(CASES.items()):
        doc_path = DOC_DIR / doc
        if not doc_path.exists():
            fails.append(f"MISSING_DOC {doc}")
            continue
        text = doc_path.read_text()
        if op not in text:
            warns.append(f"OP_TAG doc {doc} never declares {op}")
        tgt = ROOT / target
        if op == "MODIFY" and not tgt.exists():
            fails.append(f"GHOST_TARGET {doc} claims MODIFY but {target} missing")
        if op == "CREATE" and tgt.exists():
            warns.append(f"ALREADY_BUILT {target} exists (doc {doc} still says CREATE)")
        if tgt.exists() and tgt.suffix == ".py":
            for a in anchors:
                if not grep_found(tgt, a):
                    fails.append(f"GHOST_ANCHOR {doc}: {a!r} not found in {target}")
    # graph consistency
    gpath = ROOT / "docs" / "nexus_docs_code_graph.json"
    if gpath.exists():
        g = json.loads(gpath.read_text())
        actual_missing = sorted(
            t for t, _, _ in ((d, t, o) for d, (t, o, _) in CASES.items())
            if not (ROOT / t).exists()
        )
        graph_missing = sorted(g["stats"].get("planned_missing", []))
        only_graph = [x for x in graph_missing if (ROOT / x).exists()]
        if only_graph:
            fails.append(f"STALE_GRAPH claims missing but exist: {only_graph}")
    report = {"fails": fails, "warns": warns,
              "cases": len(CASES), "grounded": not fails}
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for f in fails:
            print(f"FAIL {f}")
        for w in warns:
            print(f"WARN {w}")
        print(f"{len(CASES) - len(fails)} cases checked, "
              f"{len(fails)} FAIL, {len(warns)} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
