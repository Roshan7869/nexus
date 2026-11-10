"""P7: Knowledge graph densification.

Passes:
  7b — semantic similarity edges (default threshold 0.65) via vectorized numpy
       cosine matrix (one matmul, not the plan's O(n^2) python double-loop).
  7c — assign NULL-cluster orphans to nearest cluster centroid by embedding
       similarity (Louvain is report-only; orphans have no edges).
  7d — member_of edges from 0-edge resources to a cluster anchor.
  7e — promote co_triggered count>=5 to weighted edges; optionally prune
       count==2 noise (gated by --prune-cotrigger).
  7f — prune similar_to below 0.78 and pairs already co-triggered (gated by
       NEXUS_PRUNE_SIMILAR env; backs up to resource_edges_backup).
  7g — networkx Louvain community detection with resolution sweep (gated
       write via --write-communities). Leiden-CPM is the out-of-session upgrade.
  7h — typed edges (second-brain-kg L1-a): alternative_to (same-cluster
       cos>=0.82, never co-triggered) + documented_by (doc wikilinks /
       name correspondence). Runs with the default pass sequence.

Reversible: edges added in this run get created_at = run timestamp;
    DELETE FROM resource_edges WHERE created_at >= '<run_ts>' AND edge_type='similar_to';
    7h: ... AND edge_type IN ('alternative_to','documented_by').

Usage: python3 scripts/densify_kg.py [--dry-run] [--threshold 0.65]
       [--prune-cotrigger] [--write-communities]
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "db" / "nexus.db"
THRESHOLD = 0.65
SIMILAR_CAP_PER_NODE = 8  # max similar_to edges per resource — cap density


def load_embeddings(conn) -> tuple[list[int], np.ndarray]:
    rows = conn.execute(
        "SELECT resource_id, embedding FROM resource_embeddings "
        "WHERE embedding IS NOT NULL ORDER BY resource_id"
    ).fetchall()
    ids = [r[0] for r in rows]
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
    mat = mat.reshape(len(rows), 384)
    # L2 normalize
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    return ids, mat


def existing_edge_keys(conn) -> set[tuple[int, int]]:
    keys = set()
    for r in conn.execute(
        "SELECT source_id, target_id FROM resource_edges"
    ).fetchall():
        a, b = r[0], r[1]
        keys.add((a, b))
        keys.add((b, a))
    return keys


def pass_7b_semantic_edges(conn, ids, mat, threshold, dry_run):
    """Vectorized cosine similarity matrix -> insert pairs >= threshold."""
    # sim[i,j] = dot(i,j) since rows are normalized
    sim = mat @ mat.T  # (N, N)
    n = len(ids)
    existing = existing_edge_keys(conn)
    np.fill_diagonal(sim, -1.0)  # exclude self

    # For each node, take top candidates above threshold, capped
    new_edges: list[tuple[int, int, float]] = []
    for i in range(n):
        row = sim[i]
        # indices above threshold, sorted desc
        above = np.where(row >= threshold)[0]
        if above.size == 0:
            continue
        order = above[np.argsort(-row[above])]
        added = 0
        for j in order:
            if added >= SIMILAR_CAP_PER_NODE:
                break
            a, b = ids[i], ids[j]
            if (a, b) in existing:
                continue
            new_edges.append((a, b, float(row[j])))
            existing.add((a, b))
            existing.add((b, a))
            added += 1

    print(f"7b: new similar_to edges {'(dry-run)' if dry_run else 'to insert'}: {len(new_edges)}")
    if not dry_run and new_edges:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        conn.executemany(
            "INSERT INTO resource_edges "
            "(source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at) "
            "VALUES (?, ?, 'similar_to', ?, 0, ?, ?)",
            [(a, b, round(w, 4), ts, ts) for a, b, w in new_edges],
        )
        conn.commit()
    return len(new_edges)


def pass_7c_assign_orphans(conn, ids, mat, dry_run):
    """Assign NULL-cluster resources to nearest cluster centroid by embedding."""
    # Build cluster centroids from embedded resources that have a cluster
    id_to_idx = {rid: i for i, rid in enumerate(ids)}
    cluster_centroids: dict[str, np.ndarray] = {}
    cluster_counts: dict[str, int] = {}
    rows = conn.execute(
        "SELECT id, cluster FROM resources WHERE cluster IS NOT NULL"
    ).fetchall()
    clustered_vectors: dict[str, list[int]] = {}
    for r in rows:
        rid, cl = r[0], r[1]
        idx = id_to_idx.get(rid)
        if idx is None:
            continue
        clustered_vectors.setdefault(cl, []).append(idx)
    for cl, idxs in clustered_vectors.items():
        cluster_centroids[cl] = mat[idxs].mean(axis=0)
        cluster_counts[cl] = len(idxs)

    centroid_names = list(cluster_centroids.keys())
    centroid_mat = np.array([cluster_centroids[c] for c in centroid_names])
    # normalize centroids
    cn = np.linalg.norm(centroid_mat, axis=1, keepdims=True)
    cn[cn == 0] = 1.0
    centroid_mat = centroid_mat / cn

    # Orphans with embeddings
    orphans = conn.execute(
        "SELECT id, cluster FROM resources WHERE cluster IS NULL ORDER BY id"
    ).fetchall()
    print(f"7c: orphans (NULL cluster): {len(orphans)}")

    # Keyword fallback for orphans without embeddings
    try:
        import sys as _sys
        _sys.path.insert(0, str(DB.parent.parent / "server"))
        from cluster_config import CLUSTER_DEFINITIONS  # noqa: E402
        kw_clusters = {n: [k.lower() for k in CLUSTER_DEFINITIONS[n].get("trigger_keywords", [])]
                       for n in CLUSTER_DEFINITIONS if n != "general"}
    except Exception as e:
        print(f"7c: cluster_config unavailable ({e}); keyword fallback disabled")
        kw_clusters = {}

    assigned = 0
    assigned_kw = 0
    no_embedding = 0
    for r in orphans:
        rid = r[0]
        idx = id_to_idx.get(rid)
        best_cl = None
        conf = 0.0
        if idx is not None:
            vec = mat[idx].reshape(1, -1)
            sims = (vec @ centroid_mat.T).ravel()
            best = int(np.argmax(sims))
            best_cl = centroid_names[best]
            conf = float(sims[best])
            assigned += 1
        elif kw_clusters:
            # keyword fallback on name + description
            meta = conn.execute(
                "SELECT name, description FROM resources WHERE id = ?", (rid,)
            ).fetchone()
            text = ((meta["name"] or "") + " " + (meta["description"] or "")).lower() if meta else ""
            best_hits = 0
            best_cl_kw = "general"
            for cl, kws in kw_clusters.items():
                hits = sum(1 for k in kws if k in text)
                if hits > best_hits:
                    best_hits = hits
                    best_cl_kw = cl
            best_cl = best_cl_kw
            conf = min(0.3 + best_hits * 0.1, 0.85) if best_hits else 0.2
            assigned_kw += 1
        else:
            no_embedding += 1
            continue
        if not dry_run and best_cl:
            conn.execute(
                "UPDATE resources SET cluster = ?, cluster_confidence = ? WHERE id = ?",
                (best_cl, conf, rid),
            )
    if not dry_run:
        conn.commit()
    print(f"7c: assigned by centroid: {assigned}, by keyword: {assigned_kw}, "
          f"unassignable: {no_embedding}")
    return assigned + assigned_kw, no_embedding


def pass_7d_member_edges(conn, dry_run):
    """Add a 'member_of' edge from each 0-edge resource to a cluster anchor.

    Anchor = highest-times_triggered resource in the same cluster. This gives
    every orphan at least one edge, dropping the 0-edge count hard.
    """
    # cache anchors per cluster
    anchors: dict[str, int] = {}
    for r in conn.execute(
        "SELECT cluster, id FROM resources WHERE cluster IS NOT NULL "
        "ORDER BY times_triggered DESC, id ASC"
    ).fetchall():
        if r["cluster"] not in anchors:
            anchors[r["cluster"]] = r["id"]

    existing = existing_edge_keys(conn)
    zero_edge = conn.execute(
        "SELECT id FROM resources r WHERE NOT EXISTS "
        "(SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id)"
    ).fetchall()
    print(f"7d: 0-edge resources to connect: {len(zero_edge)}")

    new_edges = 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in zero_edge:
        rid = r["id"]
        cl = conn.execute("SELECT cluster FROM resources WHERE id = ?", (rid,)).fetchone()["cluster"]
        anchor = anchors.get(cl) if cl else None
        if not anchor or anchor == rid:
            # fall back to any high-trigger resource
            anchor_row = conn.execute(
                "SELECT id FROM resources WHERE id != ? AND times_triggered > 0 "
                "ORDER BY times_triggered DESC LIMIT 1", (rid,)
            ).fetchone()
            anchor = anchor_row["id"] if anchor_row else None
        if not anchor or (rid, anchor) in existing:
            continue
        if not dry_run:
            conn.execute(
                "INSERT INTO resource_edges "
                "(source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at) "
                "VALUES (?, ?, 'member_of', 0.5, 0, ?, ?)",
                (rid, anchor, ts, ts),
            )
        existing.add((rid, anchor)); existing.add((anchor, rid))
        new_edges += 1
    if not dry_run and new_edges:
        conn.commit()
    print(f"7d: member_of edges {'(dry-run)' if dry_run else 'added'}: {new_edges}")
    return new_edges


def pass_7c_louvain_report(conn):
    """Louvain validation against the 8 manual clusters. ADDITIVE only — never
    writes clusters. Skipped if networkx/python-louvain unavailable. Reports
    n_communities, per-community plurality cluster + purity, overall purity,
    and clusters Louvain splits/merges.
    """
    try:
        import networkx as nx
        import community as community_louvain
    except Exception as e:
        print(f"7c: louvain skipped ({e})")
        return
    G = nx.Graph()
    for r in conn.execute("SELECT id FROM resources").fetchall():
        G.add_node(r[0])
    for r in conn.execute(
        "SELECT source_id, target_id, weight FROM resource_edges"
    ).fetchall():
        if r[0] != r[1]:
            G.add_edge(r[0], r[1], weight=max(float(r[2] or 1.0), 0.001))
    try:
        partition = community_louvain.best_partition(G)
    except Exception as e:
        print(f"7c: louvain failed: {e}")
        return

    # resource_id -> existing cluster (from resources table)
    rid_to_cluster = {
        r[0]: r[1] for r in conn.execute(
            "SELECT id, cluster FROM resources WHERE cluster IS NOT NULL"
        ).fetchall()
    }
    n_communities = len(set(partition.values()))
    print(f"7c: louvain detected {n_communities} communities "
          f"(vs 8 assigned clusters)")

    # contingency: community -> {cluster: count}
    contig: dict[int, dict[str, int]] = {}
    for rid, comm in partition.items():
        cluster = rid_to_cluster.get(rid)
        if cluster is None:
            continue
        contig.setdefault(comm, {})
        contig[comm][cluster] = contig[comm].get(cluster, 0) + 1

    # per-community plurality cluster + purity contribution
    total_assigned = sum(sum(c.values()) for c in contig.values())
    overall_correct = 0
    print("7c: per-community plurality (top 12 by size):")
    comm_summary = []
    for comm, counts in contig.items():
        size = sum(counts.values())
        if size == 0:
            continue
        plurality_cluster, plurality_n = max(counts.items(), key=lambda kv: kv[1])
        purity = plurality_n / size
        overall_correct += plurality_n
        comm_summary.append((size, comm, plurality_cluster, purity, len(counts)))
    comm_summary.sort(reverse=True)
    for size, comm, pc, purity, n_clusters in comm_summary[:12]:
        marker = " MERGE" if n_clusters > 2 else ""
        print(f"   comm={comm:4d} size={size:5d} plurality={pc:18s} "
              f"purity={purity:.2f} spans={n_clusters} clusters{marker}")

    if total_assigned:
        overall_purity = overall_correct / total_assigned
        print(f"7c: overall clustering purity = {overall_purity:.3f} "
              f"({overall_correct}/{total_assigned})")

    # split detection: clusters dominant in ≥2 communities
    cluster_to_comms: dict[str, list[tuple[int, int]]] = {}
    for size, comm, pc, purity, _ in comm_summary:
        cluster_to_comms.setdefault(pc, []).append((comm, size))
    splits = {c: v for c, v in cluster_to_comms.items() if len(v) > 1}
    if splits:
        print("7c: clusters Louvain SPLITS across communities:")
        for c, v in list(splits.items())[:8]:
            parts = ", ".join(f"comm={comm}({sz})" for comm, sz in v[:4])
            print(f"   {c:18s} -> {parts}")
    else:
        print("7c: no cluster splits detected (each cluster is plurality in "
              "exactly one community)")


def pass_7e_promote_co_triggered(conn, dry_run, prune=False):
    """Promote co_triggered count>=5 to weighted edges (weight =
    min(0.3+0.1*count, 0.95)); optionally prune count==2 noise.

    Pruning is gated behind --prune-cotrigger (default OFF). To avoid
    orphaning the pair / node, a count==2 edge is deleted ONLY when the same
    (source,target) pair already has another edge of a DIFFERENT edge_type.
    Idempotent: the UPDATE recomputes from co_trigger_count; deletions stick.
    """
    cnt = "SELECT COUNT(*) FROM resource_edges WHERE edge_type='co_triggered' AND co_trigger_count"
    n_signal = conn.execute(cnt + ">=5").fetchone()[0]
    n_noise = conn.execute(cnt + "=2").fetchone()[0]
    print(f"7e: co_triggered count>=5 (signal)={n_signal}, count==2 (noise)={n_noise}")
    pair_other = (" (e2.id!=e.id AND e2.edge_type!='co_triggered' AND "
                  "((e2.source_id=e.source_id AND e2.target_id=e.target_id) "
                  "OR (e2.source_id=e.target_id AND e2.target_id=e.source_id)))")
    prunable = conn.execute(
        "SELECT COUNT(*) FROM resource_edges e WHERE e.edge_type='co_triggered' "
        "AND e.co_trigger_count=2 AND EXISTS (SELECT 1 FROM resource_edges e2 WHERE"
        + pair_other + ")"
    ).fetchone()[0]
    print(f"7e: count==2 edges safe to prune (pair has other edge_type): {prunable}")
    if not dry_run:
        cur = conn.execute(
            "UPDATE resource_edges SET weight=min(0.3 + 0.1*co_trigger_count, 0.95), "
            "updated_at=datetime('now') WHERE edge_type='co_triggered' AND co_trigger_count>=5"
        )
        conn.commit()
        print(f"7e: promoted (rows updated): {cur.rowcount}")
    if prune:
        if dry_run:
            print(f"7e: (dry-run) would prune {prunable} count==2 noise edges")
        else:
            ids = [r[0] for r in conn.execute(
                "SELECT e.id FROM resource_edges e WHERE e.edge_type='co_triggered' "
                "AND e.co_trigger_count=2 AND EXISTS (SELECT 1 FROM resource_edges e2 WHERE"
                + pair_other + ")"
            ).fetchall()]
            if ids:
                conn.executemany("DELETE FROM resource_edges WHERE id=?", [(i,) for i in ids])
                conn.commit()
            print(f"7e: pruned count==2 noise edges: {len(ids)}")
    else:
        print("7e: --prune-cotrigger not set -> count==2 noise retained")
    return n_signal, n_noise, prunable


def pass_7f_prune_similar_to(conn, ids, mat, dry_run, threshold=0.78):
    """Prune similar_to: drop weight<threshold AND pairs already co-triggered
    (redundant; arXiv:2601.15298). Gated by NEXUS_PRUNE_SIMILAR env (default
    OFF) because nexus_get_neighbors / nexus_shortest_path (mcp_v2_server.py
    ~L429/L445) consume similar_to. Backs up deleted edges to
    resource_edges_backup (with backed_up_at) before deletion.
    """
    enabled = os.environ.get("NEXUS_PRUNE_SIMILAR", "").lower() in ("1", "true", "yes")
    total = conn.execute(
        "SELECT COUNT(*) FROM resource_edges WHERE edge_type='similar_to'"
    ).fetchone()[0]
    below = conn.execute(
        "SELECT COUNT(*) FROM resource_edges WHERE edge_type='similar_to' AND weight < ?",
        (threshold,)).fetchone()[0]
    cotrig = conn.execute(
        "SELECT COUNT(*) FROM resource_edges s WHERE s.edge_type='similar_to' AND EXISTS ("
        "SELECT 1 FROM resource_edges c WHERE c.edge_type='co_triggered' AND c.co_trigger_count>0 "
        "AND ((c.source_id=s.source_id AND c.target_id=s.target_id) "
        "OR (c.source_id=s.target_id AND c.target_id=s.source_id)))"
    ).fetchone()[0]
    print(f"7f: similar_to total={total}, weight<{threshold}={below}, "
          f"pairs already co-triggered={cotrig}")
    if not enabled:
        print("7f: NEXUS_PRUNE_SIMILAR not set -> skip (report-only)")
        return 0
    rows = conn.execute(
        "SELECT id, source_id, target_id, edge_type, weight, co_trigger_count, "
        "created_at, updated_at FROM resource_edges WHERE edge_type='similar_to' "
        "AND (weight < ? OR EXISTS (SELECT 1 FROM resource_edges c WHERE "
        "c.edge_type='co_triggered' AND c.co_trigger_count>0 AND ((c.source_id=resource_edges.source_id "
        "AND c.target_id=resource_edges.target_id) OR (c.source_id=resource_edges.target_id "
        "AND c.target_id=resource_edges.source_id))))", (threshold,)).fetchall()
    print(f"7f: similar_to to prune: {len(rows)}")
    if dry_run:
        return len(rows)
    conn.execute("CREATE TABLE IF NOT EXISTS resource_edges_backup AS "
                 "SELECT * FROM resource_edges WHERE 0")
    try:
        conn.execute("ALTER TABLE resource_edges_backup ADD COLUMN backed_up_at TEXT")
    except sqlite3.OperationalError:
        pass
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        "INSERT INTO resource_edges_backup (id, source_id, target_id, edge_type, "
        "weight, co_trigger_count, created_at, updated_at, backed_up_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)", [(*tuple(r), ts) for r in rows])
    conn.executemany("DELETE FROM resource_edges WHERE id=?", [(r[0],) for r in rows])
    conn.commit()
    print(f"7f: backed up + deleted {len(rows)} similar_to edges")
    return len(rows)


def pass_7g_communities(conn, dry_run, write=False):
    """Dep-free community detection: networkx Louvain with resolution sweep.

    Leiden-CPM (Traag 2019, doi:10.1038/s41598-019-41695-z) is the
    out-of-session refinement (needs leidenalg+igraph, not installed).
    nx.community.louvain_communities(G, resolution=r) exposes the resolution
    knob that addresses the Fortunato-Barthelemy PNAS 2007 resolution limit.
    Target: largest community ~=5-10% of nodes (140-280 of 2815) to avoid
    ~10-node noise communities. WRITE gated behind --write-communities.
    """
    try:
        import networkx as nx
    except Exception as e:
        print(f"7g: networkx unavailable ({e}) -> skip")
        return None
    G = nx.Graph()
    G.add_nodes_from(r[0] for r in conn.execute("SELECT id FROM resources").fetchall())
    for r in conn.execute("SELECT source_id, target_id, weight FROM resource_edges").fetchall():
        if r[0] != r[1]:
            G.add_edge(r[0], r[1], weight=max(float(r[2] or 1.0), 0.001))
    n_nodes = G.number_of_nodes()
    target_lo, target_hi = int(0.05 * n_nodes), int(0.10 * n_nodes)
    print(f"7g: graph nodes={n_nodes}, edges={G.number_of_edges()}, "
          f"target largest community {target_lo}-{target_hi}")
    sweep = []
    for res in (0.5, 1.0, 2.0, 5.0, 10.0):
        comms = nx.community.louvain_communities(G, resolution=res, seed=42)
        sizes = sorted((len(c) for c in comms), reverse=True)
        sweep.append((res, len(comms), sizes[0] if sizes else 0))
    print("7g: resolution sweep (resolution, n_communities, largest_size):")
    for res, nc, largest in sweep:
        print(f"   resolution={res:<5} n_communities={nc:<5} largest={largest}")
    in_band = [s for s in sweep if target_lo <= s[2] <= target_hi]
    if in_band:
        chosen = in_band[0]
        print(f"7g: chosen resolution={chosen[0]} (largest={chosen[2]}, in band)")
    else:
        mid = (target_lo + target_hi) / 2
        chosen = min(sweep, key=lambda s: abs(s[2] - mid))
        print(f"7g: WARNING no resolution hits band {target_lo}-{target_hi}; "
              f"closest resolution={chosen[0]} (largest={chosen[2]})")
    if dry_run or not write:
        print("7g: report-only (pass --write-communities to persist)")
        return chosen
    comms = nx.community.louvain_communities(G, resolution=chosen[0], seed=42)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM kg_community_membership")
    conn.execute("DELETE FROM kg_communities")
    for members in comms:
        conn.execute("INSERT INTO kg_communities (resolution, size, created_at) VALUES (?,?,?)",
                     (chosen[0], len(members), ts))
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO kg_community_membership (resource_id, community_id) VALUES (?,?)",
            [(rid, cid) for rid in members])
    conn.commit()
    print(f"7g: wrote {len(comms)} communities to kg_communities/kg_community_membership")
    return chosen


# ── 7h typed edges (second-brain-kg L1-a) ───────────────────────────────────
TYPED_ALT_THRESHOLD = 0.82   # stricter than similar_to's 0.65
TYPED_ALT_CAP = 4            # max alternative_to per node
TYPED_DOC_TYPES = ("doc", "spec", "topic")     # explainer types (wikilink signal)
TYPED_TOOL_TYPES = ("skill", "agent", "command", "cli_tool", "mcp", "hook")
TYPED_NAME_PREFIXES = ("skills-", "docs-", "claude-skill-")  # name/path signal
_WIKILINK_RE = None          # lazy compile (module import stays light)


def _norm_name(s: str) -> str:
    return re.sub(r"[\s_]+", "-", (s or "").strip().lower())


def _co_triggered_pairs(conn) -> set[tuple[int, int]]:
    """Unordered pairs with ANY co-trigger evidence (complements, not substitutes)."""
    pairs: set[tuple[int, int]] = set()
    for a, b in conn.execute(
        "SELECT source_id, target_id FROM resource_edges "
        "WHERE edge_type='co_triggered' AND co_trigger_count > 0"
    ).fetchall():
        pairs.add((a, b))
        pairs.add((b, a))
    return pairs


def _resolve_wikilink(target: str, name_by_norm: dict) -> int | None:
    """Resolve a [[wikilink]] target to a resource id (path/heading tolerant)."""
    t = (target or "").strip().lower()
    if not t:
        return None
    segs = re.split(r"[/#]", t)
    cands = [t]
    cands += ["/".join(segs[i:]) for i in range(1, len(segs))]
    cands.append(segs[-1])
    if len(segs) >= 2 and segs[0].rstrip("s") in {
            p.rstrip("s") for p in TYPED_DOC_TYPES + ("skill", "agent", "command", "mcp", "hook")}:
        cands.append("/".join(segs[1:]))
    for c in dict.fromkeys(cands):
        rid = name_by_norm.get(_norm_name(c))
        if rid is not None:
            return rid
    return None


def _derive_documented_by(conn, typed_existing: set[tuple[int, int]]) -> list[tuple[int, int, float]]:
    """(documented_resource, explaining_doc, weight): skill/tool → doc that
    explains it (direction per second-brain-kg change doc role statement).

    Signal 1 — obsidian [[wikilinks]] in doc-like bodies (topic/doc/spec
    resources reference what they explain). Signal 2 — name/path
    correspondence: a doc named ``skills-X``/``docs-X``/``claude-skill-X``
    explains skill ``X``.
    """
    global _WIKILINK_RE
    if _WIKILINK_RE is None:
        _WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
    name_by_norm: dict[str, int] = {}
    type_by_id: dict[int, str] = {}
    for rid, name, rtype in conn.execute(
            "SELECT id, name, resource_type FROM resources").fetchall():
        name_by_norm.setdefault(_norm_name(name), rid)
        type_by_id[rid] = rtype

    counts: dict[tuple[int, int], int] = {}
    # signal 1: wikilinks from doc-like resources
    for rid, rtype, body in conn.execute(
        "SELECT id, resource_type, body_text FROM resources "
        "WHERE body_text LIKE '%[[%'"
    ).fetchall():
        if rtype not in TYPED_DOC_TYPES or not body:
            continue
        for target in _WIKILINK_RE.findall(body):
            dst = _resolve_wikilink(target, name_by_norm)
            if dst is None or dst == rid:
                continue
            # only skill/tool -> doc counts; explainer -> explainer chains don't
            if type_by_id.get(dst) not in TYPED_TOOL_TYPES:
                continue
            key = (dst, rid)          # source=skill/tool, target=explaining doc
            if key in typed_existing:
                continue
            counts[key] = counts.get(key, 0) + 1

    # signal 2: name/path correspondence
    type_ph = ",".join("?" * len(TYPED_DOC_TYPES))
    for did, dname in conn.execute(
        f"SELECT id, name FROM resources WHERE resource_type IN ({type_ph})",
        TYPED_DOC_TYPES).fetchall():
        d = _norm_name(dname)
        for pref in TYPED_NAME_PREFIXES:
            if d.startswith(pref):
                rid = name_by_norm.get(d[len(pref):])
                if (rid is not None and rid != did
                        and type_by_id.get(rid) in TYPED_TOOL_TYPES
                        and (rid, did) not in typed_existing):
                    counts[(rid, did)] = max(counts.get((rid, did), 0), 0)
                break

    edges = []
    for (skill, doc), n in counts.items():
        # repeated wikilinks weigh more; name-only matches sit mid-scale
        w = 0.9 if n > 1 else (0.7 if n == 1 else 0.5)
        edges.append((skill, doc, w))
    return edges


def pass_7h_typed_edges(conn, ids, mat, dry_run):
    """7h: alternative_to (same non-NULL cluster, cos>=0.82, never co-triggered)
    + documented_by (doc wikilinks / name correspondence). Reversible via run_ts:
        DELETE FROM resource_edges WHERE created_at >= '<run_ts>'
          AND edge_type IN ('alternative_to','documented_by').

    DELTA vs the plan sketch: alternative_to skips only pairs that already have
    an alternative_to edge (or are co-triggered) — skipping ALL existing edges
    would starve the pass of exactly the >=0.82 pairs 7b already typed
    similar_to, i.e. the high-cos substitutes it exists to label. TYPED_ALT_CAP
    is a *cumulative* per-node incident cap (counts source+target, pre-seeded
    from existing rows) so repeated runs are idempotent, not cap-per-run.
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    cotrig = _co_triggered_pairs(conn)
    type_pairs: dict[str, set[tuple[int, int]]] = {}
    for et, a, b in conn.execute(
        "SELECT edge_type, source_id, target_id FROM resource_edges "
        "WHERE edge_type IN ('alternative_to','documented_by')").fetchall():
        s = type_pairs.setdefault(et, set())
        s.add((a, b))
        s.add((b, a))
    alt_existing = type_pairs.get("alternative_to", set())
    doc_existing = type_pairs.get("documented_by", set())

    # ── alternative_to: reuse the normalized mat (vectorized, no python double loop)
    id_to_cluster = {r[0]: r[1] for r in conn.execute(
        "SELECT id, cluster FROM resources WHERE cluster IS NOT NULL").fetchall()}
    # cumulative incident count (source OR target) so the cap holds across runs
    alt_incident: dict[int, int] = {}
    for a, b in conn.execute(
        "SELECT source_id, target_id FROM resource_edges "
        "WHERE edge_type='alternative_to'").fetchall():
        alt_incident[a] = alt_incident.get(a, 0) + 1
        alt_incident[b] = alt_incident.get(b, 0) + 1
    sim = mat @ mat.T
    np.fill_diagonal(sim, -1.0)
    alt_edges: list[tuple[int, int, float]] = []
    for i, a in enumerate(ids):
        if alt_incident.get(a, 0) >= TYPED_ALT_CAP:
            continue
        row = sim[i]
        above = np.where(row >= TYPED_ALT_THRESHOLD)[0]
        if above.size == 0:
            continue
        order = above[np.argsort(-row[above])]
        for j in order:
            if alt_incident.get(a, 0) >= TYPED_ALT_CAP:
                break
            b = ids[j]
            ca = id_to_cluster.get(a)
            if ca is None or ca != id_to_cluster.get(b):
                continue                       # both must share a real cluster
            if (a, b) in alt_existing or (b, a) in alt_existing:
                continue                       # already typed this run/earlier
            if (a, b) in cotrig:
                continue                       # co-use = complement, not substitute
            if alt_incident.get(b, 0) >= TYPED_ALT_CAP:
                continue                       # cap counts both endpoints
            alt_edges.append((a, b, float(row[j])))
            alt_existing.add((a, b))
            alt_existing.add((b, a))
            alt_incident[a] = alt_incident.get(a, 0) + 1
            alt_incident[b] = alt_incident.get(b, 0) + 1

    # ── documented_by: docs explaining skills/tools
    doc_edges = _derive_documented_by(conn, doc_existing)

    print(f"7h: alternative_to={len(alt_edges)} documented_by={len(doc_edges)} "
          f"{'(dry-run)' if dry_run else ''}")
    if not dry_run and (alt_edges or doc_edges):
        conn.executemany(
            "INSERT INTO resource_edges "
            "(source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at) "
            "VALUES (?, ?, 'alternative_to', ?, 0, ?, ?)",
            [(a, b, round(w, 4), ts, ts) for a, b, w in alt_edges])
        conn.executemany(
            "INSERT INTO resource_edges "
            "(source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at) "
            "VALUES (?, ?, 'documented_by', ?, 0, ?, ?)",
            [(a, b, round(w, 4), ts, ts) for a, b, w in doc_edges])
        conn.commit()
        print(f"7h: inserted alternative_to={len(alt_edges)} "
              f"documented_by={len(doc_edges)} (run_ts={ts})")
    return len(alt_edges), len(doc_edges)


def main():
    dry_run = "--dry-run" in sys.argv
    prune_cotrigger = "--prune-cotrigger" in sys.argv
    write_communities = "--write-communities" in sys.argv
    threshold = THRESHOLD
    if "--threshold" in sys.argv:
        threshold = float(sys.argv[sys.argv.index("--threshold") + 1])

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    before_0 = conn.execute(
        "SELECT COUNT(*) FROM resources r WHERE NOT EXISTS "
        "(SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id)"
    ).fetchone()[0]
    before_null = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE cluster IS NULL"
    ).fetchone()[0]
    print(f"BEFORE: 0-edge resources={before_0}, NULL cluster={before_null}")

    ids, mat = load_embeddings(conn)
    print(f"loaded {len(ids)} embeddings, dim={mat.shape[1]}")

    n7b = pass_7b_semantic_edges(conn, ids, mat, threshold, dry_run)
    n_assigned, n_noemb = pass_7c_assign_orphans(conn, ids, mat, dry_run)
    n7d = pass_7d_member_edges(conn, dry_run)
    if not dry_run:
        pass_7c_louvain_report(conn)
    pass_7e_promote_co_triggered(conn, dry_run, prune=prune_cotrigger)
    pass_7f_prune_similar_to(conn, ids, mat, dry_run, threshold=0.78)
    n7h_alt, n7h_doc = pass_7h_typed_edges(conn, ids, mat, dry_run)
    pass_7g_communities(conn, dry_run, write=write_communities)

    after_0 = conn.execute(
        "SELECT COUNT(*) FROM resources r WHERE NOT EXISTS "
        "(SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id)"
    ).fetchone()[0]
    after_null = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE cluster IS NULL"
    ).fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
    print()
    print(f"AFTER: 0-edge resources={after_0} (target <500, was {before_0})")
    print(f"AFTER: NULL cluster={after_null} (target <100, was {before_null})")
    print(f"AFTER: total edges={total_edges} (+{n7b} similar_to)")
    print(f"  typed edges: +{n7h_alt} alternative_to, +{n7h_doc} documented_by (7h)")
    print(f"  0-edge delta: {before_0} -> {after_0} ({before_0 - after_0} resolved)")
    conn.close()


if __name__ == "__main__":
    main()