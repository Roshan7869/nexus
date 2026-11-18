#!/usr/bin/env python3
"""nexus_exp — git-worktree eval sweeps with frozen runs (cf. orx-git + cardinal rules).

Rules: freeze answered nodes (branch children for ideas); run command + env FIXED
contract inherited verbatim — vary committed code only; hyperparams in code/config
branches, never CLI sweeps; descend onto winners (repair/refill/promote/stop).

Default is dry-run. --apply creates worktrees and writes the 021 ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def slug(title: str) -> str:
    base = "".join(c.lower() if c.isalnum() else "-" for c in title).strip("-")
    digest = hashlib.sha256(title.encode()).hexdigest()[:6]
    return f"{base[:40]}-{digest}"


def create(title: str, base: str = "HEAD", apply: bool = False) -> str:
    """Create exp/<slug> branch + worktree. Dry-run returns slug only."""
    s = slug(title)
    if not apply:
        return s
    wt = REPO.parent / "runs" / s
    subprocess.run(["git", "worktree", "add", str(wt), "-b", f"exp/{s}", base],
                   cwd=str(REPO), check=True)
    return s


def run_fixed(s: str, cmd: list[str], apply: bool = False) -> dict:
    """Run the SAME command for every node. Dry-run echoes the contract."""
    record = {"slug": s, "cmd": cmd, "answered": False}
    if not apply:
        record["dry_run"] = True
        return record
    wt = REPO.parent / "runs" / s
    proc = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True, timeout=3600)
    record.update({"answered": True, "returncode": proc.returncode,
                   "tail": proc.stdout[-2000:] + proc.stderr[-2000:]})
    return record


def decide(record: dict, beats_parent: bool, round_open: bool) -> str:
    """repair / refill / promote / stop (cardinal rule 4)."""
    if not record.get("answered"):
        return "repair"
    if beats_parent:
        return "promote"
    if round_open:
        return "refill"
    return "stop"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["create", "decide"])
    ap.add_argument("--title", default="")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.action == "create":
        print(json.dumps({"slug": create(args.title, args.base, args.apply)}))
    else:
        print(json.dumps({"decision": "refill",
                          "note": "wire to RunRecord store in P3; default keeps round open"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
