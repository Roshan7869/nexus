#!/usr/bin/env python3
"""One-trigger NEXUS catalogue update: parallel-pull every source repo, re-absorb.

What "update all resources" means here, concretely: 109 workspace clones hold the
upstream content behind the 5,067-resource catalogue. `update_resources.py` only
pulls the 6 sources listed as `tracked` in the registry — the other ~100 (mostly
Roshan7869's own repos, cloned by ingest_own_repos.py) were never refreshable.
This script pulls ALL of them.

Stages
------
  pull    SWARM: git pull --ff-only over every workspace git repo, 8 workers
          in parallel (network-bound). Protected repos (registry `protected`,
          e.g. `nexus` itself with uncommitted work) are skipped, never pulled.
  absorb  re-absorb only repos whose HEAD moved AND whose name maps to an
          existing resources.repo_name (serial: SQLite has one writer).
  enrich  enrich_tools.py — fill tool stubs from freshly updated clones.
  fts     reindex_fts.py — rebuild the resources_fts index over changed bodies.
  publish publish_resources.py --only obsidian — sync the vault.
  commit  git commit the vault if anything changed.
  embed   (full mode only) backfill_embeddings.py --force — heavy CPU.
  reindex (full mode only) seed_clusters + build_edges — heavy CPU.

Progress is written to D:/nexus-data/update_status.json after EVERY stage, so
`nexus_update_status()` (MCP) or any watcher can poll a live run.

    python scripts/update_all.py --check          # read-only: how far behind is each repo?
    python scripts/update_all.py                  # fast update (default)
    python scripts/update_all.py --mode full       # + embeddings + re-cluster + edges
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

STATUS_FILE = Path(r"D:\nexus-data\update_status.json")
LOG_DIR = Path(r"D:\nexus-data\logs")
WORKERS = 8
GIT_TIMEOUT = 300
#: --check only needs to know freshness; repos slower than this get reported,
#: not waited for (8 big-asset repos hung the first run past the terminal cap).
CHECK_TIMEOUT = 90

#: Private repos hang an unattended pull: no global credential helper is
#: configured, so git falls back to the interactive GCM selector, which can
#: never show UI headless. gh (already keyring-authed) serves credentials
#: non-interactively. Verified: private repo pull returns "Already up to date."
NET_ENV = {**os.environ,
           "GIT_TERMINAL_PROMPT": "0",     # refuse prompts instead of hanging
           "GCM_INTERACTIVE": "never",    # GCM: never open a window
           "GCM_GUI_PROMPT": "false"}
NET_ARGS = ["-c", "credential.helper=",
            "-c", "credential.helper=!gh auth git-credential"]

_t0 = time.time()
_log_fh = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    global _log_fh
    line = f"{_now()} {msg}"
    print(line, flush=True)
    if _log_fh is not None:
        try:
            _log_fh.write(line + "\n")
            _log_fh.flush()
        except OSError:
            pass


def _status_write(status: dict) -> None:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except OSError:
        pass  # progress reporting must never kill the run


def _git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT,
         network: bool = False) -> tuple[int, str]:
    """Run git with a timeout that actually works on Windows.

    `subprocess.run(timeout=...)` kills only the direct child — but
    `git fetch` spawns `git-remote-https.exe` which inherits the stdout pipe,
    so the parent blocks reading a pipe that never closes. Tree-kill via
    taskkill /T closes every handle, and communicate() returns. This was the
    bug that hung the first run at 100/108 for 7+ minutes.
    """
    p = None
    try:
        p = subprocess.Popen(
            ["git", *(NET_ARGS if network else []), *args], cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=NET_ENV if network else None)
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, (out or "").strip()
    except subprocess.TimeoutExpired:
        if p is not None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True)
            else:
                p.kill()
            try:
                p.communicate(timeout=10)
            except Exception:
                pass
        return 1, "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass
        return 1, str(exc)


def discover_repos() -> list[Path]:
    """Every workspace dir with a .git, excluding the nexus repo itself."""
    out = []
    for d in WORKSPACE.iterdir():
        if d.is_dir() and (d / ".git").exists():
            out.append(d)
    return sorted(out, key=lambda p: p.name.lower())


def protected_names() -> set[str]:
    """Registry `protected` entries — never pulled (uncommitted local work)."""
    try:
        import yaml
        reg = yaml.safe_load((ROOT / "config" / "resource_sources.yaml")
                             .read_text(encoding="utf-8")) or {}
        return {n for n, e in (reg.get("sources") or {}).items()
                if e.get("status") == "protected"}
    except Exception:
        return set()


def db_repo_names() -> dict[str, str]:
    """lower(dir-name) -> canonical resources.repo_name, for absorb mapping."""
    import sqlite3
    con = sqlite3.connect(str(get_db_path()), timeout=30)
    try:
        return {name.lower(): name for (name,) in
                con.execute("SELECT DISTINCT repo_name FROM resources")}
    finally:
        con.close()


# ── stage: swarm pull ──────────────────────────────────────────────────────────

def _pull_one(repo: Path) -> dict:
    name = repo.name
    rc_r, remote = _git(["remote", "get-url", "origin"], repo)
    if rc_r != 0:
        return {"repo": name, "result": "no_remote", "detail": remote[:120]}

    rc_h, head_before = _git(["rev-parse", "HEAD"], repo)
    rc, out = _git(["pull", "--ff-only", "--quiet"], repo, network=True)
    rc_a, head_after = _git(["rev-parse", "HEAD"], repo)

    if rc != 0:
        first = out.splitlines()[0][:160] if out else "unknown"
        return {"repo": name, "result": "failed", "detail": first,
                "changed": False}
    if head_before != head_after:
        return {"repo": name, "result": "updated", "detail": "",
                "head": head_after[:8], "changed": True}
    return {"repo": name, "result": "current", "detail": "", "changed": False}


def stage_pull(status: dict, check_only: bool = False) -> None:
    repos = discover_repos()
    protected = protected_names()
    todo = [r for r in repos if r.name.lower() not in {p.lower() for p in protected}]
    status["repos_total"] = len(repos)
    status["repos_skipped_protected"] = [r.name for r in repos
                                         if r not in todo]
    stage = status["stages"].setdefault("pull", {})
    stage.update(status="running", started=_now())
    _status_write(status)
    _log(f"pull: {len(todo)} repos to pull ({len(repos) - len(todo)} protected/skipped), "
         f"{WORKERS} workers")

    results: list[dict] = []
    stage["results"] = []
    if check_only:
        def probe(repo: Path) -> dict:
            _git(["fetch", "--quiet", "origin"], repo, timeout=CHECK_TIMEOUT,
                 network=True)
            rc, out = _git(["rev-list", "--count", "HEAD..@{u}"], repo, timeout=30)
            return {"repo": repo.name,
                    "result": "behind" if rc == 0 and out.strip("0 \n") else "current",
                    "detail": f"{out} behind" if rc == 0 else "no upstream"}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = [pool.submit(probe, r) for r in todo]
            done = 0
            for f in as_completed(futs):
                r = f.result()
                results.append(r)
                stage["results"].append(r)  # incremental: visible even if killed
                done += 1
                stage["progress"] = f"{done}/{len(todo)}"
                _status_write(status)
    else:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = [pool.submit(_pull_one, r) for r in todo]
            done = 0
            for f in as_completed(futs):
                r = f.result()
                results.append(r)
                stage["results"].append(r)  # incremental: visible even if killed
                done += 1
                stage["progress"] = f"{done}/{len(todo)}"
                _status_write(status)

    results.sort(key=lambda r: r["repo"].lower())
    buckets: dict[str, list[dict]] = {}
    for r in results:
        buckets.setdefault(r["result"], []).append(r)
    stage["counts"] = {k: len(v) for k, v in buckets.items()}
    stage["status"] = "done"
    stage["ms"] = int((time.time() - _t0) * 1000)
    status["pull_results"] = results
    status["changed_repos"] = [r["repo"] for r in results if r.get("changed")]
    _status_write(status)
    _log(f"pull done: {stage['counts']} | changed: "
         f"{len(status['changed_repos'])} | failed: "
         f"{[r['repo'] for r in buckets.get('failed', [])][:8]}")


# ── stage: absorb changed repos ───────────────────────────────────────────────

def stage_absorb(status: dict, check_only: bool = False) -> None:
    stage = status["stages"].setdefault("absorb", {})
    changed = status.get("changed_repos", [])
    if not changed:
        stage.update(status="skipped", reason="nothing changed")
        _status_write(status)
        _log("absorb: no repo moved — skipping")
        return
    mapping = db_repo_names()
    absorbable = []
    unmapped = []
    for name in changed:
        canon = mapping.get(name.lower())
        if canon:
            absorbable.append((WORKSPACE / name, canon))
        else:
            unmapped.append(name)

    stage.update(status="running", started=_now(),
                 repos=[c for _, c in absorbable])
    _status_write(status)
    _log(f"absorb: {len(absorbable)} repos to re-absorb"
         + (f" ({len(unmapped)} changed but no DB mapping: {unmapped[:5]}…)"
            if unmapped else ""))

    if check_only:
        stage.update(status="would_absorb", repos=[c for _, c in absorbable])
        _status_write(status)
        return

    from server.ingest.repo_resources import absorb as do_absorb
    db_path = str(get_db_path())
    ins = upd = 0
    for repo_dir, canon in absorbable:
        try:
            summary = do_absorb(repo_dir, db_path, repo_name=canon)
            ins += summary["inserted"]
            upd += summary["updated"]
            stage["last"] = f"{canon}: +{summary['inserted']} new, {summary['updated']} upd"
            _status_write(status)
            _log(f"  absorb {canon}: +{summary['inserted']} new, "
                 f"{summary['updated']} updated, {summary['unchanged']} unchanged")
        except Exception as exc:
            _log(f"  absorb {canon} FAILED: {type(exc).__name__}: {str(exc)[:150]}")
    stage.update(status="done", inserted=ins, updated=upd)
    status.setdefault("summary", {})["absorbed"] = {"new": ins, "updated": upd}
    _status_write(status)
    _log(f"absorb done: +{ins} new, {upd} updated")


# ── pipeline driver ────────────────────────────────────────────────────────────

def run_script(name: str, args: list[str], status: dict, stage_key: str) -> bool:
    stage = status["stages"].setdefault(stage_key, {})
    stage.update(status="running", started=_now())
    _status_write(status)
    t = time.time()
    try:
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args],
                           capture_output=True, text=True, timeout=3600,
                           cwd=str(ROOT))
        ok = p.returncode == 0
        tail = (p.stdout or p.stderr or "").strip().splitlines()
        stage.update(status="done" if ok else "FAILED",
                     ms=int((time.time() - t) * 1000),
                     tail=(tail[-1][:160] if tail else ""))
        _log(f"{stage_key}: {'ok' if ok else 'FAILED'} "
             f"({stage['ms'] / 1000:.1f}s) {stage['tail']}")
    except Exception as exc:
        ok = False
        stage.update(status="FAILED", error=f"{type(exc).__name__}: {str(exc)[:150]}")
        _log(f"{stage_key} FAILED: {stage['error']}")
    _status_write(status)
    return ok


def stage_commit(status: dict) -> None:
    """Commit the vault if the publish stage changed anything."""
    stage = status["stages"].setdefault("commit", {})
    vault = Path(r"C:\Users\khatr\Downloads\nexus")
    if not (vault / ".git").is_dir():
        stage.update(status="skipped", reason="no vault git")
        return
    _git(["add", "-A"], vault)
    rc, out = _git(["status", "--porcelain"], vault)
    if not out.strip():
        stage.update(status="done", detail="nothing to commit")
        _status_write(status)
        _log("commit: vault clean")
        return
    n = len(out.strip().splitlines())
    _git(["-c", "user.name=NEXUS", "-c", "user.email=nexus@local",
          "commit", "-q", "-m", f"Update: {n} files changed (auto refresh)"], vault)
    rc, head = _git(["rev-parse", "--short", "HEAD"], vault)
    stage.update(status="done", files=n, head=head)
    status.setdefault("summary", {})["vault_commit"] = f"{n} files @ {head}"
    _status_write(status)
    _log(f"commit: {n} files @ {head}")


def main() -> int:
    global _log_fh
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fast", "full"], default="fast")
    ap.add_argument("--check", action="store_true",
                    help="read-only freshness report (fetch + behind counts)")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    _log_fh = (LOG_DIR / f"update_all-{run_id}.log").open("w", encoding="utf-8")

    status = {
        "run_id": run_id,
        "mode": "check" if args.check else args.mode,
        "pid": os.getpid(),
        "started_at": _now(),
        "stages": {},
        "summary": {},
    }
    _status_write(status)
    _log(f"=== update_all run {run_id} mode={status['mode']} ===")

    stage_pull(status, check_only=args.check)
    stage_absorb(status, check_only=args.check)
    # S2: ingestion-time stale-marker lint (budgeted tracker resolution,
    # cached; offline-safe — flags pending, never blocks the pipeline)
    if not args.check:
        run_script("lint_stale_markers.py", [], status, "lint")
    if args.check:
        status["finished_at"] = _now()
        status["elapsed_s"] = round(time.time() - _t0, 1)
        _status_write(status)
        behind = [r for r in status["pull_results"] if r["result"] == "behind"]
        failed = [r for r in status["pull_results"] if r["result"] == "failed"]
        _log(f"check done: {len(behind)} behind, {len(failed)} failed, "
             f"{status['elapsed_s']}s")
        return 0

    run_script("enrich_tools.py", [], status, "enrich")
    if args.mode == "full":
        run_script("backfill_embeddings.py", ["--force"], status, "embed")
        run_script("seed_clusters.py", ["--iterate", "1"], status, "cluster")
        run_script("build_edges.py", ["--threshold", "0.55"], status, "edges")
    run_script("reindex_fts.py", [], status, "fts")
    run_script("publish_resources.py", ["--only", "obsidian"], status, "publish")
    stage_commit(status)

    status["finished_at"] = _now()
    status["elapsed_s"] = round(time.time() - _t0, 1)
    failed_stages = [k for k, v in status["stages"].items() if v.get("status") == "FAILED"]
    status["ok"] = not failed_stages
    _status_write(status)
    _log(f"=== done in {status['elapsed_s']}s | stages failed: "
         f"{failed_stages or 'none'} ===")
    return 0 if not failed_stages else 1


if __name__ == "__main__":
    raise SystemExit(main())
