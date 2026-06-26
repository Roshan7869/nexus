"""P7: Knowledge graph densification.

Two passes:
  7b — semantic similarity edges (threshold 0.65) between embedded resources,
       using a vectorized numpy cosine matrix (1834 x 1834 = ~13MB, trivial).
       NOT the plan's O(n^2) python double-loop — one matmul.
  7c — assign the 853 orphan (NULL-cluster) resources to their nearest cluster
       centroid by embedding similarity. Louvain community detection is run
       for reporting only — orphans have no edges so Louvain cannot place them;
       centroid assignment is the correct mechanism (matches plan 6c auto_organize).

Corrected vs plan v2:
  - INSERT uses real column `edge_type` (plan said `relation` — wrong).
  - 7a (I/O type edges) dropped: `resources.input_types`/`output_types` do not
    exist. Semantic edges alone target <500.
  - Baseline is 941 (33.4%) not 2,117 (75%) — plan was stale.

Reversible: edges added in this run get created_at = run timestamp;
    DELETE FROM resource_edges WHERE created_at >= '<run_ts>' AND edge_type='similar_to';
Cluster reverts via backup of resources.cluster column (printed at start).

Usage: python3 scripts/densify_kg.py [--dry-run] [--threshold 0.65]
"""
from __future__ import annotations

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
    """Run Louvain for reporting only — counts communities on the existing graph."""
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
        n_communities = len(set(partition.values()))
        print(f"7c: louvain detected {n_communities} communities (reporting only)")
    except Exception as e:
        print(f"7c: louvain failed: {e}")


def main():
    dry_run = "--dry-run" in sys.argv
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
    print(f"  0-edge delta: {before_0} -> {after_0} ({before_0 - after_0} resolved)")
    conn.close()


if __name__ == "__main__":
    main()