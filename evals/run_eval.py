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

# Retrieval-arm mode (Phase 0/2, okf-native-cordis):
#   --arm chunks+hybrid    -> today's production: ingestion_chunks, FTS+semantic
#   --arm sections+hybrid  -> OKF resource_sections, FTS+semantic
#   --arm sections+lexical -> OKF resource_sections, pure FTS5 (D2 target arm)
ARM_MAP = {
    "chunks+hybrid":    ("chunks", True),
    "sections+hybrid":  ("sections", True),
    "sections+lexical": ("sections", False),
}


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


def run_retrieval(cases: list[dict], arms: list[str], tag: str, golden_path: Path,
                  out_name: str | None = None) -> dict:
    """Run one or more retrieval arms over the golden set; score recall@k + MRR.

    Single arm  -> evals/results/baseline_<arm>.json   (Phase 0 contract)
    2+ arms     -> evals/results/ab_okf_<YYYYMMDD>.json (Phase 2 contract)
    """
    import datetime as _dt
    import inspect
    from server.router_v2.chunk_retrieval import retrieve_chunks
    from evals.scorer import score_retrieval, summarize_retrieval

    # Phase 0 runs BEFORE Phase 1 adds retrieve_chunks(corpus=...). Use the
    # corpus kwarg only when the signature supports it; sections arms on a
    # pre-Phase-1 signature fail loudly (they cannot run there anyway).
    has_corpus = "corpus" in inspect.signature(retrieve_chunks).parameters

    payload: dict = {
        "tag": tag,
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "golden": str(golden_path.relative_to(REPO_ROOT)),
        "arms": {},
    }

    for arm in arms:
        if arm not in ARM_MAP:
            raise SystemExit(f"unknown arm {arm!r}; choose from {sorted(ARM_MAP)}")
        corpus, vec = ARM_MAP[arm]
        scored = []
        for case in cases:
            clusters = case.get("accept_clusters") or None
            try:
                kwargs: dict = dict(top_k=int(case.get("top_k", 5)),
                                    vec=vec, clusters=clusters)
                if has_corpus:
                    rc = retrieve_chunks(case["prompt"], corpus=corpus, **kwargs)
                elif corpus == "chunks":
                    rc = retrieve_chunks(case["prompt"], **kwargs)   # pre-Phase-1
                else:
                    raise RuntimeError("corpus= unsupported before Phase 1 "
                                       "(sections arm requires retrieve_chunks(corpus=...))")
                s = score_retrieval(case, rc.get("results") or [],
                                    default_top_k=int(case.get("top_k", 5)),
                                    latency_ms=rc.get("latency_ms"))
                s["used_semantic"] = rc.get("used_semantic")
            except Exception as exc:  # a crash is an unhealthy retrieval, not a pass
                s = {"id": case.get("id"), "healthy": False, "error": str(exc),
                     "recall_hit": None, "reciprocal_rank": None}
            scored.append(s)
        summary = summarize_retrieval(scored)
        payload["arms"][arm] = {
            "corpus": corpus, "vec": vec, **summary,
            "used_semantic_any": any(r.get("used_semantic") for r in scored),
            "cases": scored,
        }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if out_name is None:
        out_name = (f"baseline_{arms[0].replace('+', '_')}.json" if len(arms) == 1
                    else f"ab_okf_{_dt.datetime.now().strftime('%Y%m%d')}.json")
    out = RESULTS_DIR / out_name
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"== NEXUS retrieval-arm eval | tag={tag} | cases={len(cases)} ==")
    print(f"{'arm':<18} {'recall@k':>9} {'mrr':>7} {'p50_ms':>8} {'scored':>7} sem")
    for arm, a in payload["arms"].items():
        print(f"{arm:<18} {str(a['recall_at_k']):>9} {str(a['mrr']):>7} "
              f"{str(a['p50_ms']):>8} {a['scored']:>7} {a['used_semantic_any']}")
    print(f"-- archived: {out.relative_to(REPO_ROOT)}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(REPO_ROOT / "evals" / "golden" / "routing_cases.yaml"))
    ap.add_argument("--tag", default="dev")
    ap.add_argument("--fail-below", type=float, default=None,
                    help="exit 1 if pass_rate < this (0..1), e.g. --fail-below 0.8")
    ap.add_argument("--arm", action="append", default=None, choices=sorted(ARM_MAP),
                    help="retrieval-arm mode; repeatable: chunks+hybrid, sections+hybrid, "
                         "sections+lexical (omit to run the full-router eval instead)")
    ap.add_argument("--out", default=None,
                    help="explicit results filename (default: baseline_<arm>.json / ab_okf_<date>.json)")
    args = ap.parse_args()

    golden_path = Path(args.golden)
    if args.arm:
        run_retrieval(load_cases(golden_path), args.arm, args.tag, golden_path, args.out)
        return 0

    meta = run(load_cases(golden_path), args.tag, golden_path)

    if args.fail_below is not None and (meta["pass_rate"] or 0) < args.fail_below:
        print(f"FAIL: pass_rate {meta['pass_rate']} < {args.fail_below}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
