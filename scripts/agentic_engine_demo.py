#!/usr/bin/env python3
"""Run one agentic harness turn with the default tool bindings.

    python scripts/agentic_engine_demo.py "survey retrieval augmented generation"
    python scripts/agentic_engine_demo.py --tool echo "hello"

The demo uses a deterministic scripted policy (no LLM) so it works offline and
prints the engine's audit trail: steps, observations, and an honest outcome that
stays `unverified` because nothing scored the result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.local_harness.engine import Decision, LocalHarnessEngine, ToolCall  # noqa: E402
from server.local_harness.toolbox import build_default_tools  # noqa: E402


def make_policy(tool: str, arguments: dict):
    """Call `tool` once, then finish. Deterministic; no model involved."""

    def decide(ctx):
        if ctx.steps:
            return Decision("finish", summary=f"ran {tool} once")
        return Decision("tool", call=ToolCall(tool, arguments))

    return decide


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--tool", default="research",
                    choices=["research", "browser_status", "echo"])
    ap.add_argument("--project", default="demo")
    ap.add_argument("--max-steps", type=int, default=4)
    ap.add_argument("--no-report", action="store_true",
                    help="skip the learning/report step (no DB required)")
    args = ap.parse_args()

    arguments = {"query": args.task, "text": args.task}
    engine = LocalHarnessEngine(
        decide=make_policy(args.tool, arguments),
        tools=build_default_tools(),
        max_steps=args.max_steps,
        report=not args.no_report,
    )

    result = engine.run(args.task, project=args.project)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
