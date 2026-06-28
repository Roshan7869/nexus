#!/usr/bin/env python3
"""L6 — implicit-outcome derivation pipeline. Derives a routing outcome from
signals that already exist in the trace, WITHOUT forcing an explicit user
verdict and WITHOUT ever faking one. Lifts feedback coverage toward >80% on
the routes that have any observable downstream behavior.

Research grounding:
  - Signal 1,2: TIER R_exec (arXiv:2605.16790) — runtime execution reward.
  - Signal 1 guard: IRC (arXiv:2604.02869) — read-only tools carry ~0 signal;
    never verdict them 'correct', mark 'routed' (zero Thompson update).
  - Signal 6: FeedRec (WWW 2022) — quick-close is a STRONG negative (abandonment),
    NOT neutral; stale (>60s, no next action) is NO signal.
  - Signal 3: Reflexion (arXiv:2303.11366) + Self-Rewarding 5-pt rubric
    (arXiv:2401.10020) — LLM-as-Judge, sampled to bound cost (P1-T2 wires this).
  - Signal 4: Self-Aligned Reward (arXiv:2509.05489) — conditioned perplexity
    gap R = clip((ppl(a)-ppl(a|q))/ppl(a), -1, 1) (P1-T2 wires the scorer).

The anti-inflation discipline (the fix for the disabled _auto_close_routed
heuristic and the dormant scripts/auto_close_routes.py landmine): every
fallback returns ('routed', 0.0, ...) — the honest neutral state, excluded
from Thompson updates. Stale is NOT correct; stale is no signal. A route
only gets 'correct'/'wrong'/'partial' when a real signal produced it.

Usage:
  from server.implicit_outcomes import derive_outcome
  outcome, quality, source = derive_outcome(route, trace, downstream,
                                             perplexity_scorer=None,
                                             judge_scorer=None)
  if outcome != "routed":
      record_outcome(skill_name=route["resource_name"], outcome=outcome,
                     task_summary=route["task_summary"], quality=quality,
                     routed_id=route["id"])
  else:
      # mark routed, update routing_log outcome='routed', DO NOT touch Thompson
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

Outcome = str  # "correct" | "wrong" | "partial" | "routed"
Quality = float  # [0.0, 1.0] — weights the Thompson belief update

# Read-only / lookup tool substrings — carry ~0 discriminative signal (IRC).
# Verdicting these 'correct' on execution success would inflate beliefs.
READ_ONLY_HINTS = (
    "search", "list", "get", "browse", "describe", "lookup",
    "ft5_search", "read", "fetch", "status", "health", "stats", "count",
)

QUICK_CLOSE_MS = 2000   # next action within 2s after a route → abandonment
STALE_MS = 60_000        # no next action within 60s → genuinely no signal


def _is_read_only(tool_name: str) -> bool:
    t = (tool_name or "").lower()
    return any(h in t for h in READ_ONLY_HINTS)


def derive_outcome(
    route: dict,
    trace: dict | None,
    downstream: dict | None,
    perplexity_scorer: Optional[Callable] = None,
    judge_scorer: Optional[Callable] = None,
) -> Tuple[Outcome, Quality, str]:
    """Return (outcome, quality in [0,1], source). Never fabricates a verdict.

    `route`:     {"resource_name", "task_summary", "id", "query"} from routing_log.
    `trace`:     execution trace — {"exception", "executed", "duration_ms"}.
    `downstream`:{"tool_result", "re_route_count", "next_action_latency_ms",
                  "task_status" in {completed,failed}, "had_correction"}.
    """
    trace = trace or {}
    downstream = downstream or {}
    tool_name = route.get("resource_name", "") or route.get("skill_name", "")

    # ── Signal 1+2: execution success + retry count (TIER R_exec + IRC) ─────
    tool_result = downstream.get("tool_result")
    re_routes = int(downstream.get("re_route_count", 0) or 0)

    if tool_result is not None and isinstance(tool_result, dict):
        exc = tool_result.get("exception")
        if exc is not None:
            return "wrong", 0.9, "execution_exception"
        if _is_read_only(tool_name):
            # IRC: read-only tools carry ~0 signal — do NOT verdict them.
            return "routed", 0.0, "read_only_zero_signal"
        if re_routes == 0:
            return "correct", 1.0, "exec_no_retry"
        if re_routes == 1:
            return "partial", 0.6, "one_retry"
        return "wrong", max(0.0, 1.0 - 0.4 * re_routes), "multi_retry"

    # ── Signal 6: quick-close vs stale (FeedRec) ───────────────────────────
    latency = downstream.get("next_action_latency_ms")
    if latency is not None:
        if latency < QUICK_CLOSE_MS:
            # Agent bailed immediately → strong negative, NOT neutral.
            return "wrong", 0.7, "quick_close_abandonment"
        if latency > STALE_MS:
            # Genuinely stale → NO signal. This is the auto-close fix.
            return "routed", 0.0, "stale_no_signal"

    # ── Signal 5: downstream task completion ──────────────────────────────
    task_status = downstream.get("task_status")
    if task_status in {"completed", "failed"}:
        if task_status == "completed" and not downstream.get("had_correction"):
            return "correct", 0.9, "task_completed"
        if task_status == "failed":
            return "wrong", 0.8, "task_failed"
        return "partial", 0.5, "completed_with_caveat"

    # ── Signal 4: conditioned perplexity gap (SAR) ────────────────────────
    if perplexity_scorer is not None:
        try:
            query = route.get("query", "") or route.get("task_summary", "")
            ppl_q = perplexity_scorer(query, tool_name, tool_result)
            ppl_a = perplexity_scorer(query, tool_name, None)  # standalone
            if ppl_q is not None and ppl_a is not None and ppl_a > 0:
                R = max(-1.0, min(1.0, (ppl_a - ppl_q) / ppl_a))
                if R > 0.3:
                    return "correct", (R + 1) / 2, "perplexity_gap_high"
                if R < -0.1:
                    return "wrong", (R + 1) / 2, "perplexity_gap_low"
                return "partial", (R + 1) / 2, "perplexity_gap_mid"
        except Exception:
            pass  # never fake on scorer failure — fall through

    # ── Signal 3: Reflexion LLM-as-Judge (5-pt rubric) — sampled by caller ──
    if judge_scorer is not None:
        try:
            query = route.get("query", "") or route.get("task_summary", "")
            score = judge_scorer(query, tool_name, tool_result, downstream)
            if score is not None:
                outcome = "correct" if score >= 4 else "wrong" if score <= 2 else "partial"
                return outcome, score / 5.0, "reflexion_judge"
        except Exception:
            pass

    # ── Honest fallback: NO signal → neutral, excluded from Thompson ──────
    return "routed", 0.0, "no_signal"


# ── self-test ──────────────────────────────────────────────────────────────
def _self_test() -> None:
    """Quick correctness check for the 6 signal paths. Run: python3 -m."""
    cases = [
        # (route, trace, downstream, expected_outcome, expected_source_subst)
        ({"resource_name": "edit_file", "query": "fix bug"},
         {}, {"tool_result": {"exception": None}, "re_route_count": 0},
         "correct", "exec_no_retry"),
        ({"resource_name": "edit_file", "query": "fix bug"},
         {}, {"tool_result": {"exception": "KeyError"}, "re_route_count": 0},
         "wrong", "execution_exception"),
        ({"resource_name": "ft5_search", "query": "find docs"},
         {}, {"tool_result": {"exception": None}, "re_route_count": 0},
         "routed", "read_only_zero_signal"),
        ({"resource_name": "edit_file", "query": "fix bug"},
         {}, {"tool_result": {"exception": None}, "re_route_count": 2},
         "wrong", "multi_retry"),
        ({"resource_name": "deploy", "query": "ship"},
         {}, {"next_action_latency_ms": 800},
         "wrong", "quick_close_abandonment"),
        ({"resource_name": "deploy", "query": "ship"},
         {}, {"next_action_latency_ms": 90_000},
         "routed", "stale_no_signal"),
        ({"resource_name": "deploy", "query": "ship"},
         {}, {"task_status": "completed", "had_correction": False},
         "correct", "task_completed"),
        ({"resource_name": "deploy", "query": "ship"},
         {}, {}, "routed", "no_signal"),
    ]
    failed = 0
    for route, trace, downstream, exp_out, exp_src in cases:
        out, q, src = derive_outcome(route, trace, downstream)
        ok = out == exp_out and exp_src in src
        if not ok:
            failed += 1
            print(f"  FAIL: expected ({exp_out}, *{exp_src}*) got ({out}, {q:.2f}, {src})")
        else:
            print(f"  ok:  {out:<8} q={q:.2f} src={src}")
    print(f"{'ALL PASS' if not failed else f'{failed} FAILED'}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    _self_test()