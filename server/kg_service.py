"""Phase 6 — Knowledge graph rebuild service (I-04).

Restores backed-up ``similar_to`` edges and runs ``scripts/densify_kg`` passes
to collapse zero-edge resource count below the <500 gate.
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path

import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

NEXUS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _resolve_db_path()
SCRIPTS = NEXUS_ROOT / "scripts"

# Gate targets from second-brain-execution-plan P7
ZERO_EDGE_TARGET = 500
NULL_CLUSTER_TARGET = 100


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def get_kg_status(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Read-only KG health snapshot."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        total_resources = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        total_edges = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
        zero_edge = conn.execute(
            "SELECT COUNT(*) FROM resources r WHERE NOT EXISTS "
            "(SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id)"
        ).fetchone()[0]
        null_cluster = conn.execute(
            "SELECT COUNT(*) FROM resources WHERE cluster IS NULL"
        ).fetchone()[0]
        backup_edges = 0
        try:
            backup_edges = conn.execute(
                "SELECT COUNT(*) FROM resource_edges_backup"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        communities = 0
        try:
            communities = conn.execute(
                "SELECT COUNT(*) FROM kg_communities"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        embeddings = conn.execute(
            "SELECT COUNT(*) FROM resource_embeddings WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        edge_types = {
            r["edge_type"]: r["cnt"]
            for r in conn.execute(
                "SELECT edge_type, COUNT(*) AS cnt FROM resource_edges GROUP BY edge_type"
            )
        }
        zero_edge_pct = round(zero_edge / max(total_resources, 1) * 100, 1)
        return {
            "total_resources": total_resources,
            "total_edges": total_edges,
            "zero_edge_resources": zero_edge,
            "zero_edge_pct": zero_edge_pct,
            "null_cluster_resources": null_cluster,
            "backup_edges_available": backup_edges,
            "kg_communities": communities,
            "embeddings": embeddings,
            "edge_types": edge_types,
            "gates": {
                "zero_edge_target": ZERO_EDGE_TARGET,
                "zero_edge_pass": zero_edge < ZERO_EDGE_TARGET,
                "null_cluster_target": NULL_CLUSTER_TARGET,
                "null_cluster_pass": null_cluster < NULL_CLUSTER_TARGET,
            },
            "issue": "I-04" if zero_edge >= ZERO_EDGE_TARGET else None,
            "status": "collapsed" if total_edges < 100 else "healthy",
        }
    finally:
        if own:
            conn.close()


def restore_backup_edges(
    conn: sqlite3.Connection | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restore ``similar_to`` edges from ``resource_edges_backup``."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        before = get_kg_status(conn)
        # The backup table is transient (created ad hoc during a past repair;
        # no migration ever adds it). Guard like get_kg_status does so a
        # missing table means "nothing to restore", not a crash.
        pending = []
        try:
            pending = conn.execute(
                "SELECT b.source_id, b.target_id, b.edge_type, b.weight, "
                "b.co_trigger_count, b.created_at, b.updated_at "
                "FROM resource_edges_backup b "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM resource_edges e "
                "  WHERE (e.source_id=b.source_id AND e.target_id=b.target_id) "
                "     OR (e.source_id=b.target_id AND e.target_id=b.source_id)"
                ")"
            ).fetchall()
        except sqlite3.OperationalError:
            pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        inserted = 0
        if not dry_run and pending:
            conn.executemany(
                "INSERT INTO resource_edges "
                "(source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, COALESCE(?, ?), COALESCE(?, ?))",
                [
                    (
                        r["source_id"],
                        r["target_id"],
                        r["edge_type"],
                        r["weight"],
                        r["co_trigger_count"] or 0,
                        r["created_at"],
                        ts,
                        r["updated_at"],
                        ts,
                    )
                    for r in pending
                ],
            )
            conn.commit()
            inserted = len(pending)
        after = get_kg_status(conn) if not dry_run else before
        return {
            "dry_run": dry_run,
            "restored": inserted if not dry_run else len(pending),
            "before_edges": before["total_edges"],
            "after_edges": after["total_edges"],
            "before_zero_edge": before["zero_edge_resources"],
            "after_zero_edge": after["zero_edge_resources"],
        }
    finally:
        if own:
            conn.close()


def _import_densify():
    sys.path.insert(0, str(SCRIPTS))
    import densify_kg as mod  # noqa: E402

    return mod


def run_densify(
    conn: sqlite3.Connection | None = None,
    *,
    dry_run: bool = False,
    threshold: float = 0.65,
    write_communities: bool = True,
    prune_cotrigger: bool = False,
) -> dict[str, Any]:
    """Run densify_kg passes 7b–7g in-process."""
    own = conn is None
    if own:
        conn = _connect()
    mod = _import_densify()
    before = get_kg_status(conn)
    ids, mat = mod.load_embeddings(conn)
    n7b = mod.pass_7b_semantic_edges(conn, ids, mat, threshold, dry_run)
    mod.pass_7c_assign_orphans(conn, ids, mat, dry_run)
    n7d = mod.pass_7d_member_edges(conn, dry_run)
    if not dry_run:
        mod.pass_7c_louvain_report(conn)
    mod.pass_7e_promote_co_triggered(conn, dry_run, prune=prune_cotrigger)
    mod.pass_7f_prune_similar_to(conn, ids, mat, dry_run, threshold=0.78)
    mod.pass_7g_communities(conn, dry_run, write=write_communities)
    after = get_kg_status(conn) if not dry_run else before
    return {
        "dry_run": dry_run,
        "similar_to_added": n7b,
        "member_of_added": n7d,
        "embeddings_used": len(ids),
        "threshold": threshold,
        "write_communities": write_communities,
        "before": before,
        "after": after,
    }


def rebuild_kg(
    *,
    dry_run: bool = False,
    restore_backup: bool = True,
    write_communities: bool = True,
    threshold: float = 0.65,
) -> dict[str, Any]:
    """Full Phase 6 rebuild: optional backup restore + densify passes."""
    conn = _connect()
    try:
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        status_before = get_kg_status(conn)
        restore_result = None
        if restore_backup:
            restore_result = restore_backup_edges(conn, dry_run=dry_run)
        densify_result = run_densify(
            conn,
            dry_run=dry_run,
            threshold=threshold,
            write_communities=write_communities,
        )
        status_after = get_kg_status(conn)
        gates = status_after["gates"]
        return {
            "phase": 6,
            "issue": "I-04",
            "started_at": started,
            "dry_run": dry_run,
            "restore": restore_result,
            "densify": densify_result,
            "before": status_before,
            "after": status_after,
            "gates_pass": gates["zero_edge_pass"] and gates["null_cluster_pass"],
        }
    finally:
        conn.close()