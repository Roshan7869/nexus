#!/usr/bin/env python3
"""Offline eval runner — Week1 card 3.4.

Calls server.router_cutover.route_for_task(prompt, open_session=False) — the
same dispatch the MCP /find_skills and HTTP /route paths use — and scores each
golden case with evals.scorer. No provider calls; runs in seconds on CPU.

Usage:
    ./venv/bin/python evals/run_eval.py [--golden PATH] [--tag TAG] [--fail-below N]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

RESULTS_DIR = REPO_ROOT / "evals" / "results"


def load_cases(path: Path) -> list[dict]:
    import yaml
    cases = yaml.safe_load(path.read_text())
    if not isinstance(cases, list) or not cases:
        raise SystemExit(f"golden file {path} is empty or malformed")
    ids = [c.get("id") for c in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"duplicate case ids in {path}")
    return cases


def run(cases: list[dict], tag: str, golden_path: Path) -> dict:
    # Lazy import: pulls server deps (embedder cache etc.) — keep startup out
    # of scorer's pure surface.
    from server.router_cutover import route_for_task
    from evals.scorer import score_case, summarize

    results = []
    for case in cases:
        decision = route_for_task(case["prompt"], open_session=False)
        results.append(score_case(case, decision))

    summary = summarize(results)
    run_meta = {
        "tag": tag,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "golden": str(golden_path.relative_to(REPO_ROOT)),
        "router_mode": decision.get("router_mode") if results else None,
        **summary,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f'{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}_{tag}'
    out = RESULTS_DIR / f"{run_id}.jsonl"
    with out.open("w") as f:
        f.write(json.dumps(run_meta) + "\n")
        for r in results:
            f.write(json.dumps(r) + "\n")

    _print_table(results, run_meta, out)
    return run_meta


def _print_table(results: list[dict], meta: dict, out: Path) -> None:
    print(f"== NEXUS eval | tag={meta['tag']} | golden={meta['golden']} ==")
    header = f"{'id':<26} {'pass':<5} {'skill':<6} {'clstr':<6} {'tier':<5} {'ms':>7} top_skill"
    print(header)
    for r in results:
        def mark(v):
            return "-" if v is None else ("Y" if v else "N")
        lat = f"{r['latency_ms']:.0f}" if isinstance(r.get("latency_ms"), (int, float)) else "-"
        print(f"{str(r['id']):<26} {'Y' if r['passed'] else 'N':<5} "
              f"{mark(r['skill_match']):<6} {mark(r['cluster_ok']):<6} "
              f"{mark(r['tier_ok']):<5} {lat:>7} {r.get('top_skill')}")
    print(f"-- pass_rate={meta['pass_rate']} skill_match={meta['skill_match_rate']} "
          f"cluster_ok={meta['cluster_ok_rate']} avg_latency_ms={meta['avg_latency_ms']}")
    print(f"-- archived: {out.relative_to(REPO_ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(REPO_ROOT / "evals" / "golden" / "routing_cases.yaml"))
    ap.add_argument("--tag", default="dev")
    ap.add_argument("--fail-below", type=float, default=None,
                    help="exit 1 if pass_rate < this (0..1), e.g. --fail-below 0.8")
    args = ap.parse_args()

    golden_path = Path(args.golden)
    meta = run(load_cases(golden_path), args.tag, golden_path)

    if args.fail_below is not None and (meta["pass_rate"] or 0) < args.fail_below:
        print(f"FAIL: pass_rate {meta['pass_rate']} < {args.fail_below}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
