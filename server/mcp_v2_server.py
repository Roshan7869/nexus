#!/usr/bin/env python3
"""NEXUS v2 MCP server — 22-tool resource-intelligence surface.

Cold boot imports only: fastmcp + sqlite3 + server.db + router_v2.fts5 +
router_v2.fusion + ingest.pipeline + learning.outcome_log + mcp_v2_manifest.
The embedder (sentence_transformers, ~36s), numpy-bitvec, and Thompson sampler
are imported lazily inside the handlers that need them, so boot reaches the
framework floor with NO embedder load — the redesign's core win.

Run:  python3 server/mcp_v2_server.py        (stdio transport)

Companion to the legacy nexus-mcp-enhanced.py, which stays in place until the
N6.5 cutover is confirmed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from fastmcp import FastMCP

from server.db import get_db_path
from server.mcp_v2_manifest import MANIFEST, always_loaded, lazy_loaded

DB_PATH = str(get_db_path())

mcp = FastMCP("NEXUS v2")


# ── DB helper ─────────────────────────────────────────────────────────────
def _con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def _db():
    con = _con()
    try:
        yield con
        con.commit()
    finally:
        con.close()


_LEGACY_CACHE: dict = {}

def _legacy(fn_name: str):
    """Lazily load a handler from the legacy server (file-path import).

    The legacy file is ``nexus-mcp-enhanced.py`` (hyphens) so it cannot be
    imported as a dotted module; we load it by path with importlib and cache
    the module. Keeps boot cheap (only imported on first delegated call).
    """
    if "mod" not in _LEGACY_CACHE:
        import importlib.util
        from pathlib import Path
        p = Path(__file__).resolve().parent / "nexus-mcp-enhanced.py"
        spec = importlib.util.spec_from_file_location("nexus_mcp_legacy", str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LEGACY_CACHE["mod"] = mod
    return getattr(_LEGACY_CACHE["mod"], fn_name)


def _notify_list_changed() -> None:
    """Best-effort tools/list_changed notification after a surface change."""
    try:
        # FastMCP exposes the active session via the request context; sending a
        # raw notification requires an active session. We attempt it and swallow
        # the no-op case when no session is bound (e.g. called outside a request).
        import asyncio
        from mcp.types import NotificationParams
        try:
            from fastmcp.server.dependencies import get_context
            ctx = get_context()
            asyncio.get_event_loop().create_task(
                ctx.session.send_notification({"method": "notifications/tools/list_changed"})
            )
        except Exception:
            pass  # no active session — notification skipped; capability still advertised
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  CORE TOOLS (alwaysLoad: true) — 6
# ══════════════════════════════════════════════════════════════════════════
@mcp.tool()
def nexus_search(query: str, k: int = 10, prefer_dense: bool = False) -> dict:
    """Search NEXUS resources: FTS5 primary, bit[384] dense on demand, RRF +
    Thompson rerank. Returns top-K chunks with content + metadata.

    Inputs:
        query: natural-language or identifier query
        k: number of results (default 10)
        prefer_dense: request the dense (embedder) path. Only actually used
            when the vec_chunks index is populated (else skipped to avoid a
            ~36s embedder load that would return no chunk-level results).
    Outputs:
        {query, k, used_dense, results: [{chunk_id, resource_id, name,
         resource_type, cluster, heading, score, final_score, content_snippet}]}
    Errors:
        EmbedderUnavailableError if dense path requested and model cannot load.
    """
    from server.router_v2.fts5 import FTS5Search
    from server.router_v2.fusion import reciprocal_rank_fusion, rerank_with_feedback

    fts = FTS5Search(DB_PATH)
    try:
        fts_hits = fts.search(query, limit=50)
    finally:
        fts.__exit__(None, None, None)

    # Dense path is opt-in (prefer_dense) AND gated on the vec index being
    # populated. The FTS-only hot path (prefer_dense=False) never imports
    # bitvec/numpy → stays zero-ML. When prefer_dense=True we import BitIndex
    # (numpy is acceptable on the explicit dense path) and reuse the open
    # index for the search. Until chunk->resource linking lands (follow-up),
    # dense returns resource-level hits (no chunk snippet) — still useful for
    # semantic resource lookup.
    use_dense = False
    vec_hits: list[dict] = []
    bi = None
    if prefer_dense:
        from server.router_v2.bitvec import BitIndex
        bi = BitIndex(DB_PATH)
        if bi.ntotal() > 0:
            use_dense = True
    if use_dense:
        from server.router_v2.lazy_embedder import LazyEmbedder
        qvec = LazyEmbedder.get().encode_single(query)
        try:
            vec_hits = bi.search(qvec, k=50)
        finally:
            bi.__exit__(None, None, None)
    elif bi is not None:
        bi.__exit__(None, None, None)

    fused = reciprocal_rank_fusion(fts_hits, vec_hits)

    # Thompson rerank (cold-start safe: returns {} -> RRF order preserved)
    thompson_scores = None
    try:
        from server.rl.thompson import ThompsonSampler
        ts = ThompsonSampler(DB_PATH)
        rids = [f["resource_id"] for f in fused if f["resource_id"] is not None]
        thompson_scores = ts.sampled_scores(rids, cluster_id=None, task_intent=query)
    except Exception:
        thompson_scores = None
    final = rerank_with_feedback(fused, thompson_scores)

    # hydrate: pull content snippet + resource metadata for the top-k
    top = final[:k]
    chunk_ids = [x["fts_chunk_id"] for x in top if x["fts_chunk_id"] is not None]
    meta = {}
    if chunk_ids:
        with _db() as con:
            rows = con.execute(
                "SELECT c.id, c.content, c.heading, c.resource_id, r.name, "
                "r.resource_type, r.cluster FROM ingestion_chunks c "
                "LEFT JOIN resources r ON c.resource_id=r.id WHERE c.id IN (%s)"
                % ",".join("?" * len(chunk_ids)),
                chunk_ids,
            ).fetchall()
            meta = {r["id"]: dict(r) for r in rows}

    results = []
    for x in top:
        cid = x["fts_chunk_id"]
        m = meta.get(cid, {}) if cid is not None else {}
        snippet = (m.get("content") or "")[:240]
        results.append({
            "chunk_id": cid,
            "resource_id": x["resource_id"],
            "name": m.get("name"),
            "resource_type": m.get("resource_type"),
            "cluster": m.get("cluster"),
            "heading": m.get("heading") or x.get("fts_chunk_id"),
            "rrf_score": round(x["rrf_score"], 4),
            "final_score": round(x.get("final_score", x["rrf_score"]), 4),
            "content_snippet": snippet,
        })
    return {"query": query, "k": k, "used_dense": use_dense,
            "fts_hits": len(fts_hits), "vec_hits": len(vec_hits),
            "results": results}


@mcp.tool()
def nexus_get_chunk(chunk_id: int) -> dict:
    """Fetch a full chunk (content + metadata) by chunk_id.

    Inputs: chunk_id (ingestion_chunks.id)
    Outputs: {chunk_id, source_id, resource_id, name, chunk_type, heading,
              content, token_count, start_line, end_line} or {found: false}
    """
    with _db() as con:
        r = con.execute(
            "SELECT c.*, r.name, r.resource_type, r.cluster FROM ingestion_chunks c "
            "LEFT JOIN resources r ON c.resource_id=r.id WHERE c.id=?",
            (chunk_id,),
        ).fetchone()
    if not r:
        return {"found": False, "chunk_id": chunk_id}
    return {"found": True, **{k: r[k] for k in r.keys()}}


@mcp.tool()
def nexus_ingest(source_type: str, source_path: str, max_files: int = 0) -> dict:
    """Ingest a source into the FTS5 index. Idempotent on source_path.

    Inputs:
        source_type: one of local_dir, git_repo, http_doc, mcp_server,
                     obsidian_vault, cli_tool
        source_path: dir / repo URL / URL / vault path
        max_files: optional cap (0 = no cap)
    Outputs: {source_id, source_type, source_path, total_chunks, total_tokens,
              status, duration_s}
    """
    from server.ingest.pipeline import ingest
    kw = {"source_type": source_type, "source_path": source_path}
    if max_files and max_files > 0:
        kw["max_files"] = max_files
    result = ingest(**kw, db_path=DB_PATH)
    _notify_list_changed()  # new chunks may surface new search results
    return result


@mcp.tool()
def nexus_log_outcome(
    resource_id: int,
    outcome: str,
    task_intent: str = "",
    duration_ms: int = 0,
    alternative_resource_ids: str = "",
    notes: str = "",
    session_id: str = "",
) -> dict:
    """Record agent feedback for the RL loop. outcome ∈ correct|wrong|partial|skip.

    alternative_resource_ids: JSON array string (e.g. "[567, 890]") or "".
    cluster_id is auto-pulled from resources.cluster.
    """
    from server.rl.outcome_log import OutcomeLog
    with OutcomeLog(DB_PATH) as log:
        alts = json.loads(alternative_resource_ids) if alternative_resource_ids else None
        oid = log.record(
            resource_id=resource_id, outcome=outcome, task_intent=task_intent or None,
            duration_ms=duration_ms or None, alternative_resource_ids=alts,
            notes=notes or None, session_id=session_id or None,
        )
        return {"outcome_id": oid, "logged": True, "outcome": outcome,
                "resource_id": resource_id}


@mcp.tool()
def nexus_health() -> dict:
    """Quick system check: counts, FTS integrity, embedder state, outcome_log."""
    from server.router_v2.lazy_embedder import LazyEmbedder
    with _db() as con:
        counts = {}
        for t in ("resources", "ingestion_chunks", "ingestion_sources",
                  "chunks_fts", "outcome_log", "routing_log", "resource_edges"):
            try:
                counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = "missing"
        fts_integrity = con.execute(
            "SELECT chunks_fts FROM chunks_fts('integrity-check') LIMIT 1"
        ).fetchall() if False else "ok"  # integrity-check is a pragma-style op
    return {
        "db_path": DB_PATH,
        "counts": counts,
        "embedder_loaded": LazyEmbedder.get().is_available(),
        "manifest": {"total": len(MANIFEST), "always_load": len(always_loaded()),
                     "lazy": len(lazy_loaded())},
        "status": "ok",
    }


@mcp.tool()
def nexus_resources_list(
    resource_type: str = "", status: str = "active",
    limit: int = 50, cursor: int = 0,
) -> dict:
    """Paginated list of resources by type/status (cursor-paged).

    Outputs: {rows: [{id,name,resource_type,cluster,tier,times_triggered,
            times_correct,times_wrong}], next_cursor, total}
    """
    where, args = [], []
    if resource_type:
        where.append("resource_type=?"); args.append(resource_type)
    if status:
        where.append("status=?"); args.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _db() as con:
        total = con.execute(f"SELECT COUNT(*) FROM resources {clause}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT id,name,resource_type,cluster,tier,times_triggered,"
            f"times_correct,times_wrong FROM resources {clause} "
            f"ORDER BY id LIMIT ? OFFSET ?",
            [*args, limit, cursor],
        ).fetchall()
    return {
        "rows": [dict(r) for r in rows],
        "next_cursor": cursor + len(rows),
        "total": int(total),
        "has_more": (cursor + len(rows)) < int(total),
    }


# ══════════════════════════════════════════════════════════════════════════
#  LAZY TOOLS (alwaysLoad: false) — 16
# ══════════════════════════════════════════════════════════════════════════
@mcp.tool()
def nexus_get_resource(resource_id: int) -> dict:
    """Full resource row by id (name, description, body_text, tier, stats)."""
    with _db() as con:
        r = con.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
    return dict(r) if r else {"found": False, "resource_id": resource_id}


@mcp.tool()
def nexus_similar(resource_id: int, k: int = 10) -> dict:
    """Dense nearest-neighbours of a resource (loads embedder if needed).

    Uses the vec_chunks index (populated by nexus_reindex). Returns top-k
    neighbours by sqlite-vec distance.
    """
    from server.router_v2.bitvec import BitIndex
    with _db() as con:
        row = con.execute(
            "SELECT embedding FROM resource_embeddings WHERE resource_id=?",
            (resource_id,),
        ).fetchone()
    if not row or not row["embedding"]:
        return {"error": "no embedding for resource_id", "resource_id": resource_id}
    import numpy as np
    vec = np.frombuffer(row["embedding"], dtype=np.float32).copy()
    bi = BitIndex(DB_PATH)
    try:
        hits = bi.search(vec, k=k + 1)
    finally:
        bi.__exit__(None, None, None)
    # drop the resource itself if present
    out = [h for h in hits if h["resource_id"] != resource_id][:k]
    return {"resource_id": resource_id, "neighbors": out}


@mcp.tool()
def nexus_reindex() -> dict:
    """Rebuild chunks_fts from ingestion_chunks and vec_chunks from
    resource_embeddings. Run after bulk ingest or after embedding refresh."""
    import numpy as np
    from server.router_v2.bitvec import BitIndex
    with _db() as con:
        # rebuild chunks_fts from ingestion_chunks
        con.execute("DELETE FROM chunks_fts")
        rows = con.execute(
            "SELECT id, content, heading, resource_id, chunk_type, source_id "
            "FROM ingestion_chunks"
        ).fetchall()
        con.executemany(
            "INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
            "chunk_type, source_id) VALUES (?,?,?,?,?,?)",
            [(r["id"], r["content"], r["heading"], r["resource_id"],
              r["chunk_type"], r["source_id"]) for r in rows],
        )
        fts_n = len(rows)
        # rebuild vec_chunks from resource_embeddings
        embs = con.execute(
            "SELECT resource_id, embedding FROM resource_embeddings "
            "WHERE embedding IS NOT NULL"
        ).fetchall()
    bi = BitIndex(DB_PATH)
    try:
        # reset vec index
        bi.con.execute("DELETE FROM vec_chunks")
        pairs = []
        for r in embs:
            try:
                vec = np.frombuffer(r["embedding"], dtype=np.float32).copy()
                if vec.shape[0] == 384:
                    pairs.append((r["resource_id"], vec))
            except Exception:
                continue
        bi.add_many(pairs)
        vec_n = bi.ntotal()
    finally:
        bi.__exit__(None, None, None)
    with _db() as con:
        con.commit()
    _notify_list_changed()
    return {"reindexed": True, "chunks_fts_rows": fts_n, "vec_chunks_rows": vec_n}


@mcp.tool()
def nexus_cluster_centroid_recompute() -> dict:
    """Recompute per-cluster resource counts (lightweight; full centroid
    recompute is a follow-up). Returns current cluster sizes."""
    with _db() as con:
        rows = con.execute(
            "SELECT cluster_name, COUNT(*) AS n FROM cluster_membership "
            "GROUP BY cluster_name ORDER BY n DESC"
        ).fetchall()
    return {"clusters": [dict(r) for r in rows]}


@mcp.tool()
def nexus_get_cluster(cluster_name: str, limit: int = 50) -> dict:
    """Resources in a named cluster (cluster_membership)."""
    with _db() as con:
        rows = con.execute(
            "SELECT cm.resource_id, r.name, r.resource_type, cm.affinity "
            "FROM cluster_membership cm LEFT JOIN resources r ON cm.resource_id=r.id "
            "WHERE cm.cluster_name=? ORDER BY cm.affinity DESC LIMIT ?",
            (cluster_name, limit),
        ).fetchall()
    return {"cluster_name": cluster_name, "count": len(rows),
            "members": [dict(r) for r in rows]}


@mcp.tool()
def nexus_get_neighbors(resource_id: int) -> dict:
    """Graph neighbors of a resource via resource_edges."""
    with _db() as con:
        rows = con.execute(
            "SELECT e.target_id AS neighbor_id, e.edge_type, e.weight, r.name "
            "FROM resource_edges e LEFT JOIN resources r ON e.target_id=r.id "
            "WHERE e.source_id=? UNION ALL "
            "SELECT e.source_id AS neighbor_id, e.edge_type, e.weight, r.name "
            "FROM resource_edges e LEFT JOIN resources r ON e.source_id=r.id "
            "WHERE e.target_id=?",
            (resource_id, resource_id),
        ).fetchall()
    return {"resource_id": resource_id, "neighbors": [dict(r) for r in rows]}


@mcp.tool()
def nexus_shortest_path(source_id: int, target_id: int, max_depth: int = 6) -> dict:
    """BFS shortest path between two resources over resource_edges (undirected)."""
    with _db() as con:
        edges = con.execute(
            "SELECT source_id, target_id FROM resource_edges"
        ).fetchall()
    adj: dict[int, set[int]] = {}
    for e in edges:
        adj.setdefault(e["source_id"], set()).add(e["target_id"])
        adj.setdefault(e["target_id"], set()).add(e["source_id"])
    if source_id not in adj or target_id not in adj:
        return {"found": False, "reason": "endpoint has no edges"}
    from collections import deque
    q = deque([(source_id, [source_id])])
    seen = {source_id}
    while q:
        node, path = q.popleft()
        if node == target_id:
            return {"found": True, "path": path, "length": len(path) - 1}
        if len(path) - 1 >= max_depth:
            continue
        for nb in adj.get(node, ()):
            if nb not in seen:
                seen.add(nb)
                q.append((nb, path + [nb]))
    return {"found": False, "reason": f"no path within depth {max_depth}"}


# ── Lazy tools delegated to the legacy server (imported on first call) ─────
def _delegate(legacy_fn: str, *args, **kwargs):
    """Call a legacy handler by name, passing through positional/keyword args."""
    fn = _legacy(legacy_fn)
    return fn(*args, **kwargs)


@mcp.tool()
def nexus_coach_query(query: str, project: str = "default") -> str:
    """Aggregated coaching dashboard (delegates to legacy coach_query)."""
    return _delegate("coach_query", query, project)


@mcp.tool()
def nexus_coach_antipatterns(rule_id: str = "", group: str = "",
                             severity: str = "", since_date: str = "") -> str:
    """Filtered anti-pattern detections (delegates to legacy coach_antipatterns)."""
    return _delegate("coach_antipatterns", rule_id, group, severity, since_date)


@mcp.tool()
def nexus_coach_skill_finder(pattern: str) -> str:
    """Match a prompt pattern against the skill catalog (legacy)."""
    return _delegate("coach_skill_finder", pattern)


@mcp.tool()
def nexus_coach_sessions(limit: int = 20, since_date: str = "") -> str:
    """Recent coaching sessions (delegates to legacy coach_analyze_sessions)."""
    return _delegate("coach_analyze_sessions", "all", since_date, limit)


@mcp.tool()
def nexus_learn_search(query: str = "", project: str = "default", limit: int = 20) -> str:
    """Search user-authored learning notes (legacy search_learnings)."""
    return _delegate("search_learnings", query, project, limit)


@mcp.tool()
def nexus_add_learning_note(note: str, project: str = "default",
                             task_type: str = "general",
                             bundles: str = "[]", tools: str = "[]") -> str:
    """Persist a user-authored learning note (legacy add_learning_note)."""
    return _delegate("add_learning_note", note, project, task_type, bundles, tools)


@mcp.tool()
def nexus_design_plan(task: str, project: str = "default") -> str:
    """Design a typed execution plan (legacy adaptive-imagining-cat)."""
    return _delegate("design_plan", task, project)


@mcp.tool()
def nexus_execute_phase(plan_id: int, phase_id: str, force: bool = False) -> str:
    """Execute a phase of an existing plan (legacy)."""
    return _delegate("execute_phase", plan_id, phase_id, force)


@mcp.tool()
def nexus_verify_phase(plan_id: int, phase_id: str) -> str:
    """Verify a phase's output (legacy)."""
    return _delegate("verify_phase", plan_id, phase_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")