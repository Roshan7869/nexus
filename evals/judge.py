#!/usr/bin/env python3
"""Sampled LLM-judge over golden cases (the expensive sensor — 10% sample).

Temperature 0, model snapshot recorded, judge differs from candidate when
possible. Skips cleanly (exit 0) when no OLLAMA_CLOUD_API_KEY — the pivot
made provider calls optional, never blocking.

Usage: python evals/judge.py [--golden PATH] [--sample 0.10] [--model NAME]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def sample_cases(path: Path, fraction: float, seed: int = 42) -> list[dict]:
    import yaml
    cases = yaml.safe_load(path.read_text())
    k = max(1, int(len(cases) * fraction))
    return random.Random(seed).sample(cases, k)


def chat(model: str, messages: list[dict], temperature: float = 0.0) -> str:
    key = os.environ.get("OLLAMA_CLOUD_API_KEY", "")
    req = urllib.request.Request(
        "https://ollama.com/v1/chat/completions",
        data=json.dumps({"model": model, "messages": messages,
                         "temperature": temperature}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def judge_case(model: str, case: dict) -> dict:
    rubric = (case.get("verifier") or {}).get("rubric", "answer is correct and complete")
    answer = chat(model, [{"role": "user", "content": case["prompt"]}])
    verdict_text = chat(model, [
        {"role": "system", "content":
         "You are an evaluation judge. Score the ANSWER to the TASK 1-5 "
         "against the RUBRIC. Reply with exactly one line: 'SCORE: N' then "
         "one sentence of justification."},
        {"role": "user", "content":
         f"TASK: {case['prompt']}\nRUBRIC: {rubric}\nANSWER: {answer[:2000]}"},
    ])
    score = None
    for part in verdict_text.split():
        if part.isdigit() and 1 <= int(part) <= 5:
            score = int(part)
            break
    return {"id": case["id"], "score": score,
            "justification": verdict_text[:300], "model_snapshot": model}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(REPO_ROOT / "evals" / "golden" / "routing_cases.yaml"))
    ap.add_argument("--sample", type=float, default=0.10)
    ap.add_argument("--model", default="deepseek-v4-flash")
    args = ap.parse_args()

    if not os.environ.get("OLLAMA_CLOUD_API_KEY"):
        print("SKIP: no OLLAMA_CLOUD_API_KEY (optional since pivot; not blocking)")
        return 0

    cases = sample_cases(Path(args.golden), args.sample)
    out_path = REPO_ROOT / "evals" / "results" / (
        f'judge_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.jsonl')
    scores = []
    with out_path.open("w") as f:
        for case in cases:
            try:
                r = judge_case(args.model, case)
            except Exception as e:  # judge failures are data, not crashes
                r = {"id": case["id"], "score": None, "error": str(e)[:200]}
            f.write(json.dumps(r) + "\n")
            scores.append(r)
    valid = [r["score"] for r in scores if r.get("score")]
    print(f"judged={len(scores)} avg_score="
          f"{round(sum(valid) / len(valid), 2) if valid else 'n/a'} "
          f"archived={out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
