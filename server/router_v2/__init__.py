"""router_v2 — unified retrieval + 4-term scoring pipeline.

Single entry point :func:`unified_route` wires the package's pieces together:

    resource name/description search (lexical) ─┐
                                                 ├→ RRF (k=60) ──→ 4-term blend ──→ two-tier hot/cold split
    bit[384] (semantic, resource-keyed) ────────┘    (0.40 iso + 0.15 thompson + 0.25 efc + 0.20 ucb)

The vec (bit[384]) path is best-effort: if the sentence-transformers embedder
is unavailable (36s cold load, no model, or no `vec_chunks` index), it falls
back to lexical-only — RRF and the 4-term blend both handle an empty vec list.

DATA NOTE: ``chunks_fts`` (66495 chunks) has 0/66495 chunks linked to a
``resource_id``, so a chunk-level FTS path returns no named skills. Until
chunk→resource linking is backfilled (P7 data hygiene, separate task), the
lexical source here searches the 2815 ``resources`` directly by
name/description. ``vec_chunks`` rowids ARE resource ids (built from
``resource_embeddings``), so the semantic path is correctly resource-keyed.

This is the integration glue the MCP ``find_skills`` tool calls when
``USE_UNIFIED_ROUTER=1`` is set (P2 wiring, default OFF = zero live change).
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

_SELF = Path(__file__).resolve().parent          # server/router_v2/
_SERVER = _SELF.parent                             # server/
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

import math

from router_v2.bitvec import BitIndex  # noqa: E402
from router_v2.fusion import (  # noqa: E402
    DEFAULT_BLEND_WEIGHTS,
    convex_combine_resource,
    reciprocal_rank_fusion,
    two_tier_split,
)
from router_v2 import scoring as _scoring  # noqa: E402
from router_v2.lazy_embedder import LazyEmbedder, EmbedderUnavailableError  # noqa: E402
from router_v2.chunk_retrieval import retrieve_chunks as _retrieve_chunks  # noqa: E402

DB_PATH = _resolve_db_path()

# Decision D1 corpus for the chunk stream: "chunks" (ingestion_chunks, today's
# default) or "sections" (OKF resource_sections / sections_fts). Module-level
# constant, NOT an env flag (decision D4) — flipped once in Phase 2 after the
# golden A/B adopts the OKF corpus.
RETRIEVAL_CORPUS = "chunks"

# stopwords stripped from lexical queries (FTS AND over common words matches nothing)
_STOPWORDS = {
    "a", "an", "the", "and", "or", "not", "in", "on", "at", "to", "for",
    "of", "is", "it", "this", "that", "with", "by", "be", "as", "fix",
    "how", "do", "i", "my", "me", "we", "you",
}

__all__ = ["unified_route", "DEFAULT_BLEND_WEIGHTS"]


def _tokens(query: str, keep_stopwords: bool = False) -> list[str]:
    """Lowercase alphanumeric tokens, optionally dropping stopwords."""
    toks = re.findall(r"[a-z0-9_\-]+", (query or "").lower())
    if keep_stopwords:
        return toks
    return [t for t in toks if t and t not in _STOPWORDS]


def _resource_search(query: str, db_path: str, limit: int = 50,
                     clusters: list[str] | None = None,
                     use_sections: bool = True) -> list[dict]:
    """Lexical search over resources.name + description + body_text
    (and optionally resource_sections.heading + content).

    Returns best-first list of ``{resource_id, name, cluster, score}`` where
    score is a simple relevance weight (name exact > name token > desc/body
    token > section heading > section content). Used as the lexical source for
    RRF in place of the unlinked ``chunks_fts`` index. Scores are negative so
    that, like bm25, lower is better (RRF only uses ranks, so the sign does
    not affect fusion).

    When *use_sections* is True (default), the query LEFT JOINs the
    ``resource_sections`` table and adds per-token scoring on
    ``resource_sections.heading`` (weight 4) and
    ``resource_sections.content`` (weight 2).  Section scores are accumulated
    across multiple matching sections via SUM + GROUP BY.

    ``clusters``: when set, restrict candidates to resources in the given
    clusters (via cluster_membership JOIN, fallback resources.cluster IN).
    Used by unified_route_v5's L2 cluster-constrained topic retrieval (HGMF).
    """
    toks = _tokens(query)
    if not toks:
        # fall back to the raw query string if it's all stopwords/punct
        toks = _tokens(query, keep_stopwords=True)
    if not toks:
        return []
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        # OR-of-LIKE filter, then rank by weighted match score
        like_clauses = []
        params: list = []
        for t in toks:
            if use_sections:
                like_clauses.append(
                    "name LIKE ? OR description LIKE ? OR body_text LIKE ? "
                    "OR resource_sections.heading LIKE ? "
                    "OR resource_sections.content LIKE ?"
                )
                params.extend([f"%{t}%"] * 5)
            else:
                like_clauses.append("name LIKE ? OR description LIKE ? OR body_text LIKE ?")
                params.extend([f"%{t}%"] * 3)
        where = " OR ".join(f"({c})" for c in like_clauses)
        cluster_clause = ""
        cluster_join = ""
        if clusters:
            # cluster_membership is the authoritative cluster source; JOIN it.
            ph = ",".join("?" for _ in clusters)
            cluster_join = " JOIN cluster_membership cm ON cm.resource_id = resources.id "
            cluster_clause = f" AND cm.cluster_name IN ({ph}) "
        score_terms = ["(CASE WHEN name = ? THEN 50 ELSE 0 END)"]
        params_score: list = [query.strip().lower()]
        for t in toks:
            score_terms.append("(CASE WHEN name LIKE ? THEN 10 ELSE 0 END)")
            params_score.append(f"%{t}%")
            score_terms.append("(CASE WHEN description LIKE ? THEN 3 ELSE 0 END)")
            params_score.append(f"%{t}%")
            score_terms.append("(CASE WHEN body_text LIKE ? THEN 1 ELSE 0 END)")
            params_score.append(f"%{t}%")
            if use_sections:
                score_terms.append(
                    "COALESCE(SUM(CASE WHEN resource_sections.heading LIKE ? "
                    "THEN 4 ELSE 0 END), 0)"
                )
                params_score.append(f"%{t}%")
                score_terms.append(
                    "COALESCE(SUM(CASE WHEN resource_sections.content LIKE ? "
                    "THEN 2 ELSE 0 END), 0)"
                )
                params_score.append(f"%{t}%")
        if use_sections:
            sql = (
                "SELECT resources.id AS id, resources.name AS name, "
                "resources.cluster AS cluster, "
                + " + ".join(score_terms)
                + " AS match_score "
                f"FROM resources {cluster_join} "
                "LEFT JOIN resource_sections "
                "ON resource_sections.resource_id = resources.id "
                f"WHERE resources.status NOT IN ('disabled','deprecated','archived') "
                f"{cluster_clause} AND ({where}) "
                "GROUP BY resources.id "
                "ORDER BY match_score DESC, resources.times_triggered DESC LIMIT ?"
            )
        else:
            sql = (
                "SELECT DISTINCT resources.id AS id, resources.name AS name, "
                "resources.cluster AS cluster, "
                + " + ".join(score_terms)
                + " AS match_score "
                f"FROM resources {cluster_join} "
                f"WHERE resources.status NOT IN ('disabled','deprecated','archived') "
                f"{cluster_clause} AND ({where}) "
                "ORDER BY match_score DESC, resources.times_triggered DESC LIMIT ?"
            )
        bind = params_score[:]
        if clusters:
            bind += list(clusters)
        bind += params + [limit]
        rows = conn.execute(sql, tuple(bind)).fetchall()
        out: list[dict] = []
        for i, r in enumerate(rows, start=1):
            out.append({
                "resource_id": r["id"],
                "name": r["name"],
                "cluster": r["cluster"],
                # negative so lower=better (bm25 convention); rank is all RRF uses
                "score": -(i + (50 - r["match_score"]) * 0.001),
                # raw match_score for CC fusion (FIX 6)
                "match_score": float(r["match_score"]),
            })
        return out
    finally:
        conn.close()


def _try_vec(query: str, db_path: str, k: int) -> tuple[list[dict], bool, str]:
    """Best-effort bit[384] search. Returns (results, used_dense, reason).

    Never raises: on any failure returns ([], False, reason).
    """
    try:
        emb = LazyEmbedder.get()   # singleton — avoids per-call model reload
        qvec = emb.encode_single(query)
    except EmbedderUnavailableError as e:
        return [], False, f"embedder_unavailable: {e}"
    except Exception as e:
        return [], False, f"embed_error: {e}"
    try:
        with BitIndex(db_path) as idx:
            vec = idx.search(qvec, k=k)
        return vec, True, "ok"
    except Exception as e:
        return [], False, f"bitvec_search_error: {e}"


def _filter_by_clusters(results: list[dict], db_path: str,
                        clusters: list[str]) -> list[dict]:
    """Post-filter vec results to resources in the given clusters
    (cluster_membership is authoritative; fallback resources.cluster)."""
    rids = [r.get("resource_id") for r in results if r.get("resource_id") is not None]
    if not rids:
        return []
    ph = ",".join("?" for _ in rids)
    cph = ",".join("?" for _ in clusters)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                f"SELECT DISTINCT resource_id FROM cluster_membership "
                f"WHERE resource_id IN ({ph}) AND cluster_name IN ({cph})",
                tuple(rids + clusters)).fetchall()
            ok = {r["resource_id"] for r in rows}
        except Exception:
            rows = conn.execute(
                f"SELECT id FROM resources WHERE id IN ({ph}) AND cluster IN ({cph})",
                tuple(rids + clusters)).fetchall()
            ok = {r["id"] for r in rows}
    finally:
        conn.close()
    return [r for r in results if r.get("resource_id") in ok]


def _hydrate_names(conn: sqlite3.Connection, rids: list[int]) -> dict[int, dict]:
    """Pull name/cluster/resource_type for a set of resource ids."""
    if not rids:
        return {}
    ph = ",".join("?" for _ in rids)
    rows = conn.execute(
        f"SELECT id, name, cluster, resource_type, description "
        f"FROM resources WHERE id IN ({ph})",
        tuple(rids),
    ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def _chunk_resource_stream(query: str, db_path: str, k: int,
                           clusters: list[str] | None = None) -> list[dict]:
    """Chunk-derived resource stream — the 66,495 chunks actively participating.

    Runs chunk-level FTS (``chunks_fts``) + chunk semantic (``vec_chunks_content``)
    → RRF over chunk_id (via :func:`retrieve_chunks`), then aggregates chunk hits
    by ``resource_id`` (best chunk per resource) to produce a resource-keyed
    stream. Only chunks the linker has linked to a resource contribute; unlinked
    chunks (findings with no resource) are dropped at this layer because the
    router ranks resources, not chunks.

    Best-effort: any failure (embedder cold, vec0 missing, no linked chunks)
    returns ``[]`` so the caller's RRF simply gets no chunk signal — identical
    to the pre-wiring state. Reuses the ``LazyEmbedder`` singleton (same
    all-MiniLM-L6-v2 model as the resource path) so there is NO second model
    cold-start; only the ``vec_chunks_content`` sqlite open is extra (~ms).

    Args:
        query: natural-language task description.
        db_path: path to nexus.db.
        k: target chunk hits to fetch before aggregation.
        clusters: when set, pass through to retrieve_chunks cluster filter.

    Returns: best-first list of ``{resource_id, chunk_rank, chunk_rrf,
    n_chunks}`` — one entry per resource, ranked by its best chunk's RRF score.
    """
    try:
        rc = _retrieve_chunks(query, top_k=k, clusters=clusters,
                              db_path=db_path, vec=True,
                              corpus=RETRIEVAL_CORPUS,
                              graph_lane=False)   # L1-b: router ensemble is
                              # top-1-calibrated; lane serves RAG consumers
    except Exception:
        return []
    # aggregate chunk hits by resource_id; keep the best (highest rrf) chunk
    best: dict[int, dict] = {}
    for r in rc.get("results", []):
        rid = r.get("resource_id")
        if rid is None:
            continue
        score = r.get("rrf_score", 0.0) or 0.0
        cur = best.get(rid)
        if cur is None or score > cur["chunk_rrf"]:
            best[rid] = {
                "resource_id": rid,
                "chunk_rrf": score,
                "chunk_id": r.get("chunk_id"),
                "heading": r.get("heading"),
                "n_chunks": (cur["n_chunks"] + 1) if cur else 1,
            }
        else:
            cur["n_chunks"] += 1
    # rank resources by their best chunk's rrf score
    ranked = sorted(best.values(), key=lambda x: x["chunk_rrf"], reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["chunk_rank"] = i
    return ranked


def _rrf_resource(lex: list[dict], vec_results: list[dict],
                  chunks: list[dict] | None = None,
                  k: int = 60, chunk_weight: float = 0.5,
                  section_weight: float = 1.0) -> list[dict]:
    """Resource-keyed RRF (Cormack 2009), fusing lexical + semantic (+ chunk) on resource_id.

    Unlike :func:`router_v2.fusion.reciprocal_rank_fusion` (which keys FTS by
    ``chunk_id`` and vec by ``resource_id``), this keys ALL streams by
    ``resource_id`` so a resource found by multiple streams gets the sum of
    their ``1/(k+rank)`` contributions — proper RRF merge. Used by
    :func:`unified_route` because its lexical source is the resource table,
    not the (unlinked) chunk FTS index.

    The chunk stream is **discounted** (``chunk_weight`` < 1.0, default 0.5):
    a chunk *content* match is weaker evidence than a direct resource
    *name/description* match, so equal RRF weight would let loosely-matched
    chunk content displace strong direct matches. Weighted RRF (a known
    Cormack extension) downweights the noisier stream. A resource found by
    BOTH lex/vec AND chunks still gets a boost (the chunk confirms it); a
    chunk-ONLY resource ranks below strong direct matches — no pollution.

    When a chunk was matched via section content (``chunk_heading`` is not
    None), its RRF contribution is further multiplied by ``section_weight``.
    This lets callers amplify (e.g. 2.0) or dampen (e.g. 0.5) section-sourced
    matches relative to direct resource-level matches.

    Args:
        lex: best-first list of ``{resource_id, name?, cluster?}``.
        vec_results: best-first list of ``{resource_id, distance?}``.
        chunks: optional best-first list of ``{resource_id, chunk_rank}`` from
            :func:`_chunk_resource_stream` (the 66k chunks actively participating).
        k: RRF constant (default 60).
        chunk_weight: multiplicative weight for all chunk RRF contributions
            (default 0.5).
        section_weight: multiplicative weight for section-sourced chunk RRF
            contributions (default 1.0 — backward-compatible neutral).

    Returns:
        best-first list of ``{resource_id, rrf_score, lex_rank, vec_rank,
        chunk_rank, name, cluster}``.
    """
    acc: dict[int, dict] = {}
    for i, r in enumerate(lex, start=1):
        rid = r.get("resource_id")
        if rid is None:
            continue
        e = acc.setdefault(rid, {"resource_id": rid, "lex_rank": None,
                                 "vec_rank": None, "chunk_rank": None})
        e["lex_rank"] = i
        e.setdefault("name", r.get("name"))
        e.setdefault("cluster", r.get("cluster"))
    for i, r in enumerate(vec_results, start=1):
        rid = r.get("resource_id")
        if rid is None:
            continue
        e = acc.setdefault(rid, {"resource_id": rid, "lex_rank": None,
                                 "vec_rank": None, "chunk_rank": None})
        e["vec_rank"] = i
    for r in (chunks or []):
        rid = r.get("resource_id")
        if rid is None:
            continue
        e = acc.setdefault(rid, {"resource_id": rid, "lex_rank": None,
                                 "vec_rank": None, "chunk_rank": None})
        e["chunk_rank"] = r.get("chunk_rank")
        # carry chunk provenance for debugging/audit
        e.setdefault("chunk_id", r.get("chunk_id"))
        e.setdefault("chunk_heading", r.get("heading"))
        e.setdefault("n_chunks", r.get("n_chunks"))
    out: list[dict] = []
    for rid, e in acc.items():
        rrf = 0.0
        if e["lex_rank"] is not None:
            rrf += 1.0 / (k + e["lex_rank"])
        if e["vec_rank"] is not None:
            rrf += 1.0 / (k + e["vec_rank"])
        if e["chunk_rank"] is not None:
            chunk_rrf = 1.0 / (k + e["chunk_rank"])
            if e.get("chunk_heading") and section_weight != 1.0:
                chunk_rrf *= section_weight
            rrf += chunk_weight * chunk_rrf
        out.append({
            "resource_id": rid,
            "rrf_score": rrf,
            "lex_rank": e["lex_rank"],
            "vec_rank": e["vec_rank"],
            "chunk_rank": e["chunk_rank"],
            "chunk_id": e.get("chunk_id"),
            "chunk_heading": e.get("chunk_heading"),
            "n_chunks": e.get("n_chunks"),
            "name": e.get("name"),
            "cluster": e.get("cluster"),
        })
    out.sort(key=lambda x: x["rrf_score"], reverse=True)
    return out


def _cc_resource(lex: list[dict], vec_results: list[dict],
                 chunks: list[dict] | None = None,
                 weights: dict[str, float] | None = None,
                 section_weight: float = 1.0) -> list[dict]:
    """Resource-keyed Convex Combination fusion (FIX 6 — Bruch et al. 2022).

    Thin wrapper around ``convex_combine_resource`` that normalizes the
    ``cc_score`` to ``rrf_score`` (so the downstream 4-term blend and
    two-tier split work identically regardless of fusion mode). Each
    output dict carries both ``cc_score`` and ``rrf_score`` (aliased).

    When ``section_weight != 1.0``, chunk scores from section-content
    matches (where ``heading`` is set) are scaled by that weight before
    being passed to the convex combination — same amplification/dampening
    semantics as in ``_rrf_resource``.

    Args: same as ``_rrf_resource`` but with ``weights`` instead of ``k``
    and ``chunk_weight``.
    """
    if section_weight != 1.0 and chunks:
        chunks = [
            {**c, "chunk_rrf": c.get("chunk_rrf", 0) * section_weight}
            if c.get("heading") else c
            for c in chunks
        ]
    cc = convex_combine_resource(lex, vec_results, chunks, weights=weights)
    # Normalize cc_score to serve as rrf_score for downstream 4-term blend
    cc_vals = [x["cc_score"] for x in cc if x["cc_score"] > 0]
    cc_max = max(cc_vals) if cc_vals else 1.0
    out: list[dict] = []
    for i, x in enumerate(cc):
        out.append({
            "resource_id": x["resource_id"],
            "rrf_score": x["cc_score"],  # aliased for downstream
            "cc_score": x["cc_score"],
            "lex_score": x.get("lex_score"),
            "vec_score": x.get("vec_score"),
            "chunk_score": x.get("chunk_score"),
            "name": x.get("name"),
            "cluster": x.get("cluster"),
        })
    return out


def _estimate_query_entropy(query: str) -> float:
    """Estimate query specificity via token-length entropy (0..1).

    ToolkenGPT-inspired: short/generic queries have low entropy and need more
    cold exploration; long/specific queries have high entropy and can exploit
    hot resources. Returns normalized entropy in [0, 1].
    """
    toks = _tokens(query)
    if not toks:
        return 0.0
    # Use token-length distribution as a proxy for specificity
    # Short queries (1-2 words) → low entropy → more exploration needed
    # Long queries (5+ words) → high entropy → can exploit hot resources
    n = len(toks)
    if n <= 1:
        return 0.1
    if n <= 2:
        return 0.3
    if n <= 4:
        return 0.6
    return min(1.0, 0.6 + 0.1 * math.log2(n - 3))


def _adaptive_cold_fraction(query: str, base_fraction: float = 0.3) -> float:
    """Adapt cold_fraction based on query novelty (ToolkenGPT, §3.2).

    Low-entropy (generic/short) queries → more cold exploration (up to 0.5).
    High-entropy (specific/long) queries → less cold exploration (down to 0.2).
    """
    entropy = _estimate_query_entropy(query)
    # Map entropy 0..1 to cold_fraction 0.5..0.2 (inverse relationship)
    return base_fraction + 0.2 * (1.0 - entropy) - 0.1 * entropy


def _adaptive_iso_theta(query: str, base_theta: float = 0.05) -> float:
    """Adaptive ISO θ threshold (FIX 6b — Tool Attention §3.2).

    Low-entropy (generic/short) queries get a LOWER theta (more inclusive)
    because the intent is broad and many resources could match.
    High-entropy (specific/long) queries get a HIGHER theta (stricter gate)
    because the narrow intent demands high relevance.

    Range: 0.02 (1-word query) to 0.15 (8+ word query).
    """
    toks = _tokens(query)
    n = len(toks)
    if n <= 1:
        return max(0.0, base_theta - 0.03)  # 0.02
    if n <= 2:
        return base_theta  # 0.05
    if n <= 4:
        return min(base_theta + 0.03, 0.15)  # 0.08
    return min(base_theta + 0.10, 0.30)  # cap at 0.15 for very specific queries


def unified_route(query: str, top_k: int = 10, cold_fraction: float | None = None,
                  db_path: str | Path = DB_PATH, vec: bool = True,
                  weights: dict[str, float] | None = None,
                  clusters: list[str] | None = None,
                   use_chunks: bool = True,
                  fusion: str = "rrf",
                  iso_theta: float | None = None,
                  section_weight: float = 1.0) -> dict[str, Any]:
    """Full router_v2 pipeline for a query.

    Args:
        query: natural-language task description.
        top_k: total results to return (split 70/30 hot/cold by default).
        cold_fraction: fraction of top_k reserved for cold-tier (never-triggered)
            resources, UCB-driven. 0.0 disables two-tier (single blended list).
            None = auto (adaptive based on query entropy).
        db_path: path to nexus.db.
        vec: enable the bit[384] dense path (best-effort; falls back to
            lexical-only if the embedder or index is unavailable).
        weights: 4-term blend weights (defaults to DEFAULT_BLEND_WEIGHTS).
        clusters: when set, restrict retrieval to resources in the given
            clusters (HGMF L2 cluster-constrained topic; used by unified_route_v5).
            ``None`` = unconstrained (default, backward-compatible).
        use_chunks: when True, add a chunk-derived resource stream (FTS over
            ``chunks_fts`` + semantic over ``vec_chunks_content``, aggregated by
            ``resource_id``) as a third RRF source so the 66,495 ingestion
            chunks actively participate in routing. Best-effort: falls back to
            no chunk signal if the chunk embedder/index is unavailable or no
            chunks are linked to resources yet. Default ``False`` (backward-
            compatible; the live MCP only enables it behind ``USE_UNIFIED_ROUTER``).
        fusion: L2 fusion method — ``"rrf"`` (default, backward-compatible) or
            ``"cc"`` (convex combination, FIX 6 — Bruch et al. 2022).
        iso_theta: minimum ISO normalized score threshold (FIX 6b). When set,
            candidates with ``iso_n < iso_theta`` are filtered out before the
            4-term blend. ``None`` = auto-adaptive based on query entropy.
            Range: 0.02 (short queries) to 0.15 (long queries).
        section_weight: multiplicative weight for section-sourced chunk matches
            (default 1.0 — backward-compatible neutral). Passed to both
            ``_rrf_resource`` and ``_cc_resource``.

    Returns:
        ``{query, hot, cold, merged, hot_k, cold_k, used_dense, vec_reason,
        used_chunks, chunk_hits, weights, fusion, iso_theta, n}``. Each result
        in hot/cold/merged carries ``resource_id``, ``name``, ``cluster``,
        ``final_score``, ``rrf_score`` and the blend components (``iso``,
        ``thompson``, ``efc``, ``ucb``); chunk-sourced entries also carry
        ``chunk_id``, ``chunk_heading``, ``n_chunks``. When ``fusion="cc"``,
        entries also carry ``cc_score``, ``lex_score``, ``vec_score``,
        ``chunk_score``.
    """
    # FIX 4 (ToolkenGPT): adaptive cold_fraction based on query entropy
    if cold_fraction is None:
        cold_fraction = _adaptive_cold_fraction(query)

    # FIX 6b (Tool Attention): adaptive iso_theta based on query entropy
    if iso_theta is None:
        iso_theta = _adaptive_iso_theta(query)

    db_path = str(db_path)
    fts = _resource_search(query, db_path, limit=max(top_k * 5, 50), clusters=clusters)

    vec_results: list[dict] = []
    used_dense = False
    vec_reason = "disabled"
    if vec:
        vec_results, used_dense, vec_reason = _try_vec(query, db_path, k=max(top_k * 3, 30))
        if clusters and vec_results:
            vec_results = _filter_by_clusters(vec_results, db_path, clusters)

    # chunk-derived resource stream (66k chunks actively participating)
    chunk_stream: list[dict] = []
    used_chunks = False
    chunk_hits = 0
    if use_chunks:
        chunk_stream = _chunk_resource_stream(
            query, db_path, k=max(top_k * 3, 30), clusters=clusters)
        used_chunks = bool(chunk_stream)
        chunk_hits = len(chunk_stream)

    # resource-keyed fusion (RRF or CC, all keyed by resource_id)
    if fusion == "cc":
        fused = _cc_resource(fts, vec_results, chunks=chunk_stream,
                             section_weight=section_weight)
    else:
        fused = _rrf_resource(fts, vec_results, chunks=chunk_stream,
                              section_weight=section_weight)

    # 4-term blend (scoring.rank reads Thompson/EFC/UCB from the live DB)
    ranked = _scoring.rank(fused, db_path=db_path, weights=weights, iso_theta=iso_theta)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        # split hot (triggered) / cold (never triggered)
        if cold_fraction > 0.0:
            hot_k, cold_k = two_tier_split(top_k, cold_fraction)
            rids = [x.get("resource_id") for x in ranked if x.get("resource_id") is not None]
            triggered: set[int] = set()
            if rids:
                ph = ",".join("?" for _ in rids)
                for r in conn.execute(
                    f"SELECT id FROM resources WHERE id IN ({ph}) AND times_triggered > 0",
                    tuple(rids),
                ).fetchall():
                    triggered.add(r["id"])
            hot = [x for x in ranked if x.get("resource_id") in triggered][:hot_k]
            cold = [x for x in ranked if x.get("resource_id") not in triggered][:cold_k]
        else:
            hot_k, cold_k = top_k, 0
            hot = ranked[:top_k]
            cold = []
        merged = hot + cold
        # hydrate names (FTS path already has name/cluster; vec-only + chunk-only
        # entries need it). Use explicit assignment, not setdefault — _rrf_resource
        # pre-sets name=None for vec/chunk entries, and setdefault won't overwrite.
        all_rids = [x.get("resource_id") for x in merged if x.get("resource_id") is not None]
        names = _hydrate_names(conn, all_rids)
        for x in merged:
            rid = x.get("resource_id")
            meta = names.get(rid, {}) if rid is not None else {}
            if x.get("name") is None:
                x["name"] = meta.get("name")
            if x.get("cluster") is None:
                x["cluster"] = meta.get("cluster")
            if x.get("resource_type") is None:
                x["resource_type"] = meta.get("resource_type")
    finally:
        conn.close()

    return {
        "query": query,
        "hot": hot,
        "cold": cold,
        "merged": merged,
        "hot_k": hot_k,
        "cold_k": cold_k,
        "used_dense": used_dense,
        "vec_reason": vec_reason,
        "used_chunks": used_chunks,
        "chunk_hits": chunk_hits,
        "weights": weights or DEFAULT_BLEND_WEIGHTS,
        "fusion": fusion,
        "iso_theta": iso_theta,
        "n": len(merged),
    }


if __name__ == "__main__":
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "find a skill for fixing a login bug"
    out = unified_route(q, top_k=5)
    print(json.dumps({
        "query": out["query"],
        "used_dense": out["used_dense"],
        "vec_reason": out["vec_reason"],
        "weights": out["weights"],
        "n": out["n"],
        "merged": [
            {"name": x.get("name"), "cluster": x.get("cluster"),
             "final_score": round(x.get("final_score", 0), 4),
             "rrf_score": round(x.get("rrf_score", 0), 4)}
            for x in out["merged"]
        ],
    }, indent=2))