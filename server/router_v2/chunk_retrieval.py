"""Chunk-level retrieval: FTS5 (chunks_fts) + semantic (vec_chunks_content) -> RRF.

The function the NEXUS router calls to actually reach the 66,495 ingestion
chunks. Each hit is hydrated with its heading, content snippet, source path,
and — when the linker has populated resource_id — the resource name + cluster,
so chunk findings map back into the domain-cluster taxonomy.

  retrieve_chunks(query, top_k=10, clusters=None, db_path=DB)

Read-only w.r.t. the live MCP and Thompson tables.
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path

import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_SELF = Path(__file__).resolve().parent
_SERVER = _SELF.parent
_REPO = _SERVER.parent
for _p in (str(_REPO), str(_SERVER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from router_v2.chunk_embedder import ChunkEmbedder  # noqa: E402
from router_v2.chunk_index import ChunkIndex  # noqa: E402
from kg.graph_lane import (expand_over_edges, lane_enabled,  # noqa: E402
                           map_neighbors_to_chunks)

DB_PATH = _resolve_db_path()
RRF_K = 60

# FTS5 stopwords / operators stripped from the MATCH query
_FTS_SPECIAL = set('*:^"-+()')
_FTS_STOP = {"a", "an", "the", "and", "or", "not", "in", "on", "at", "to",
             "for", "of", "is", "it", "this", "that", "with", "by", "be", "as",
             "how", "do", "i", "my", "we", "you"}


def _prepare_fts(query: str) -> str:
    cleaned = "".join(" " if ch in _FTS_SPECIAL else ch for ch in (query or ""))
    toks = [t for t in re.split(r"\s+", cleaned.lower()) if t and t not in _FTS_STOP]
    return " ".join(f'"{t}"' for t in toks)


def _prepare_fts_any(query: str) -> str:
    """OR-join variant of :func:`_prepare_fts`.

    Used by BOTH the sections arm and the chunks arm. The chunks arm originally
    kept the AND form ("baseline fidelity") on the assumption that the semantic
    arm carries recall for partial matches — but with chunk_embeddings empty
    the semantic arm returns nothing, and the golden set (2026-11-19,
    evals/results/perf/golden-baseline-e2.json) measured the AND form at
    1.2% hit@5 on the chunk lane: a 7-token prompt requires a chunk containing
    ALL seven tokens, which almost never happens. OR + bm25 ordering lets
    partial matches rank by relevance instead of vanishing. The change is
    gated by the E2 eval harness — re-run it after any revert.
    """
    cleaned = "".join(" " if ch in _FTS_SPECIAL else ch for ch in (query or ""))
    toks = [t for t in re.split(r"\s+", cleaned.lower()) if t and t not in _FTS_STOP]
    if not toks:
        return ""
    return " OR ".join(f'"{t}"' for t in toks)


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _fts_search(con: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    # OR form: see _prepare_fts_any — the AND form measured 1.2% hit@5 on the
    # golden set with the semantic arm empty (E2 baseline, 2026-11-19).
    match = _prepare_fts_any(query)
    if not match:
        return []
    try:
        rows = con.execute(
            "SELECT f.rowid AS chunk_id, bm25(chunks_fts) AS score "
            "FROM chunks_fts f WHERE chunks_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    # bm25 returns negative scores (lower=better); invert so higher=better
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({"chunk_id": int(r["chunk_id"]), "fts_rank": i, "fts_raw": float(r["score"])})
    return out


def _fts_search_sections(con: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    """FTS5 over ``sections_fts`` (OKF corpus arm, D1). Sibling of ``_fts_search``.

    Rows are normalized to carry ``chunk_id`` = ``resource_sections.id`` so the
    shared ``_rrf`` (keyed on chunk_id) works untouched. Sections and chunks are
    never fused in one call (one corpus per call), so the id spaces never collide.

    Uses the OR variant (:func:`_prepare_fts_any`) — see its docstring; the
    chunks arm keeps the AND form for baseline fidelity.
    """
    match = _prepare_fts_any(query)
    if not match:
        return []
    try:
        rows = con.execute(
            "SELECT f.rowid AS section_id, bm25(sections_fts) AS score "
            "FROM sections_fts f WHERE sections_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({"chunk_id": int(r["section_id"]), "fts_rank": i, "fts_raw": float(r["score"])})
    return out


def _sem_search(idx: ChunkIndex, embedder: ChunkEmbedder, query: str,
                limit: int) -> list[dict]:
    try:
        qvec = embedder.encode_one(query)
    except Exception:
        return []
    cands = idx.search(qvec, k=max(limit * 3, 30))
    if not cands:
        return []
    reranked = idx.rerank(qvec, cands, k=limit)
    return [{"chunk_id": r["chunk_id"], "cos": r["cos"], "hamming": r["hamming"]}
            for r in reranked]


def _sem_search_sections(db_path: str, query: str, limit: int) -> list[dict]:
    """Best-effort semantic arm over ``vec_sections_content`` (section grain).

    Returns [] until the section vec index is populated (backfill via
    ``okf_sections.backfill_section_embeddings``). Rows carry ``chunk_id`` =
    section id for the shared ``_rrf``.
    """
    try:
        from router_v2.chunk_index import SectionIndex
        with SectionIndex(db_path) as idx:
            if idx.ntotal() == 0:
                return []
            embedder = idx.embedder
            qvec = embedder.encode_one(query)
            cands = idx.search(qvec, k=max(limit * 3, 30))
            if not cands:
                return []
            reranked = idx.rerank(qvec, cands, k=limit)
            return [{"chunk_id": r["chunk_id"], "cos": r["cos"], "hamming": r["hamming"]}
                    for r in reranked]
    except Exception:
        return []


def _rrf(fts: list[dict], sem: list[dict], k: int = RRF_K,
         graph: list[dict] | None = None,
         graph_weight: float = 0.95) -> list[dict]:
    """RRF over the FTS + semantic chunk lists, plus an optional graph-lane
    list (second-brain-kg L1-b). ``graph`` entries are ``{"chunk_id": ...}``
    already ranked best-first (neighbors resolved to representative chunks)
    and contribute ``graph_weight / (k + rank)`` (tuned 0.95 on the golden
    suite — evals/results/lane_sweep2_*.json; 0.5-0.85 is inert because a
    weighted graph entry never clears the top-k RRF cutoff). Absent/empty
    ``graph`` reproduces the pre-lane output exactly, for any weight.
    """
    acc: dict[int, dict] = {}
    for i, r in enumerate(fts, start=1):
        cid = r["chunk_id"]
        e = acc.setdefault(cid, {"chunk_id": cid, "fts_rank": None, "sem_rank": None})
        e["fts_rank"] = i
    for i, r in enumerate(sem, start=1):
        cid = r["chunk_id"]
        e = acc.setdefault(cid, {"chunk_id": cid, "fts_rank": None, "sem_rank": None})
        e["sem_rank"] = i
        e["cos"] = r.get("cos")
    for i, r in enumerate(graph or [], start=1):
        cid = r["chunk_id"]
        e = acc.setdefault(cid, {"chunk_id": cid, "fts_rank": None, "sem_rank": None})
        e["graph_rank"] = i
    out = []
    for cid, e in acc.items():
        rrf = 0.0
        if e["fts_rank"] is not None:
            rrf += 1.0 / (k + e["fts_rank"])
        if e["sem_rank"] is not None:
            rrf += 1.0 / (k + e["sem_rank"])
        if e.get("graph_rank") is not None:
            rrf += graph_weight / (k + e["graph_rank"])
        parts = [n for n in ("fts", "sem", "graph") if e.get(f"{n}_rank") is not None]
        if parts == ["fts", "sem"]:        # pre-lane label preserved
            source = "both"
        else:
            source = "+".join(parts)
        out.append({
            "chunk_id": cid, "rrf_score": rrf,
            "fts_rank": e["fts_rank"], "sem_rank": e["sem_rank"],
            "graph_rank": e.get("graph_rank"),
            "cos": e.get("cos"), "source": source,
        })
    out.sort(key=lambda x: x["rrf_score"], reverse=True)
    return out


def _hydrate(con: sqlite3.Connection, chunk_ids: list[int],
             snippet_len: int = 240) -> dict[int, dict]:
    if not chunk_ids:
        return {}
    ph = ",".join("?" for _ in chunk_ids)
    rows = con.execute(
        f"SELECT c.id, c.heading, c.content, c.source_id, c.resource_id, "
        f"c.chunk_type, s.source_path "
        f"FROM ingestion_chunks c "
        f"LEFT JOIN ingestion_sources s ON s.id = c.source_id "
        f"WHERE c.id IN ({ph})",
        tuple(chunk_ids),
    ).fetchall()
    out = {}
    for r in rows:
        cid = int(r["id"])
        content = r["content"] or ""
        out[cid] = {
            "chunk_id": cid,
            "heading": r["heading"],
            "snippet": content[:snippet_len].replace("\n", " ").strip(),
            "chunk_type": r["chunk_type"],
            "source_id": r["source_id"],
            "source_path": r["source_path"],
            "resource_id": r["resource_id"],
        }
    return out


def _hydrate_resources(con: sqlite3.Connection, rids: list[int]) -> dict[int, dict]:
    if not rids:
        return {}
    ph = ",".join("?" for _ in rids)
    rows = con.execute(
        f"SELECT id, name, cluster, resource_type, repo_name FROM resources WHERE id IN ({ph})",
        tuple(rids),
    ).fetchall()
    return {int(r["id"]): dict(r) for r in rows}


def _hydrate_sections(con: sqlite3.Connection, section_ids: list[int],
                      snippet_len: int = 240) -> dict[int, dict]:
    """Hydrate ``resource_sections`` rows (OKF corpus arm). Sibling of ``_hydrate``.

    Returns the same shape ``_hydrate`` produces (``chunk_id`` = section id,
    ``chunk_type`` = "section", ``source_path`` None, resource fields left for
    the shared ``_hydrate_resources``) so the result-assembly loop stays
    corpus-agnostic — one code path, both corpora.
    """
    if not section_ids:
        return {}
    ph = ",".join("?" for _ in section_ids)
    rows = con.execute(
        f"SELECT id, heading, content, resource_id "
        f"FROM resource_sections WHERE id IN ({ph})",
        tuple(section_ids),
    ).fetchall()
    out = {}
    for r in rows:
        sid = int(r["id"])
        content = r["content"] or ""
        out[sid] = {
            "chunk_id": sid,
            "heading": r["heading"],
            "snippet": content[:snippet_len].replace("\n", " ").strip(),
            "chunk_type": "section",
            "source_id": None,
            "source_path": None,
            "resource_id": r["resource_id"],
        }
    return out


def retrieve_chunks(query: str, top_k: int = 10, clusters: list[str] | None = None,
                    db_path: str | Path = DB_PATH, vec: bool = True,
                    snippet_len: int = 240, corpus: str = "chunks",
                    graph_lane: bool | None = None) -> dict[str, Any]:
    """Retrieve chunk hits for a natural-language query.

    Args:
        query: natural-language task description.
        top_k: number of chunk hits to return.
        clusters: when set, post-filter to chunks linked to a resource in one of
            these clusters (best-effort — unlinked chunks are kept only if
            clusters is None).
        db_path: path to nexus.db.
        vec: enable the semantic path (best-effort; falls back to FTS-only).
        snippet_len: content snippet character length.
        corpus: "chunks" (ingestion_chunks, today's default) or "sections"
            (OKF resource_sections / sections_fts, decision D1). One corpus per
            call; they are never fused together.
        graph_lane: None = env-driven (NEXUS_GRAPH_LANE, default ON); True/False
            forces the graph-lane fold on/off regardless of env. The router's
            chunk stream passes False — its v5 ensemble is top-1-calibrated and
            the lane costs ~2 passes there, while RAG consumers gain +69%
            recall (golden suite; see second-brain-kg L1-b evidence).

    Returns: ``{query, results, n, used_semantic, latency_ms}`` where each result
        carries chunk_id, heading, snippet, source_path, resource_id,
        resource_name, cluster, rrf_score, source, cos.
    """
    db_path = str(db_path)
    t0 = time.perf_counter()
    pool = max(top_k * 5, 50)

    con = _connect(db_path)
    if corpus == "sections":
        fts = _fts_search_sections(con, query, limit=pool)
    elif corpus == "chunks":
        fts = _fts_search(con, query, limit=pool)
    else:
        raise ValueError(f"unknown corpus {corpus!r} (expected 'chunks'|'sections')")

    sem: list[dict] = []
    used_semantic = False
    if vec:
        if corpus == "sections":
            sem = _sem_search_sections(db_path, query, limit=pool)
            used_semantic = bool(sem)
        else:
            try:
                embedder = ChunkEmbedder(db_path)
                idx = ChunkIndex(db_path, embedder=embedder)
                sem = _sem_search(idx, embedder, query, limit=pool)
                used_semantic = bool(sem)
                idx.con.close()
            except Exception:
                sem = []

    text_fused = _rrf(fts, sem)          # full text-only ranking
    fused = text_fused[:top_k * 2]       # over-fetch for cluster filtering

    cids = [r["chunk_id"] for r in fused]
    meta = (_hydrate_sections(con, cids, snippet_len=snippet_len)
            if corpus == "sections" else _hydrate(con, cids, snippet_len=snippet_len))

    # graph lane (second-brain-kg L1-b): 1-hop typed-edge expansion over the
    # strongest seeds, folded as a third RRF list. NEXUS_GRAPH_LANE=0 disables;
    # graph_lane=False (router chunk stream) overrides env.
    _lane_on = lane_enabled() if graph_lane is None else graph_lane
    if text_fused and _lane_on:
        _hydrate_lane = (_hydrate_sections if corpus == "sections" else _hydrate)
        extra = [r["chunk_id"] for r in text_fused if r["chunk_id"] not in meta]
        if extra:                          # resource map for the whole text pool
            meta.update(_hydrate_lane(con, extra, snippet_len=snippet_len))
        seed_rids: list[int] = []
        _seen: set[int] = set()
        for f in fused:
            rid = (meta.get(f["chunk_id"]) or {}).get("resource_id")
            if rid is not None and rid not in _seen:
                _seen.add(rid)
                seed_rids.append(rid)
        graph_list: list[dict] = []
        try:
            try:
                _max_seeds = int(os.environ.get("NEXUS_GRAPH_LANE_SEEDS", "5"))
            except ValueError:
                _max_seeds = 10
            try:
                _per_seed = int(os.environ.get("NEXUS_GRAPH_LANE_PER_SEED", "5"))
            except ValueError:
                _per_seed = 5
            neighbors = expand_over_edges(
                seed_rids, con, max_seeds=_max_seeds, per_seed_cap=_per_seed)
            if clusters:
                # Neighbors must respect the caller's cluster constraint too:
                # cross-cluster graph entries used to occupy the fused window
                # and evict in-cluster candidates (cluster_ok 0.44→0.37 on the
                # golden suite; the lane post-filter could not recover them).
                _nrm = _hydrate_resources(
                    con, [n["resource_id"] for n in neighbors])
                neighbors = [n for n in neighbors
                             if _nrm.get(n["resource_id"], {}).get("cluster")
                             in clusters]
            # NEXUS_GRAPH_LANE_MODE:
            #   "promote" (default, tuned on the golden suite) — attach each
            #              neighbor to its BEST text-ranked chunk (candidate
            #              set unchanged, graph only re-ranks); without this
            #              attachment promote is inert (representative ids
            #              rarely overlap the text pool);
            #   "inject"  — representative chunk per neighbor, surfaces
            #              resources the text arms miss entirely (spec
            #              original; cost ~3 router passes vs off-band).
            if os.environ.get("NEXUS_GRAPH_LANE_MODE", "promote") == "promote":
                rid_to_best: dict[int, int] = {}
                for r in text_fused:       # rank asc: first hit wins
                    rid = (meta.get(r["chunk_id"]) or {}).get("resource_id")
                    if rid is not None and rid not in rid_to_best:
                        rid_to_best[rid] = r["chunk_id"]
                graph_list = [
                    {"chunk_id": rid_to_best[n["resource_id"]]}
                    for n in neighbors if n["resource_id"] in rid_to_best
                ]
            else:
                graph_list = [
                    {"chunk_id": cid}
                    for _, cid in map_neighbors_to_chunks(
                        con, [n["resource_id"] for n in neighbors], corpus=corpus)
                ]
        except Exception:
            graph_list = []               # lane is best-effort, never breaks retrieval
        if graph_list:
            try:
                gw = float(os.environ.get("NEXUS_GRAPH_LANE_WEIGHT", "0.95"))
            except ValueError:
                gw = 0.95
            fused = _rrf(fts, sem, graph=graph_list,
                         graph_weight=gw)[:top_k * 2]
            new_cids = [f["chunk_id"] for f in fused if f["chunk_id"] not in meta]
            if new_cids:
                meta.update(_hydrate_lane(con, new_cids, snippet_len=snippet_len))

    rids = [m["resource_id"] for m in meta.values() if m["resource_id"] is not None]
    rmeta = _hydrate_resources(con, rids)

    results = []
    for f in fused:
        m = meta.get(f["chunk_id"])
        if m is None:
            continue
        rm = rmeta.get(m["resource_id"], {}) if m["resource_id"] is not None else {}
        if clusters is not None:
            cl = rm.get("cluster")
            if cl not in clusters:
                continue  # filter out chunks not in requested clusters (incl. unlinked)
        results.append({
            "chunk_id": f["chunk_id"],
            "heading": m["heading"],
            "snippet": m["snippet"],
            "chunk_type": m["chunk_type"],
            "source_path": m["source_path"],
            "resource_id": m["resource_id"],
            "resource_name": rm.get("name"),
            "cluster": rm.get("cluster"),
            "repo_name": rm.get("repo_name"),
            "rrf_score": round(f["rrf_score"], 5),
            "source": f["source"],
            "cos": round(f["cos"], 4) if f.get("cos") is not None else None,
        })
        if len(results) >= top_k:
            break

    con.close()
    return {
        "query": query,
        "results": results,
        "n": len(results),
        "used_semantic": used_semantic,
        "fts_hits": len(fts),
        "sem_hits": len(sem),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


if __name__ == "__main__":
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "fix a login registration bug"
    out = retrieve_chunks(q, top_k=8)
    print(json.dumps({
        "query": out["query"], "n": out["n"],
        "used_semantic": out["used_semantic"],
        "fts_hits": out["fts_hits"], "sem_hits": out["sem_hits"],
        "latency_ms": out["latency_ms"],
        "results": [{"chunk_id": r["chunk_id"], "heading": r["heading"],
                      "resource": r["resource_name"], "cluster": r["cluster"],
                      "source": r["source"], "rrf": r["rrf_score"],
                      "snippet": r["snippet"][:120]}
                     for r in out["results"]],
    }, indent=2))