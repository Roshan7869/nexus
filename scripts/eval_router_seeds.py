#!/usr/bin/env python3
"""E2: multi-seed golden-set eval — the accuracy ruler that gates every later phase.

Runs the 105-case golden set (evals/golden/routing_cases.yaml) against the live
router N times (default 3 seeds), scores via evals.scorer.score_case, and reports:

  * top-1 / top-3 / top-k hit rates on expect_skills
  * MRR (rank of the first expected skill)
  * cluster_ok / passed rates
  * per-case route latency p50/p95 — accuracy and latency on the SAME curve
  * bootstrap 95% CIs over cases (the noise floor that auto-rejects sub-noise wins)
  * per-seed deltas (real variance source: routing writes shift counters between seeds)

Receipt: evals/results/perf/golden-<tag>.json

    python scripts/eval_router_seeds.py --seeds 3
    python scripts/eval_router_seeds.py --seeds 3 --tag after-e3
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

import yaml  # noqa: E402

from evals.scorer import score_case  # noqa: E402

GOLDEN = ROOT / "evals" / "golden" / "routing_cases.yaml"
OUT_DIR = ROOT / "evals" / "results" / "perf"


def load_cases() -> list[dict]:
    data = yaml.safe_load(GOLDEN.read_text(encoding="utf-8")) or {}
    cases = data.get("cases") if isinstance(data, dict) else data
    return [c for c in (cases or []) if isinstance(c, dict) and c.get("prompt")]


def run_seed(cases: list[dict]) -> list[dict]:
    from server.router_cutover import route_for_task
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        try:
            decision = route_for_task(case["prompt"])
            latency = (time.perf_counter() - t0) * 1000.0
            decision = dict(decision or {})
            decision["latency_ms"] = decision.get("latency_ms") or latency
        except Exception as exc:
            decision = {"skills": [], "error": f"{type(exc).__name__}: {exc}"}
            decision["latency_ms"] = (time.perf_counter() - t0) * 1000.0
        scored = score_case(case, decision)
        scored["route_latency_ms"] = round(decision.get("latency_ms") or 0.0, 1)
        scored["error"] = decision.get("error")
        # MRR: rank of first expected skill in the full returned list
        if case.get("expect_skills"):
            skills = [s.get("name") for s in (decision.get("skills") or [])]
            expected = set(case["expect_skills"])
            rr = 0.0
            for i, name in enumerate(skills, 1):
                if name in expected:
                    rr = 1.0 / i
                    break
            scored["mrr"] = rr
            scored["top1"] = bool(skills and skills[0] in expected)
            k3 = skills[:3]
            scored["top3"] = any(n in expected for n in k3)
        rows.append(scored)
    return rows


def boot_ci(values: list[float], n: int = 2000, alpha: float = 0.05) -> tuple[float, float, float]:
    """Bootstrap mean CI. values are 0/1 indicators."""
    vals = [v for v in values if v is not None]
    if not vals:
        return (0.0, 0.0, 0.0)
    mean = sum(vals) / len(vals)
    rng = random.Random(42)
    means = []
    for _ in range(n):
        sample = [vals[rng.randrange(len(vals))] for _ in range(len(vals))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(n * alpha / 2)]
    hi = means[min(n - 1, int(n * (1 - alpha / 2)))]
    return (round(mean, 4), round(lo, 4), round(hi, 4))


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    return round(sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))], 1)


def summarize_seed(rows: list[dict]) -> dict:
    skill_rows = [r for r in rows if r.get("skill_match") is not None]
    lat = sorted(r["route_latency_ms"] for r in rows if r.get("route_latency_ms"))
    return {
        "n_cases": len(rows),
        "n_skill_cases": len(skill_rows),
        "hit_at_k": boot_ci([1.0 if r["skill_match"] else 0.0 for r in skill_rows]),
        "top1": boot_ci([1.0 if r.get("top1") else 0.0 for r in skill_rows]),
        "top3": boot_ci([1.0 if r.get("top3") else 0.0 for r in skill_rows]),
        "mrr": round(statistics.fmean(r["mrr"] for r in skill_rows), 4) if skill_rows else None,
        "cluster_ok": boot_ci([1.0 if r.get("cluster_ok") else 0.0
                               for r in rows if r.get("cluster_ok") is not None]),
        "passed": boot_ci([1.0 if r.get("passed") else 0.0 for r in rows]),
        "healthy": boot_ci([1.0 if r.get("healthy") else 0.0 for r in rows]),
        "latency_p50_ms": pct(lat, 0.50),
        "latency_p95_ms": pct(lat, 0.95),
        "errors": sum(1 for r in rows if r.get("error")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--golden", default=str(GOLDEN))
    ap.add_argument("--hermetic", action="store_true", default=False,
                    help="route against a throw-away COPY of the live DB so this "
                         "eval's routing_log writes cannot change N_total/UCB for "
                         "later runs (see the E4b measurement-integrity note)")
    args = ap.parse_args()

    # ── hermetic mode ────────────────────────────────────────────────────
    # MUST run before any server import: router.py resolves DB_PATH at import.
    if args.hermetic:
        import os
        import sqlite3
        import tempfile
        # Reference precedence: explicit env > frozen reference > live DB.
        # Copying from the LIVE db is not reproducible: routing_log grows with
        # every route (114 -> 3,469 across this session) and N_total feeds the
        # UCB term, so "same corpus" runs still scored 0.274 / 0.286. All
        # measurements must come from ONE frozen reference.
        ref = Path(os.environ.get("NEXUS_EVAL_REFERENCE") or
                   r"D:\nexus-data\db\nexus_eval_reference.db")
        live = Path(os.environ.get("NEXUS_DB_PATH") or
                    Path.home() / "nexus" / "db" / "nexus.db")
        if not live.exists():
            live = Path(r"D:\nexus-data\db\nexus.db")
        src = ref if ref.exists() else live
        tmp = Path(tempfile.gettempdir()) / "nexus_eval_hermetic.db"
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(tmp) + suffix)
            if f.exists():
                f.unlink()
        # Use the SQLite backup API, NOT a file copy: copying a live WAL-mode
        # DB file-by-file can snapshot a torn state (observed 2026-11-19: a copy
        # missing the vector lane produced a bogus 0.155 result). backup() reads
        # through WAL and yields a consistent database.
        _s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
        _d = sqlite3.connect(str(tmp), timeout=60)
        try:
            _s.backup(_d)
        finally:
            _d.close()
            _s.close()
        # sanity: the snapshot must carry the vector lane the eval depends on
        _chk = sqlite3.connect(str(tmp), timeout=30)
        try:
            _n = _chk.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
        except sqlite3.Error:
            _n = 0
        finally:
            _chk.close()
        if _n == 0:
            print(f"hermetic ABORT: snapshot {tmp} has 0 chunk_embeddings "
                  f"(vector lane missing) — refusing to measure")
            return 2
        os.environ["NEXUS_DB_PATH"] = str(tmp)
        print(f"hermetic db  : {tmp}  (backup of {src}, {_n} chunk vectors)")

    cases = load_cases()
    print(f"golden set : {len(cases)} cases")
    print(f"seeds      : {args.seeds}")

    # Warm-up: pay the cold embedder load once, outside the measurement.
    from server.router_cutover import route_for_task
    t0 = time.perf_counter()
    route_for_task(cases[0]["prompt"])
    print(f"warm-up    : {(time.perf_counter() - t0) * 1000:.0f} ms")

    seed_summaries = []
    all_rows_by_seed = []
    for s in range(1, args.seeds + 1):
        t0 = time.perf_counter()
        rows = run_seed(cases)
        elapsed = time.perf_counter() - t0
        summary = summarize_seed(rows)
        seed_summaries.append(summary)
        all_rows_by_seed.append(rows)
        print(f"  seed {s}: hit@k={summary['hit_at_k'][0]:.3f} "
              f"top1={summary['top1'][0]:.3f} top3={summary['top3'][0]:.3f} "
              f"mrr={summary['mrr']} pass={summary['passed'][0]:.3f} "
              f"p50={summary['latency_p50_ms']}ms p95={summary['latency_p95_ms']}ms "
              f"({elapsed:.0f}s)")

    # Cross-seed deltas (state-feedback drift is the honest variance source)
    def drift(metric: str) -> str:
        vals = [s[metric][0] for s in seed_summaries if s.get(metric)]
        if not vals:
            return "n/a"
        return f"{min(vals):.3f}-{max(vals):.3f} (spread {max(vals) - min(vals):.3f})"

    print("\n== cross-seed drift (state feedback) ==")
    print(f"  hit@k : {drift('hit_at_k')}")
    print(f"  top1  : {drift('top1')}")

    # Worst cases (for the improvement backlog)
    flat = [r for rows in all_rows_by_seed for r in rows]
    from collections import defaultdict
    misses = defaultdict(int)
    for r in flat:
        if r.get("skill_match") is False:
            misses[r["id"]] += 1
    worst = sorted(misses.items(), key=lambda kv: -kv[1])[:10]

    receipt = {
        "tag": args.tag,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_cases": len(cases),
        "seeds": seed_summaries,
        "cross_seed_hit_spread": drift("hit_at_k"),
        "worst_cases": [{"id": k, "misses": v} for k, v in worst],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"golden-{args.tag}.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"\nreceipt    : {out}")
    print(f"worst cases: {[k for k, _ in worst[:6]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
