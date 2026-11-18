"""Small MCP-backed NEXUS skill harness for IDE-local work."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class NexusHarness:
    def __init__(self, project: str, git_root: str, query: str, ide: str = "codex",
                 session: ClientSession | None = None):
        self.project, self.git_root, self.query, self.ide = project, git_root, query, ide
        self.session = session
        self._stack = AsyncExitStack()
        self.task_session_id = ""
        self.capability_session_id = ""
        self.skill_id = ""
        self.skill_version = ""
        self.outcome = "partial"
        self.summary = "Skill retrieved; caller has not verified an operation."
        self.evidence: dict[str, Any] = {}
        self.attached = False

    async def __aenter__(self):
        try:
            if self.session is None:
                env = os.environ.copy()
                env.setdefault("NEXUS_DAEMON_URL", "http://127.0.0.1:8000")
                env["NEXUS_IDE"] = self.ide
                script = Path(__file__).resolve().parents[2] / "server" / "nexus-mcp-enhanced.py"
                params = StdioServerParameters(command=str(Path(__file__).resolve().parents[2] / "venv/bin/python3"),
                                               args=[str(script)], env=env)
                streams = await self._stack.enter_async_context(stdio_client(params))
                self.session = await self._stack.enter_async_context(
                    ClientSession(*streams, read_timeout_seconds=timedelta(seconds=180)))
            await asyncio.wait_for(self.session.initialize(), 180)
            available = await asyncio.wait_for(self.session.list_tools(), 180) if self._stack else None
            required = {"project_attach", "project_resume", "project_note", "task_start", "task_complete",
                        "capability_session_open", "capability_session_close", "capability_get", "skill_read"}
            missing = required - {tool.name for tool in available.tools} if available else set()
            if missing:
                raise RuntimeError(f"MCP server is missing required tools: {sorted(missing)}")
            await self.call("project_attach", {"project": self.project, "git_root": self.git_root})
            self.attached = True
            await self.call("project_resume", {"project": self.project})
            started = await self.call("task_start", {"query": self.query, "project": self.project, "ide": self.ide})
            self.task_session_id = started["session_id"]
            opened = await self.call("capability_session_open", {"project": self.project, "harness_id": self.ide})
            self.capability_session_id = opened["session"]["session_id"]
            self.outcome = "partial"
        except BaseException as error:
            await self.__aexit__(type(error), error, error.__traceback__)
            raise
        return self

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.wait_for(self.session.call_tool(name, arguments), 180)
        if getattr(result, "isError", False):
            raise RuntimeError(f"MCP {name} failed")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            data = structured
            if isinstance(data, dict) and isinstance(data.get("result"), str):
                try:
                    data = json.loads(data["result"])
                except json.JSONDecodeError:
                    pass
        else:
            data = {}
            for block in getattr(result, "content", []):
                if getattr(block, "text", None):
                    try:
                        data = json.loads(block.text)
                        break
                    except json.JSONDecodeError:
                        continue
        if not isinstance(data, dict) or data.get("error"):
            raise RuntimeError(f"MCP {name} returned an error: {data}")
        return data

    async def read_skill(self, skill_id: str) -> dict[str, Any]:
        descriptor = await self.call("capability_get", {"capability_id": skill_id})
        desc = descriptor["descriptor"]
        self.skill_id, self.skill_version = skill_id, desc["version"]
        content = await self.call("skill_read", {"capability_id": skill_id,
                                                   "version": self.skill_version, "path": "SKILL.md"})
        return content["skill"]

    def record(self, outcome: str, summary: str, evidence: dict[str, Any] | None = None):
        if outcome not in {"correct", "wrong", "partial", "skip"}:
            raise ValueError("outcome must be correct, wrong, partial, or skip")
        self.outcome, self.summary, self.evidence = outcome, summary, evidence or {}

    async def __aexit__(self, exc_type, exc, tb):
        failures = []
        if exc is not None:
            self.outcome = "partial"
        note = {"skill": self.skill_id, "version": self.skill_version, "outcome": self.outcome,
                "next_step": self.summary, "task_session_id": self.task_session_id,
                "capability_session_id": self.capability_session_id, "evidence": self.evidence}
        if self.capability_session_id:
            try:
                await self.call("capability_session_close", {"session_id": self.capability_session_id,
                    "outcome": self.outcome, "notes": json.dumps(note, sort_keys=True)})
            except Exception as error:
                failures.append(error)
        if self.task_session_id:
            try:
                await self.call("task_complete", {"session_id": self.task_session_id,
                    "outcome": self.outcome, "summary": json.dumps(note, sort_keys=True)})
            except Exception as error:
                failures.append(error)
        if self.attached:
            try:
                await self.call("project_note", {"project": self.project, "text": json.dumps(note, sort_keys=True)})
            except Exception as error:
                failures.append(error)
        try:
            await self._stack.aclose()
        except Exception as error:
            failures.append(error)
        if failures and exc is None:
            raise RuntimeError("NEXUS cleanup failed: " + "; ".join(map(str, failures)))
        if failures and exc is not None and hasattr(exc, "add_note"):
            exc.add_note("NEXUS cleanup failures: " + "; ".join(map(str, failures)))
        return False


async def _cli(args):
    result = None
    async with NexusHarness(args.project, args.git_root, args.query, args.ide) as harness:
        await harness.read_skill(args.skill)
        result = {"execution_mode": "skill_only", "skill": harness.skill_id,
                  "version": harness.skill_version}
        if args.check_repo:
            proc = await asyncio.to_thread(subprocess.run, ["git", "status", "--short"],
                                           cwd=args.git_root, text=True, capture_output=True,
                                           check=False, timeout=15)
            result["execution_mode"] = "host_callback"
            lines = proc.stdout.splitlines()
            result["evidence"] = {"operation": "git status --short", "returncode": proc.returncode,
                                  "entry_count": len(lines), "entries": lines[:5],
                                  "truncated": len(lines) > 5,
                                  "stderr": proc.stderr[:500]}
            harness.evidence = result["evidence"]
            harness.record("correct" if proc.returncode == 0 else "wrong",
                           "Bounded repository status callback completed.", result["evidence"])
    if result.get("evidence", {}).get("returncode", 0):
        print(json.dumps(result, indent=2))
        raise SystemExit(result["evidence"]["returncode"])
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--git-root", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--ide", default="codex")
    parser.add_argument("--check-repo", action="store_true")
    asyncio.run(_cli(parser.parse_args()))


if __name__ == "__main__":
    main()
