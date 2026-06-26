"""Reciprocal Rank Fusion (RRF) for the NEXUS v2 router.

Combines the FTS5 (lexical BM25) ranked list and the sqlite-vec bit[384]
(semantic Hamming) ranked list into a single ranking.

RRF (Cormack, Clarke, Buettcher, 2009):
    score(d) = Σ_i  1 / (k + rank_i(d))      with k = 60

Both input lists must already be sorted best-first (FTS5: ascending bm25 score;
bitvec: ascending Hamming distance). A document present in only one list
contributes 0 from the missing list (per the redesign plan §4.2).

Keying: FTS results are keyed by ``chunk_id`` (each chunk is a distinct
document); vec results are keyed by ``resource_id``. Composite keys
``("fts", chunk_id)`` / ``("vec", resource_id)`` keep the two streams from
colliding numerically. Until chunk→resource linking lands (a documented
follow-up; chunks are ingested with ``resource_id = NULL``), the FTS-only hot
path ranks each chunk by its FTS rank — which is the correct, useful behaviour
for the zero-ML search path. When both streams are present they are scored
independently (no double-count) until linking merges them.

This module is pure-Python and side-effect-free; it never touches the DB or
the embedder. The Thompson-Sampling re-rank hook is added in N5.3 via
``rerank_with_feedback``.
"""
from __future__ import annotations

from typing import Sequence

DEFAULT_K = 60  # Cormack 2009 constant

# Type alias: each ranked result carries at least a keyable id.
RankedResult = dict


def _fts_key(r: RankedResult) -> tuple | None:
    """Composite key for an FTS result: ('fts', chunk_id), or None to skip."""
    cid = r.get("chunk_id")
    return ("fts", cid) if cid is not None else None


def _vec_key(r: RankedResult) -> tuple | None:
    """Composite key for a vec result: ('vec', resource_id), or None to skip."""
    rid = r.get("resource_id")
    return ("vec", rid) if rid is not None else None


def reciprocal_rank_fusion(
    fts_results: Sequence[RankedResult],
    vec_results: Sequence[RankedResult],
    k: int = DEFAULT_K,
) -> list[dict]:
    """Fuse FTS5 + bit[384] ranked lists via RRF.

    Args:
        fts_results: best-first list of dicts with ``chunk_id`` (and typically
            ``resource_id``, ``heading``, ``score``).
        vec_results: best-first list of dicts with ``resource_id`` (and
            typically ``distance``).
        k: RRF constant (default 60).

    Returns:
        List of dicts sorted DESC by rrf_score:
        ``{resource_id, rrf_score, fts_rank, vec_rank, fts_chunk_id}``.
        ``resource_id`` is the resource id for a vec-keyed entry, else the
        chunk's own resource_id (may be None pre-linking).
        ``fts_chunk_id`` is the chunk_id of the best FTS hit (for hydration by
        the caller); None when the entry had no FTS hit.
    """
    fts_ranks: dict[tuple, int] = {}
    fts_chunk: dict[tuple, int | None] = {}
    for i, r in enumerate(fts_results, start=1):
        key = _fts_key(r)
        if key is None:
            continue
        if key not in fts_ranks:            # first occurrence = best rank
            fts_ranks[key] = i
            fts_chunk[key] = r.get("chunk_id")

    vec_ranks: dict[tuple, int] = {}
    for i, r in enumerate(vec_results, start=1):
        key = _vec_key(r)
        if key is None:
            continue
        if key not in vec_ranks:
            vec_ranks[key] = i

    all_keys = set(fts_ranks) | set(vec_ranks)
    scored: list[dict] = []
    for key in all_keys:
        fr = fts_ranks.get(key)
        vr = vec_ranks.get(key)
        rrf = 0.0
        if fr is not None:
            rrf += 1.0 / (k + fr)
        if vr is not None:
            rrf += 1.0 / (k + vr)
        # resource_id is meaningful for vec keys; for fts keys leave it None
        # (the caller hydrates the real resource_id from the chunk row).
        rid = key[1] if key[0] == "vec" else None
        scored.append({
            "resource_id": rid,
            "rrf_score": rrf,
            "fts_rank": fr,
            "vec_rank": vr,
            "fts_chunk_id": fts_chunk.get(key),
        })
    scored.sort(key=lambda x: x["rrf_score"], reverse=True)
    return scored


def rerank_with_feedback(
    fused: Sequence[dict],
    thompson_scores: dict[int, float] | None,
    rrf_weight: float = 0.7,
    thompson_weight: float = 0.3,
) -> list[dict]:
    """Blend RRF scores with Thompson-Sampling posterior scores.

    Called by the search pipeline only when the learning loop has enough
    outcome data (>= 20 entries for the cluster+intent). When
    ``thompson_scores`` is None or empty (cold start), the input order is
    preserved unchanged — RRF alone decides.

    Args:
        fused: output of ``reciprocal_rank_fusion`` (must contain rrf_score).
        thompson_scores: {resource_id: sampled_thompson_score} or None.
        rrf_weight: blend weight for RRF (default 0.7).
        thompson_weight: blend weight for Thompson (default 0.3).

    Returns:
        New list of dicts sorted DESC by ``final_score``. Each dict is the
        input dict plus ``final_score`` and ``thompson_score`` fields.
    """
    if not thompson_scores:
        # Cold start: keep RRF order, record final = rrf_score.
        out = []
        for x in fused:
            y = dict(x)
            y["thompson_score"] = None
            y["final_score"] = x["rrf_score"]
            out.append(y)
        return out

    # Normalise both score streams to [0, 1] before blending so the weights
    # are commensurate regardless of scale.
    rrf_vals = [x["rrf_score"] for x in fused if x["rrf_score"] > 0]
    rrf_max = max(rrf_vals) if rrf_vals else 1.0
    t_vals = [v for v in thompson_scores.values() if v is not None and v >= 0]
    t_max = max(t_vals) if t_vals else 1.0

    out: list[dict] = []
    for x in fused:
        rid = x["resource_id"]
        rrf_n = x["rrf_score"] / rrf_max if rrf_max > 0 else 0.0
        t = thompson_scores.get(rid) if rid is not None else None
        t_n = (t / t_max) if (t is not None and t_max > 0) else 0.0
        final = rrf_weight * rrf_n + thompson_weight * t_n
        y = dict(x)
        y["thompson_score"] = t
        y["final_score"] = final
        out.append(y)
    out.sort(key=lambda z: z["final_score"], reverse=True)
    return out