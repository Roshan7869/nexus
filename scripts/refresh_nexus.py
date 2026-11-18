#!/usr/bin/env python3
"""One-command NEXUS refresh: pull sources, re-absorb, embed, index, publish, sync.

This is the pipeline the catalogue was missing — a single entry point that turns
"the repos moved" into "the catalogue and its mirrors moved".

Stages (each skippable):
  1. pull      tracked clones: git pull --ff-only
  2. absorb    re-absorb every tracked local clone
  3. embed     backfill any missing/changed vectors
  4. reindex   re-cluster + rebuild graph edges
  5. obsidian  publish the full corpus to the vault
  6. wenlan    capture changed resources as memories (incremental)
  7. commit    git commit the vault if anything changed

    python scripts/refresh_nexus.py --dry-run
    python scripts/refresh_nexus.py
    python scripts/refresh_nexus.py --skip wenlan,commit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    print("PyYAML required", file=sys.stderr)
    raise SystemExit(2)

from server.db import get_db_path  # noqa: E402

REGISTRY = ROOT / "config" / "resource_sources.yaml"
VAULT = Path(os.environ.get("NEXUS_VAULT", r"C:\Users\khatr\Downloads\nexus"))
PY = sys.executable

STAGES = ["pull", "absorb", "enrich", "embed", "reindex", "fts", "obsidian", "commit"]


def run(script: str, args: list[str], timeout: int = 3600) -> tuple[int, str]:
    p = subprocess.run([PY, str(ROOT / "scripts" / script), *args],
                       capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def tracked_sources() -> list[str]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    return [name for name, e in (data.get("sources") or {}).items()
            if e.get("status") == "tracked"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip", default="", help="comma list of stages to skip")
    ap.add_argument("--only", default="", help="comma list of stages to run")
    args = ap.parse_args()

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    def want(stage: str) -> bool:
        if only:
            return stage in only
        return stage not in skip

    if args.dry_run:
        print("would run stages:", [s for s in STAGES if want(s)])
        print("tracked sources  :", tracked_sources())
        print("vault            :", VAULT, "(git repo)" if (VAULT / ".git").is_dir() else "(no git)")
        return 0

    t0 = time.perf_counter()
    report: dict[str, str] = {}

    if want("pull"):
        sources = ",".join(tracked_sources())
        rc, out = run("update_resources.py", ["--pull"])
        report["pull"] = out.strip().splitlines()[-1] if out.strip() else f"rc={rc}"

    if want("absorb"):
        sources = tracked_sources()
        rc, out = run("update_resources.py", ["--absorb", ",".join(sources)])
        lines = [l.strip() for l in out.splitlines() if " new," in l]
        report["absorb"] = "; ".join(lines) if lines else f"rc={rc}"

    if want("enrich"):
        rc, out = run("enrich_tools.py", [])
        keep = [l for l in out.splitlines() if l.startswith(("enriched", "skipped"))]
        report["enrich"] = " | ".join(k.strip() for k in keep) if keep else f"rc={rc}"

    if want("embed"):
        # --force: enrichment rewrites body_text in place, so "missing vectors only"
        # would leave stale embeddings behind.
        rc, out = run("backfill_embeddings.py", ["--force"])
        keep = [l for l in out.splitlines() if "embedded" in l or "to embed" in l]
        report["embed"] = " | ".join(k.strip() for k in keep)

    if want("reindex"):
        for script, sargs in (("seed_clusters.py", ["--iterate", "1"]),
                              ("build_edges.py", ["--threshold", "0.55"])):
            rc, out = run(script, sargs)
            report.setdefault("reindex", "")
            report["reindex"] += f"{script}={'ok' if rc == 0 else 'FAIL'} "

    if want("fts"):
        # Must follow absorb/enrich: those rewrite body_text, which the index mirrors.
        rc, out = run("reindex_fts.py", [])
        keep = [l for l in out.splitlines() if "resources_fts" in l]
        report["fts"] = " | ".join(k.strip() for k in keep) if keep else f"rc={rc}"

    if want("obsidian"):
        rc, out = run("publish_resources.py", ["--only", "obsidian"])
        keep = [l for l in out.splitlines() if "written" in l or "failed" in l]
        report["obsidian"] = " | ".join(k.strip() for k in keep)

    if want("wenlan"):
        rc, out = run("sync_wenlan.py", [])
        keep = [l for l in out.splitlines() if "captured" in l or "nothing to sync" in l]
        report["wenlan"] = " | ".join(k.strip() for k in keep)

    if want("commit") and (VAULT / ".git").is_dir():
        add = subprocess.run(["git", "add", "-A"], cwd=str(VAULT), capture_output=True, text=True)
        diff = subprocess.run(["git", "status", "--porcelain"], cwd=str(VAULT),
                              capture_output=True, text=True)
        if diff.stdout.strip():
            msg = f"Refresh: {len(diff.stdout.strip().splitlines())} files changed"
            subprocess.run(["git", "-c", "user.name=NEXUS", "-c", "user.email=nexus@local",
                            "commit", "-q", "-m", msg], cwd=str(VAULT),
                           capture_output=True, text=True)
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(VAULT),
                                  capture_output=True, text=True).stdout.strip()
            report["commit"] = f"{msg} @ {head}"
        else:
            report["commit"] = "nothing to commit"

    elapsed = time.perf_counter() - t0
    print(f"\nrefresh complete in {elapsed:.1f}s")
    for stage in STAGES:
        if stage in report:
            print(f"  {stage:9} {report[stage][:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
