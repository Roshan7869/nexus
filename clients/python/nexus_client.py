#!/usr/bin/env python3
"""Minimal NEXUS execution-plane client (P4).

Usage:
  from nexus_client import NexusClient
  c = NexusClient("http://127.0.0.1:8080")
  s = c.session_open("fix auth bug", project="kaari", ide="hermes")
  # ... agent runs with s["tools"] ...
  r = c.session_report(s["session_id"], "correct", notes="tests passed")
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class NexusClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {err_body}") from e

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def session_open(
        self,
        task: str,
        project: str = "default",
        *,
        budget: int = 8,
        ide: str = "http",
        use_embedding: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/session/open",
            {
                "task": task,
                "project": project,
                "budget": budget,
                "ide": ide,
                "use_embedding": use_embedding,
            },
        )

    def session_report(
        self,
        session_id: str,
        outcome: str,
        *,
        notes: str = "",
        quality: float = 1.0,
        skill_name: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "outcome": outcome,
            "notes": notes,
            "quality": quality,
        }
        if skill_name:
            body["skill_name"] = skill_name
        return self._request("POST", f"/v1/session/{session_id}/report", body)

    def session_get(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/session/{session_id}")

    def route(self, task: str, project: str = "default") -> dict[str, Any]:
        return self._request("POST", "/route", {"task": task, "project": project})


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="NEXUS execution-plane CLI")
    p.add_argument("--base", default="http://127.0.0.1:8080")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    op = sub.add_parser("open")
    op.add_argument("task")
    op.add_argument("--project", default="default")
    op.add_argument("--ide", default="http")
    op.add_argument("--budget", type=int, default=8)
    rp = sub.add_parser("report")
    rp.add_argument("session_id")
    rp.add_argument("outcome", choices=["correct", "wrong", "partial", "skip"])
    rp.add_argument("--notes", default="")
    rp.add_argument("--skill", default=None)
    args = p.parse_args()
    c = NexusClient(args.base)
    if args.cmd == "health":
        print(json.dumps(c.health(), indent=2))
    elif args.cmd == "open":
        print(json.dumps(
            c.session_open(args.task, args.project, budget=args.budget, ide=args.ide),
            indent=2,
        ))
    elif args.cmd == "report":
        print(json.dumps(
            c.session_report(
                args.session_id, args.outcome, notes=args.notes, skill_name=args.skill,
            ),
            indent=2,
        ))


if __name__ == "__main__":
    main()
