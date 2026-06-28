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

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

_SELF = Path(__file__).resolve().parent          # server/router_v2/
_SERVER = _SELF.parent                             # server/
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from router_v2.bitvec import BitIndex  # noqa: E402
from router_v2.fusion import (  # noqa: E402
    DEFAULT_BLEND_WEIGHTS,
    reciprocal_rank_fusion,
    two_tier_split,
)
from router_v2 import scoring as _scoring  # noqa: E402
from router_v2.lazy_embedder import LazyEmbedder, EmbedderUnavailableError  # noqa: E402
from router_v2.chunk_retrieval import retrieve_chunks as _retrieve_chunks  # noqa: E402

DB_PATH = _SELF.parent.parent / "db" / "nexus.db"

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
                     clusters: list[str] | None = None) -> list[dict]:
    """Lexical search over resources.name + description + body_text.

    Returns best-first list of ``{resource_id, name, cluster, score}`` where
    score is a simple relevance weight (name exact > name token > desc/body
    token). Used as the lexical source for RRF in place of the unlinked
    ``chunks_fts`` index. Scores are negative so that, like bm25, lower is
    better (RRF only uses ranks, so the sign does not affect fusion).

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
            like_clauses.append("name LIKE ? OR description LIKE ? OR body_text LIKE ?")
            params.extend([f"%{t}%", f"%{t}%", f"%{t}%"])
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
            score_terms.append(f"(CASE WHEN name LIKE ? THEN 10 ELSE 0 END)")
            params_score.append(f"%{t}%")
            score_terms.append(f"(CASE WHEN description LIKE ? THEN 3 ELSE 0 END)")
            params_score.append(f"%{t}%")
            score_terms.append(f"(CASE WHEN body_text LIKE ? THEN 1 ELSE 0 END)")
            params_score.append(f"%{t}%")
        sql = (
            "SELECT DISTINCT resources.id AS id, resources.name AS name, "
            "resources.cluster AS cluster, "
            + " + ".join(score_terms)
            + " AS match_score "
            f"FROM resources {cluster_join} "
            f"WHERE resources.status != 'disabled' {cluster_clause} AND ({where}) "
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
                              db_path=db_path, vec=True)
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
                  k: int = 60, chunk_weight: float = 0.5) -> list[dict]:
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

    Args:
        lex: best-first list of ``{resource_id, name?, cluster?}``.
        vec_results: best-first list of ``{resource_id, distance?}``.
        chunks: optional best-first list of ``{resource_id, chunk_rank}`` from
            :func:`_chunk_resource_stream` (the 66k chunks actively participating).
        k: RRF constant (default 60).

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
            rrf += chunk_weight * 1.0 / (k + e["chunk_rank"])
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


def unified_route(query: str, top_k: int = 10, cold_fraction: float = 0.3,
                  db_path: str | Path = DB_PATH, vec: bool = True,
                  weights: dict[str, float] | None = None,
                  clusters: list[str] | None = None,
                  use_chunks: bool = False) -> dict[str, Any]:
    """Full router_v2 pipeline for a query.

    Args:
        query: natural-language task description.
        top_k: total results to return (split 70/30 hot/cold by default).
        cold_fraction: fraction of top_k reserved for cold-tier (never-triggered)
            resources, UCB-driven. 0.0 disables two-tier (single blended list).
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

    Returns:
        ``{query, hot, cold, merged, hot_k, cold_k, used_dense, vec_reason,
        used_chunks, chunk_hits, weights, n}``. Each result in hot/cold/merged
        carries ``resource_id``, ``name``, ``cluster``, ``final_score``,
        ``rrf_score`` and the blend components (``iso``, ``thompson``, ``efc``,
        ``ucb``); chunk-sourced entries also carry ``chunk_id``, ``chunk_heading``,
        ``n_chunks``.
    """
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

    # resource-keyed RRF (lexical + semantic + chunk, all keyed by resource_id)
    fused = _rrf_resource(fts, vec_results, chunks=chunk_stream)

    # 4-term blend (scoring.rank reads Thompson/EFC/UCB from the live DB)
    ranked = _scoring.rank(fused, db_path=db_path, weights=weights)

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