#!/usr/bin/env python3
"""Refresh NEXUS resources from their upstream sources.

Resolution is driven by `config/resource_sources.yaml`. For each source:

  tracked    git pull --ff-only, then re-absorb + embed any newly changed resources
  clonable   fetch (with --clone), absorb, embed
  untracked  reported — no verified upstream, cannot be refreshed
  protected  fetched only (never pulled: uncommitted work would be clobbered)

The point of this script is honesty: it reports what it *cannot* update instead of
pretending a snapshot is current.

    python scripts/update_resources.py                 # report only
    python scripts/update_resources.py --pull          # update tracked clones
    python scripts/update_resources.py --clone agent-skills --pull
    python scripts/update_resources.py --reindex       # re-cluster + rebuild edges
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required: uv pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

from server.db import get_db_path  # noqa: E402

REGISTRY = ROOT / "config" / "resource_sources.yaml"
WORKSPACE = ROOT.parent


def load_registry() -> dict:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    return data.get("sources") or {}


def git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=300)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def resolve_path(entry: dict) -> Path | None:
    raw = entry.get("local_path")
    if not raw:
        return None
    p = (ROOT / raw).resolve() if raw != "." else ROOT
    return p if p.exists() else None


def db_counts() -> dict[str, int]:
    import sqlite3
    con = sqlite3.connect(str(get_db_path()), timeout=30)
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT repo_name, count(*) FROM resources GROUP BY 1")}
    finally:
        con.close()


def report(registry: dict) -> dict[str, list[str]]:
    live = db_counts()
    buckets: dict[str, list[str]] = {"tracked": [], "clonable": [], "untracked": [],
                                     "protected": [], "missing_local": []}
    for name, entry in registry.items():
        status = entry.get("status", "untracked")
        n = live.get(name, 0)
        p = resolve_path(entry)
        if status in ("tracked", "protected") and p is None:
            buckets["missing_local"].append(f"{name} (local_path missing, {n} rows)")
            continue
        label = f"{name:26} rows={n:<4} upstream={entry.get('upstream') or '-'}"
        if status == "tracked":
            rc, out = git(["rev-parse", "--short", "HEAD"], p) if p else (1, "")
            behind = ""
            if p:
                git(["fetch", "--quiet", "origin"], p)
                rc2, out2 = git(["rev-list", "--count", "HEAD..@{u}"], p)
                behind = f" behind={out2}" if rc2 == 0 else ""
            buckets["tracked"].append(f"{label} head={out}{behind}")
        elif status == "protected":
            buckets["protected"].append(label)
        elif status == "clonable":
            buckets["clonable"].append(label)
        else:
            buckets["untracked"].append(label)
    return buckets


def pull_tracked(registry: dict) -> list[str]:
    notes = []
    for name, entry in registry.items():
        if entry.get("status") != "tracked":
            continue
        p = resolve_path(entry)
        if p is None:
            continue
        rc, out = git(["pull", "--ff-only"], p)
        notes.append(f"{name}: {'updated' if 'Already up to date' not in out else 'already current'} — {out.splitlines()[-1] if out else ''}")
    return notes


def absorb(names: list[str], registry: dict) -> list[str]:
    from server.ingest.repo_resources import absorb as do_absorb
    notes = []
    for name in names:
        entry = registry.get(name) or {}
        p = resolve_path(entry)
        if p is None:
            notes.append(f"{name}: no local path")
            continue
        summary = do_absorb(p, get_db_path(), repo_name=name)
        notes.append(f"{name}: +{summary['inserted']} new, {summary['updated']} updated, "
                     f"{summary['unchanged']} unchanged")
    return notes


def reindex() -> list[str]:
    out = []
    for script, args in (("seed_clusters.py", ["--iterate", "1"]),
                         ("build_edges.py", ["--threshold", "0.55"])):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                           capture_output=True, text=True, timeout=1800)
        tail = (r.stdout or r.stderr).strip().splitlines()
        out.append(f"{script}: {'ok' if r.returncode == 0 else 'FAILED'} "
                   f"{(tail[-1] if tail else '')[:90]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true", help="git pull tracked clones")
    ap.add_argument("--clone", default=None, help="clone+absorb one clonable source")
    ap.add_argument("--absorb", default=None, help="comma list of sources to re-absorb")
    ap.add_argument("--reindex", action="store_true", help="re-cluster + rebuild edges")
    args = ap.parse_args()

    registry = load_registry()
    print(f"registry: {REGISTRY} ({len(registry)} sources)")

    if args.clone:
        entry = registry.get(args.clone) or {}
        url = entry.get("upstream")
        if not url:
            print(f"error: {args.clone} has no verified upstream", file=sys.stderr)
            return 2
        dest = WORKSPACE / args.clone
        if dest.exists():
            print(f"  {dest} exists; pulling instead")
            git(["pull", "--ff-only"], dest)
        else:
            rc, out = git(["clone", "--depth", "1", url, str(dest)], WORKSPACE)
            print(f"  clone {args.clone}: {'ok' if rc == 0 else 'FAILED ' + out[:200]}")
            if rc != 0:
                return 1
        print("\n".join(absorb([args.clone], registry)))

    if args.pull:
        print("\n-- pulling tracked --")
        for n in pull_tracked(registry):
            print("  ", n)

    if args.absorb:
        print("\n-- re-absorbing --")
        for n in absorb([s.strip() for s in args.absorb.split(",") if s.strip()], registry):
            print("  ", n)

    if args.reindex:
        print("\n-- reindex --")
        for n in reindex():
            print("  ", n)

    print("\n-- source status --")
    buckets = report(registry)
    for kind in ("tracked", "protected", "clonable", "missing_local", "untracked"):
        items = buckets[kind]
        print(f"\n  {kind.upper()} ({len(items)})")
        for it in items:
            print("   ", it)

    updatable = len(buckets["tracked"]) + len(buckets["clonable"])
    total = len(registry)
    print(f"\nupdatable: {updatable}/{total} sources  |  "
          f"untracked (no upstream): {len(buckets['untracked'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
