"""Pure-Python graded-relevance metrics for NEXUS routing evaluation.

Restores the honest accuracy/quality metrics the legacy reporting layer was
missing: `partial` outcomes (graded between correct and wrong) were silently
dropped by the cluster-affinity / stats filters, inflating binary "accuracy".

References:
  - Järvelin & Kekäläinen, "Cumulated gain-based evaluation of IR
    techniques" (ACM TOIS 2002) — NDCG with graded relevance and the
    discount / ideal-DCG formulation used here.
  - Deliu et al., "Ordinal rewards in recommender evaluation" (2024) —
    ordinal/graded outcome rewards (correct > partial > wrong), which
    motivates the 2/1/0 gain map and the partial_weight parameterization.

No external dependencies (ranx / pytrec_eval are intentionally NOT required).
All functions take an open sqlite3 connection with ``row_factory = sqlite3.Row``
so they can be reused from the benchmark, the MCP server, and ad-hoc shells.
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Iterable

# Gain map: correct=2, partial=1, wrong=0 (Järvelin & Kekäläinen graded gains).
# partial_weight scales the partial gain for weighted_accuracy only — the
# NDCG/iP@k gain map stays on the integer 2/1/0 ladder so the discount math
# is stable and independent of the accuracy weight.
_MAX_GAIN = 2.0


def outcome_to_gain(outcome: str | None, partial_weight: float = 0.5) -> float:
    """Map a routing outcome to a graded gain.

    correct -> ``partial_weight``-independent 2.0 (full gain on the ladder),
    partial -> 1.0, wrong / NULL / unknown -> 0.0.

    ``partial_weight`` is accepted for API symmetry with
    :func:`weighted_accuracy` but does NOT rescale the NDCG ladder gain —
    it only documents the partial semantics. Returning a fixed 1.0 for
    partial keeps DCG/iDCG on the same integer scale.
    """
    if outcome == "correct":
        return 2.0
    if outcome == "partial":
        return 1.0
    return 0.0


def weighted_accuracy(conn: sqlite3.Connection,
                     partial_weight: float = 0.5) -> float:
    """Honest weighted accuracy over terminal routing outcomes.

    ``(correct + partial_weight * partial) / max(correct + partial + wrong, 1)``

    Excludes NULL / routed / pending outcomes (no signal). This is the
    metric that replaces the legacy binary ``correct/(correct+wrong)``
    which silently dropped all 1611 partial rows.
    """
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) AS correct, "
        "SUM(CASE WHEN outcome='partial'  THEN 1 ELSE 0 END) AS partial, "
        "SUM(CASE WHEN outcome='wrong'    THEN 1 ELSE 0 END) AS wrong "
        "FROM routing_log WHERE outcome IN ('correct','partial','wrong')"
    ).fetchone()
    correct = row["correct"] or 0
    partial = row["partial"] or 0
    wrong = row["wrong"] or 0
    denom = correct + partial + wrong
    if denom <= 0:
        return 0.0
    return (correct + partial_weight * partial) / denom


def _ranked_gains_for_query(rows: list[sqlite3.Row]) -> list[float]:
    """Return per-rank gains for one query, ordered by matched_skills order.

    Each row is a routing_log entry for the same ``task_summary``. The
    routed resource order comes from the ``matched_skills`` JSON list (the
    order the router presented them); the outcome gain is looked up per
    skill_name. Skills present in matched_skills but with no outcome row
    for this query get gain 0 (unverified — IRC arXiv:2604.02869: no
    signal). Skills with an outcome but absent from matched_skills are
    appended in id order (defensive — keeps every outcome row visible).
    """
    # skill -> gain (last write wins; one outcome per skill per query)
    gain_by_skill: dict[str, float] = {}
    ordered_skills: list[str] = []
    seen_skills: set[str] = set()

    # First pass: establish the router's intended rank order from matched_skills.
    for r in rows:
        raw = r["matched_skills"]
        if not raw:
            continue
        try:
            skills = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(skills, Iterable):
            for s in skills:
                name = s if isinstance(s, str) else (
                    s.get("name") if isinstance(s, dict) else None
                )
                if name and name not in seen_skills:
                    seen_skills.add(name)
                    ordered_skills.append(name)
        break  # matched_skills is identical across rows of the same query

    # Second pass: record outcome gains per skill.
    for r in rows:
        name = r["skill_name"]
        if not name:
            continue
        gain_by_skill[name] = outcome_to_gain(r["outcome"])
        if name not in seen_skills:
            seen_skills.add(name)
            ordered_skills.append(name)

    return [gain_by_skill.get(s, 0.0) for s in ordered_skills]


def _dcg(gains: list[float], k: int) -> float:
    dcg = 0.0
    for i, g in enumerate(gains[:k], start=1):
        dcg += g / math.log2(i + 1)
    return dcg


def ndcg_at_k(conn: sqlite3.Connection, k: int = 5) -> float:
    """Average NDCG@k over routing_log queries, grouped by ``task_summary``.

    Per query: DCG@k uses the router's ranked order (matched_skills order)
    with graded gains (correct=2, partial=1, wrong=0). iDCG@k uses the same
    gains sorted descending. NDCG = DCG / iDCG (0 when iDCG = 0). Queries
    with zero total gain (all wrong / no outcomes) contribute 0 — they are
    genuine failures, not excluded.
    """
    rows = conn.execute(
        "SELECT task_summary, skill_name, matched_skills, outcome, id "
        "FROM routing_log "
        "WHERE outcome IN ('correct','partial','wrong') "
        "  AND task_summary IS NOT NULL "
        "ORDER BY task_summary, id"
    ).fetchall()

    # Group by task_summary (the query identifier — routing_log has no
    # dedicated `query`/`trigger_query` column; task_summary holds it).
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        key = r["task_summary"]
        groups.setdefault(key, []).append(r)

    if not groups:
        return 0.0

    scores: list[float] = []
    for key, grp in groups.items():
        gains = _ranked_gains_for_query(grp)
        dcg = _dcg(gains, k)
        idcg = _dcg(sorted(gains, reverse=True), k)
        scores.append(dcg / idcg if idcg > 0 else 0.0)
    return sum(scores) / len(scores)


def intent_aware_precision_at_k(conn: sqlite3.Connection, k: int = 5) -> float:
    """Intent-aware Precision@k with graded relevance (iP@k).

    Per query: ``(sum_{i=1..k} gain_i / MAX_GAIN) / k`` — gains normalized
    by the max gain (2) so the score is in [0, 1], averaged over queries.
    This is the graded-relevance analogue of binary P@k: a partial at rank 1
    contributes 0.5, a correct contributes 1.0, a wrong contributes 0.
    """
    rows = conn.execute(
        "SELECT task_summary, skill_name, matched_skills, outcome, id "
        "FROM routing_log "
        "WHERE outcome IN ('correct','partial','wrong') "
        "  AND task_summary IS NOT NULL "
        "ORDER BY task_summary, id"
    ).fetchall()

    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault(r["task_summary"], []).append(r)

    if not groups:
        return 0.0

    scores: list[float] = []
    for grp in groups.values():
        gains = _ranked_gains_for_query(grp)
        topk = gains[:k]
        if k <= 0:
            scores.append(0.0)
            continue
        weighted = sum(g / _MAX_GAIN for g in topk)
        scores.append(weighted / k)
    return sum(scores) / len(scores)