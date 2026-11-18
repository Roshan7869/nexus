"""S4 acceptance — 30 frozen stale-vs-current conflict probes, A/B stamps.

Design gate (DISTRACTION-RESISTANT-CONTEXT.md §5): with provenance stamps,
>=90% of answers follow the CURRENT version (binomial CI reported).

Each probe packs a learning note pair into a routed context block:
  arm A (control): no stamps — the model cannot tell stale from current
                   (2606.26511: they are near-cosine-identical)
  arm B (stamps):  honest 'absorbed <date>' stamps mark the stale note older
The judge is any OpenAI-compatible chat endpoint:
  NEXUS_JUDGE_URL (default https://api.openai.com/v1)
  NEXUS_JUDGE_KEY (or OPENAI_API_KEY), NEXUS_JUDGE_MODEL (default gpt-4o-mini)
Without a key the script runs the deterministic presence check only
(every arm-B passage carries a dated stamp) and reports the judged half as
pending — no fabricated numbers.

    python evals/conflict_probes.py [--n 30] [--arm both|a|b]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 30 frozen probes: topic-agnostic, deterministic. stale_note is the OLDER
# claim (its absorbed stamp will be older in arm B); current_note supersedes
# it. correct_answer names what a current-following response must choose.
PROBES: list[dict] = [
    {"q": "Which linter config should the CI gate on?",
     "stale": "Gate CI on .eslintrc.json only — eslint-plugin is unreliable.",
     "current": "Gate CI on eslint.config.mjs (flat config); the legacy file is deprecated.",
     "correct": "eslint.config.mjs"},
    {"q": "Which Python version should the venv pin?",
     "stale": "Pin 3.12 for the service venv; 3.13 has wheel gaps.",
     "current": "Pin 3.13; the wheel gaps were fixed and 3.12 is in security-only mode.",
     "correct": "3.13"},
    {"q": "How should we batch SQLite writes in the ingest path?",
     "stale": "Commit after every row so partial failures never lose data.",
     "current": "One transaction per batch; per-row commits are 40x slower under WAL.",
     "correct": "transaction"},
    {"q": "Which FTS5 populate command rebuilds an external-content index?",
     "stale": "DELETE FROM the fts table then INSERT rows manually.",
     "current": "INSERT INTO x_fts(x_fts) VALUES('rebuild') — count(*) reads the content table.",
     "correct": "rebuild"},
    {"q": "What is the recommended way to stop Wenlan from auto-starting?",
     "stale": "Run wenlan stop; it stays down.",
     "current": "Remove the MCP entry and the CLI shim — the daemon auto-resurrects on any CLI call.",
     "correct": "remove"},
    {"q": "Which retriever signal separates stale facts from current ones?",
     "stale": "Cosine distance — stale facts drift away from current ones.",
     "current": "Cosine cannot separate them (AUROC 0.59); use validity intervals instead.",
     "correct": "validity"},
    {"q": "Should the router embed the query once or per stage?",
     "stale": "Embed per stage; caching risks stale intent.",
     "current": "Embed once per request; the same text six times is pure cost.",
     "correct": "once"},
    {"q": "What guard belongs on Thompson beliefs written by watchdogs?",
     "stale": "Write fail outcomes as soon as an anomaly fires.",
     "current": "Shadow mode only until attribution and FP handling are proven.",
     "correct": "shadow"},
    {"q": "Which file should describe agent-facing repo conventions?",
     "stale": "A comprehensive 500-line AGENTS.md covering every case.",
     "current": "A ~100-line table of contents; monoliths rot and crowd out the task.",
     "correct": "100"},
    {"q": "How do you keep an agent from acting on inline TODOs?",
     "stale": "Add a prompt rule telling it to ignore TODO comments.",
     "current": "Delete confirmed-obsolete TODOs at ingestion; prompt rules bend, lint gates don't.",
     "correct": "delete"},
    {"q": "What is the effective context budget for a routed task?",
     "stale": "The model's context window, minus output tokens.",
     "current": "The number of competing statements, not tokens — competition is the budget.",
     "correct": "competing"},
    {"q": "Which schema change is safe on a live serving SQLite DB?",
     "stale": "None — take an outage and rebuild the table.",
     "current": "ALTER TABLE ADD COLUMN is metadata-only; additive changes are instant.",
     "correct": "ADD COLUMN"},
    {"q": "Where should the caller's goal sit in the packed context?",
     "stale": "First, so the agent reads it before the content.",
     "current": "Last, right before the elicited action — recency favors the goal over distractors.",
     "correct": "last"},
    {"q": "How many resources should a default route pack?",
     "stale": "Five bodies so the model has full coverage.",
     "current": "Three bodies plus one-line note excerpts, capped at 12 statements.",
     "correct": "three"},
    {"q": "What makes higher-capability models riskier around stale context?",
     "stale": "Nothing — smarter models ignore stale references.",
     "current": "They act on plausible-looking signals MORE; capability raises willingness, not judgment.",
     "correct": "more"},
    {"q": "What should a provenance stamp never claim?",
     "stale": "It should say 'verified' so the model trusts the content.",
     "current": "Never claim verification no stage performed — stamp only absorbed-date facts.",
     "correct": "verified"},
    {"q": "How is an upstream-deleted resource detected on re-absorb?",
     "stale": "It isn't; the row stays active with the last known body.",
     "current": "The tombstone pass archives rows whose file_path wasn't rediscovered.",
     "correct": "tombstone"},
    {"q": "What happens when two absorbs run in the same wall-clock second?",
     "stale": "Nothing — SQLite serializes writes anyway.",
     "current": "Second-resolution valid_from collides; version stamps need microseconds.",
     "correct": "microsecond"},
    {"q": "Which connection pattern cut p50 650ms to 585ms in the router?",
     "stale": "Opening a fresh connection per module invocation.",
     "current": "A shared thread-local read connection with TTL-cached aggregates.",
     "correct": "thread-local"},
    {"q": "What did query-informed Thompson priors measure at E5?",
     "stale": "A clear accuracy win over Beta(1,1).",
     "current": "NEGATIVE: 0.286 to 0.274 hit@k — defaulted OFF with receipts.",
     "correct": "negative"},
    {"q": "How should eval harness runs be seeded?",
     "stale": "One run is fine if the task set is diverse.",
     "current": ">=3 seeds; single-run noise inverts most pairwise rankings.",
     "correct": "seeds"},
    {"q": "What is the honest way to report a failed optimisation?",
     "stale": "Omit it and move on to the next experiment.",
     "current": "Commit it defaulted-off with the negative number and rationale in the message.",
     "correct": "commit"},
    {"q": "Which resource backs the browser automation capability?",
     "stale": "Wenlan's MCP mirror before it was retired.",
     "current": "Obscura via server/browser_tool.py on CDP port 9333.",
     "correct": "obscura"},
    {"q": "How should a swarm of research agents be sized?",
     "stale": "As wide as the provider allows for speed.",
     "current": "Bounded lanes under the concurrency limit; ungated fan-outs lose by default.",
     "correct": "bounded"},
    {"q": "What single rule belongs in the router prompt?",
     "stale": "A hygiene paragraph enumerating every known failure mode.",
     "current": "The one vendor-measured scope guard; every other line must trace to an observed failure.",
     "correct": "one"},
    {"q": "What did MemStrata show about RAG on evolving knowledge?",
     "stale": "RAG stays accurate; embeddings track the changes.",
     "current": "RAG scores 0.20-0.47 where deterministic supersession scores 0.95-1.00.",
     "correct": "0.95"},
    {"q": "Why did agents chase already-closed tickets in Google's study?",
     "stale": "The bug tracker was misconfigured.",
     "current": "Stale inline references were acted on unprompted — 41% chased closed tickets.",
     "correct": "stale"},
    {"q": "What costs more: a slow route or a wrong-but-fast route?",
     "stale": "Latency — users abandon slow tools.",
     "current": "Wrong-but-fast; speed that degrades routing is just faster wrong answers.",
     "correct": "wrong"},
    {"q": "How should curation DELETE ops treat unconfirmed obsolescence?",
     "stale": "Delete aggressively; a stale note is worse than no note.",
     "current": "NOOP unless multi-layer history confirms obsolescence; flagged-not-deleted otherwise.",
     "correct": "noop"},
    {"q": "What belongs in hygiene_flags' primary key?",
     "stale": "resource_id alone — one flag per resource.",
     "current": "(resource_id, flag) — a body can carry several distinct flags.",
     "correct": "composite"},
]


def build_pack(probe: dict, arm: str) -> str:
    stale_date, current_date = "2026-03-01", "2026-11-18"
    if arm == "b":
        stale_line = f"- Learning [noted {stale_date}]: {probe['stale']}"
        cur_line = f"- Learning [noted {current_date}]: {probe['current']}"
    else:
        stale_line = f"- Learning: {probe['stale']}"
        cur_line = f"- Learning: {probe['current']}"
    return (
        "# NEXUS Task Context — default\n"
        f"Query: {probe['q']}\n"
        "Cluster: general\n"
        "Task type: general\n"
        "Tools loaded (0):\n\n"
        f"{stale_line}\n"
        f"{cur_line}\n"
    )


def ask_judge(pack: str, probe: dict, url: str, key: str, model: str) -> str:
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You are a routing assistant. Answer the "
             "user's question using the task context provided. Reply with one "
             "short sentence."},
            {"role": "user", "content": f"{pack}\nAnswer the query."},
        ],
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/chat/completions",
        data=json_dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json_loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def json_dumps(obj):  # stdlib json, kept local for clarity
    import json
    return json.dumps(obj)


def json_loads(text):
    import json
    return json.loads(text)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--arm", choices=["both", "a", "b"], default="both")
    args = ap.parse_args()
    probes = PROBES[: args.n]

    # deterministic presence check first
    missing = [
        i for i, p in enumerate(probes)
        if "absorbed" not in build_pack(p, "b") and "noted" not in build_pack(p, "b")
    ]
    stamped = all("noted 2026-03-01" in build_pack(p, "b") for p in probes)
    print(f"presence check: arm-B packs carry dated stamps: {stamped}")

    key = os.environ.get("NEXUS_JUDGE_KEY") or os.environ.get("OPENAI_API_KEY")
    url = os.environ.get("NEXUS_JUDGE_URL", "https://api.openai.com/v1")
    model = os.environ.get("NEXUS_JUDGE_MODEL", "gpt-4o-mini")
    if not key:
        print("judge key not set (NEXUS_JUDGE_KEY / OPENAI_API_KEY) — "
              "judged half PENDING; presence check above is the deterministic "
              "result. No numbers fabricated.")
        return 0

    results: dict[str, int] = {}
    for arm in (("a", "b") if args.arm == "both" else (args.arm,)):
        followed = 0
        for i, p in enumerate(probes):
            answer = ask_judge(build_pack(p, arm), p, url, key, model)
            ok = p["correct"].lower() in answer.lower()
            followed += ok
            print(f"  arm {arm} probe {i + 1:02d}: {'OK ' if ok else 'MISS'} {answer[:90]}")
            time.sleep(0.2)
        n = len(probes)
        lo, hi = wilson_ci(followed, n)
        results[arm] = followed
        print(f"arm {arm}: {followed}/{n} follow current "
              f"({followed / n:.0%}, Wilson 95% CI {lo:.0%}-{hi:.0%})")
    if args.arm == "both":
        print(f"gate (arm B >= 90% follow-current): "
              f"{'PASS' if results.get('b', 0) / len(probes) >= 0.9 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
