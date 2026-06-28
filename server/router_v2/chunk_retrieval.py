"""Chunk-level retrieval: FTS5 (chunks_fts) + semantic (vec_chunks_content) -> RRF.

The function the NEXUS router calls to actually reach the 66,495 ingestion
chunks. Each hit is hydrated with its heading, content snippet, source path,
and — when the linker has populated resource_id — the resource name + cluster,
so chunk findings map back into the domain-cluster taxonomy.

  retrieve_chunks(query, top_k=10, clusters=None, db_path=DB)

Read-only w.r.t. the live MCP and Thompson tables.
"""
from __future__ import annotations

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

DB_PATH = _REPO / "db" / "nexus.db"
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


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _fts_search(con: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    match = _prepare_fts(query)
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


def _rrf(fts: list[dict], sem: list[dict], k: int = RRF_K) -> list[dict]:
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
    out = []
    for cid, e in acc.items():
        rrf = 0.0
        if e["fts_rank"] is not None:
            rrf += 1.0 / (k + e["fts_rank"])
        if e["sem_rank"] is not None:
            rrf += 1.0 / (k + e["sem_rank"])
        out.append({
            "chunk_id": cid, "rrf_score": rrf,
            "fts_rank": e["fts_rank"], "sem_rank": e["sem_rank"],
            "cos": e.get("cos"), "source": ("both" if e["fts_rank"] and e["sem_rank"]
                                            else ("fts" if e["fts_rank"] else "sem")),
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


def retrieve_chunks(query: str, top_k: int = 10, clusters: list[str] | None = None,
                    db_path: str | Path = DB_PATH, vec: bool = True,
                    snippet_len: int = 240) -> dict[str, Any]:
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

    Returns: ``{query, results, n, used_semantic, latency_ms}`` where each result
        carries chunk_id, heading, snippet, source_path, resource_id,
        resource_name, cluster, rrf_score, source, cos.
    """
    db_path = str(db_path)
    t0 = time.perf_counter()
    pool = max(top_k * 5, 50)

    con = _connect(db_path)
    fts = _fts_search(con, query, limit=pool)

    sem: list[dict] = []
    used_semantic = False
    if vec:
        try:
            embedder = ChunkEmbedder(db_path)
            idx = ChunkIndex(db_path, embedder=embedder)
            sem = _sem_search(idx, embedder, query, limit=pool)
            used_semantic = bool(sem)
            idx.con.close()
        except Exception:
            sem = []

    fused = _rrf(fts, sem)[:top_k * 2]  # over-fetch for cluster filtering

    cids = [r["chunk_id"] for r in fused]
    meta = _hydrate(con, cids, snippet_len=snippet_len)
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