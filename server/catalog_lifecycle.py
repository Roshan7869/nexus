"""P2 Catalog lifecycle — promote rules, archive hygiene, cluster SoT, FAISS.

First principles (NEXUS_SYSTEM_DESIGN_PLAN_v1 P2):
  - cluster_membership is source of truth; resources.cluster is denormalized cache
  - only active (and degraded) resources are retrievable / in FAISS
  - archive stubs and long-cold never-triggered items so search space stays clean
  - never invent learning labels (orthogonal to learning.py)

Usage:
  from server.catalog_lifecycle import CatalogLifecycle
  CatalogLifecycle().run_all(dry_run=False)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NEXUS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.path.expandvars(os.environ.get("NEXUS_DB_PATH", str(NEXUS_ROOT / "db" / "nexus.db"))))

# Promote thresholds (active catalog eligibility)
MIN_TOKEN_COUNT = int(os.environ.get("NEXUS_PROMOTE_MIN_TOKENS", "20"))
MIN_BODY_LEN = int(os.environ.get("NEXUS_PROMOTE_MIN_BODY", "40"))
COLD_DAYS = int(os.environ.get("NEXUS_COLD_ARCHIVE_DAYS", "90"))
# Only archive cold never-triggered if also low signal (avoid nuking new good skills)
COLD_REQUIRE_LOW_TOKENS = os.environ.get("NEXUS_COLD_REQUIRE_LOW_TOKENS", "true").lower() in (
    "1", "true", "yes",
)
COLD_MAX_TOKENS = int(os.environ.get("NEXUS_COLD_MAX_TOKENS", "50"))

# Gap 1: types that are invokable in IDE sessions vs reference-only inventory
INVOKE_TYPES = (
    "skill", "command", "agent", "cli_tool", "mcp", "hook",
)
REFERENCE_TYPES = (
    "design_system", "doc", "spec", "craft",
)
# Gap 1 campaign defaults (first-principles: demand-shaped, not raw age-only)
CAMPAIGN_DAYS = int(os.environ.get("NEXUS_COLD_CAMPAIGN_DAYS", "60"))
CAMPAIGN_MAX_TOKENS = int(os.environ.get("NEXUS_COLD_CAMPAIGN_MAX_TOKENS", "120"))
COLD_KPI_TARGET_PCT = float(os.environ.get("NEXUS_TARGET_COLD_PCT", "35"))


@dataclass
class LifecycleReport:
    started_at: str = ""
    finished_at: str = ""
    dry_run: bool = True
    schema_ok: bool = False
    promote_checked: int = 0
    promote_eligible: int = 0
    promote_blocked: int = 0
    archived_zero_token: int = 0
    archived_cold: int = 0
    archived_reference_cold: int = 0
    archived_campaign: int = 0
    multi_membership_pruned: int = 0
    cluster_denorm_fixed: int = 0
    cluster_counts_synced: int = 0
    faiss: dict = field(default_factory=dict)
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    """Apply additive lifecycle columns if missing. Returns actions taken."""
    actions: list[str] = []
    cols = {r[1] for r in conn.execute("PRAGMA table_info(resources)")}
    for col, decl in (
        ("archive_reason", "TEXT"),
        ("archived_at", "TEXT"),
        ("lifecycle_state", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE resources ADD COLUMN {col} {decl}")
            actions.append(f"added resources.{col}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resources_status_tokens "
        "ON resources(status, token_count)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_resources_status_triggered "
        "ON resources(status, times_triggered)"
    )
    conn.commit()
    return actions


def catalog_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Catalog health + Gap-1 split cold metrics.

    KPI for cold inventory uses **invoke types only** (skill/command/agent/…),
    not design_system/doc bulk reference inventory (RC-C5).
    """
    active = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='active'"
    ).fetchone()[0]
    archived = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='archived'"
    ).fetchone()[0]
    never = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='active' "
        "AND COALESCE(times_triggered,0)=0"
    ).fetchone()[0]
    zero_tok = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='active' "
        "AND COALESCE(token_count,0)=0"
    ).fetchone()[0]
    div = conn.execute(
        """
        SELECT COUNT(*) FROM resources r
        JOIN cluster_membership cm ON cm.resource_id = r.id
        WHERE r.cluster IS NOT NULL AND r.cluster != cm.cluster_name
        """
    ).fetchone()[0]
    multi = conn.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT resource_id FROM cluster_membership
          GROUP BY resource_id HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    emb = conn.execute("SELECT COUNT(*) FROM resource_embeddings").fetchone()[0]

    invoke_ph = ",".join("?" for _ in INVOKE_TYPES)
    ref_ph = ",".join("?" for _ in REFERENCE_TYPES)

    invoke_active = conn.execute(
        f"SELECT COUNT(*) FROM resources WHERE status='active' "
        f"AND resource_type IN ({invoke_ph})",
        INVOKE_TYPES,
    ).fetchone()[0]
    invoke_never = conn.execute(
        f"SELECT COUNT(*) FROM resources WHERE status='active' "
        f"AND COALESCE(times_triggered,0)=0 AND resource_type IN ({invoke_ph})",
        INVOKE_TYPES,
    ).fetchone()[0]
    ref_active = conn.execute(
        f"SELECT COUNT(*) FROM resources WHERE status='active' "
        f"AND resource_type IN ({ref_ph})",
        REFERENCE_TYPES,
    ).fetchone()[0]
    ref_never = conn.execute(
        f"SELECT COUNT(*) FROM resources WHERE status='active' "
        f"AND COALESCE(times_triggered,0)=0 AND resource_type IN ({ref_ph})",
        REFERENCE_TYPES,
    ).fetchone()[0]
    skill_active = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='active' AND resource_type='skill'"
    ).fetchone()[0]
    skill_never = conn.execute(
        "SELECT COUNT(*) FROM resources WHERE status='active' "
        "AND resource_type='skill' AND COALESCE(times_triggered,0)=0"
    ).fetchone()[0]

    try:
        cold_no_sections = conn.execute(
            """
            SELECT COUNT(*) FROM resources r
            WHERE r.status='active' AND COALESCE(r.times_triggered,0)=0
              AND NOT EXISTS (
                SELECT 1 FROM resource_sections rs WHERE rs.resource_id=r.id
              )
            """
        ).fetchone()[0]
    except Exception:
        cold_no_sections = 0

    invoke_never_pct = round(100.0 * invoke_never / max(invoke_active, 1), 2)
    return {
        "active": active,
        "archived": archived,
        "never_triggered_active": never,
        "never_triggered_pct": round(100.0 * never / max(active, 1), 2),
        # Gap 1 primary KPI (reference types excluded from denominator)
        "invoke_active": invoke_active,
        "invoke_never_triggered": invoke_never,
        "invoke_never_triggered_pct": invoke_never_pct,
        "invoke_cold_target_pct": COLD_KPI_TARGET_PCT,
        "invoke_cold_gate_pass": invoke_never_pct <= COLD_KPI_TARGET_PCT,
        "reference_active": ref_active,
        "reference_never_triggered": ref_never,
        "skill_active": skill_active,
        "skill_never_triggered": skill_never,
        "skill_never_triggered_pct": round(
            100.0 * skill_never / max(skill_active, 1), 2
        ),
        "cold_without_sections": cold_no_sections,
        "zero_token_active": zero_tok,
        "cluster_divergence": div,
        "multi_membership": multi,
        "resource_embeddings": emb,
    }


def is_promote_eligible(row: sqlite3.Row | dict) -> tuple[bool, list[str]]:
    """Promote rules: body + tokens + cluster + (embedding or row in resource_embeddings)."""
    reasons: list[str] = []
    get = row.__getitem__ if not isinstance(row, dict) else row.get
    status = get("status") or "active"
    if status == "archived":
        return False, ["already_archived"]
    body = (get("body_text") or "").strip()
    tokens = int(get("token_count") or 0)
    cluster = get("cluster")
    if len(body) < MIN_BODY_LEN:
        reasons.append(f"body_len<{MIN_BODY_LEN}")
    if tokens < MIN_TOKEN_COUNT:
        reasons.append(f"token_count<{MIN_TOKEN_COUNT}")
    if not cluster:
        reasons.append("no_cluster")
    # embedding presence checked by caller when conn available
    return (len(reasons) == 0), reasons


class CatalogLifecycle:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def run_all(
        self,
        *,
        dry_run: bool = True,
        rebuild_faiss: bool = True,
        cold_days: int | None = None,
        cold_campaign: bool = False,
        campaign_days: int | None = None,
        aggressive: bool = False,
    ) -> LifecycleReport:
        report = LifecycleReport(
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            dry_run=dry_run,
        )
        cold_days = COLD_DAYS if cold_days is None else cold_days
        campaign_days = CAMPAIGN_DAYS if campaign_days is None else campaign_days
        conn = _connect(self.db_path)
        try:
            actions = ensure_schema(conn)
            report.schema_ok = True
            if actions:
                logger.info("schema: %s", actions)

            report.metrics_before = catalog_metrics(conn)

            self._scan_promote(conn, report)
            report.archived_zero_token = self.archive_zero_token(conn, dry_run=dry_run)
            report.archived_cold = self.archive_cold(
                conn, days=cold_days, dry_run=dry_run
            )
            if cold_campaign:
                report.archived_reference_cold = self.archive_reference_cold(
                    conn, dry_run=dry_run
                )
                report.archived_campaign = self.archive_cold_campaign(
                    conn,
                    days=campaign_days,
                    dry_run=dry_run,
                    aggressive=aggressive,
                )
            report.multi_membership_pruned = self.prune_multi_membership(
                conn, dry_run=dry_run
            )
            report.cluster_denorm_fixed = self.sync_cluster_denorm(
                conn, dry_run=dry_run
            )
            report.cluster_counts_synced = self.sync_cluster_counts(
                conn, dry_run=dry_run
            )

            if not dry_run:
                conn.commit()

            report.metrics_after = catalog_metrics(conn)
            # For dry-run campaign, project post-KPI after simulated archives
            if dry_run and cold_campaign and report.metrics_before:
                b = report.metrics_before
                cut = (
                    report.archived_reference_cold
                    + report.archived_campaign
                    + report.archived_zero_token
                    + report.archived_cold
                )
                # all candidates are never_triggered active
                proj_active = max(0, int(b.get("active", 0)) - cut)
                proj_never = max(0, int(b.get("never_triggered_active", 0)) - cut)
                proj_invoke_a = max(
                    0,
                    int(b.get("invoke_active", 0))
                    - report.archived_campaign
                    - report.archived_zero_token,
                )
                # reference archives reduce invoke denominator? no — only ref types
                # zero_token may include mcp invoke
                proj_invoke_n = max(
                    0,
                    int(b.get("invoke_never_triggered", 0))
                    - report.archived_campaign
                    - report.archived_zero_token,
                )
                report.metrics_after = {
                    **b,
                    "active": proj_active,
                    "never_triggered_active": proj_never,
                    "never_triggered_pct": round(
                        100.0 * proj_never / max(proj_active, 1), 2
                    ),
                    "invoke_active": proj_invoke_a,
                    "invoke_never_triggered": proj_invoke_n,
                    "invoke_never_triggered_pct": round(
                        100.0 * proj_invoke_n / max(proj_invoke_a, 1), 2
                    ),
                    "invoke_cold_gate_pass": (
                        100.0 * proj_invoke_n / max(proj_invoke_a, 1)
                    )
                    <= float(b.get("invoke_cold_target_pct", COLD_KPI_TARGET_PCT)),
                    "projected_dry_run": True,
                }
        except Exception as e:
            report.errors.append(str(e))
            logger.exception("lifecycle run failed")
            conn.rollback()
        finally:
            conn.close()

        if rebuild_faiss and not dry_run and not report.errors:
            try:
                report.faiss = self.rebuild_faiss_index(active_only=True)
            except Exception as e:
                report.errors.append(f"faiss: {e}")
                logger.exception("faiss rebuild failed")
        elif rebuild_faiss and dry_run:
            report.faiss = {"skipped": "dry_run"}

        report.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return report

    # ── promote scan (report only; does not auto-activate archived) ──

    def _scan_promote(self, conn: sqlite3.Connection, report: LifecycleReport) -> None:
        rows = conn.execute(
            """
            SELECT r.id, r.name, r.status, r.token_count, r.body_text, r.cluster,
                   (SELECT 1 FROM resource_embeddings re WHERE re.resource_id=r.id LIMIT 1)
                     AS has_emb
            FROM resources r
            WHERE r.status IN ('active', 'degraded')
            """
        ).fetchall()
        report.promote_checked = len(rows)
        for row in rows:
            ok, reasons = is_promote_eligible(row)
            if not row["has_emb"] and not (row["token_count"] or 0):
                # missing emb only blocks if already token-poor
                pass
            if ok and row["has_emb"]:
                report.promote_eligible += 1
            else:
                report.promote_blocked += 1

    # ── archive jobs ─────────────────────────────────────────────────

    def archive_zero_token(
        self, conn: sqlite3.Connection, *, dry_run: bool = True
    ) -> int:
        """Archive active resources with token_count=0 (empty MCP stubs)."""
        rows = conn.execute(
            """
            SELECT id, name, resource_type FROM resources
            WHERE status = 'active' AND COALESCE(token_count, 0) = 0
            """
        ).fetchall()
        if dry_run:
            return len(rows)
        for r in rows:
            conn.execute(
                """
                UPDATE resources
                SET status = 'archived',
                    archive_reason = 'zero_token_stub',
                    archived_at = datetime('now'),
                    lifecycle_state = 'archived',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (r["id"],),
            )
        return len(rows)

    def archive_cold(
        self,
        conn: sqlite3.Connection,
        *,
        days: int = 90,
        dry_run: bool = True,
    ) -> int:
        """Archive never-triggered active resources older than ``days``.

        ISO created_at only (unix-epoch legacy rows skipped). Optional low-token
        gate so high-quality never-triggered skills are not wiped.
        """
        token_clause = ""
        params: list[Any] = [f"-{int(days)} days"]
        if COLD_REQUIRE_LOW_TOKENS:
            token_clause = "AND COALESCE(token_count, 0) <= ?"
            params.append(COLD_MAX_TOKENS)

        sql = f"""
            SELECT id, name FROM resources
            WHERE status = 'active'
              AND COALESCE(times_triggered, 0) = 0
              AND created_at LIKE '____-__-__%'
              AND created_at < datetime('now', ?)
              {token_clause}
        """
        rows = conn.execute(sql, params).fetchall()
        if dry_run:
            return len(rows)
        reason = f"cold_{days}d"
        for r in rows:
            conn.execute(
                """
                UPDATE resources
                SET status = 'archived',
                    archive_reason = ?,
                    archived_at = datetime('now'),
                    lifecycle_state = 'archived',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (reason, r["id"]),
            )
        return len(rows)

    def archive_reference_cold(
        self, conn: sqlite3.Connection, *, dry_run: bool = True
    ) -> int:
        """Gap 1: archive never-triggered reference inventory (doc/design/spec/craft).

        These inflate the all-active cold % without representing IDE-invokable tools.
        """
        ph = ",".join("?" for _ in REFERENCE_TYPES)
        rows = conn.execute(
            f"""
            SELECT id, name, resource_type FROM resources
            WHERE status = 'active'
              AND COALESCE(times_triggered, 0) = 0
              AND resource_type IN ({ph})
            """,
            REFERENCE_TYPES,
        ).fetchall()
        if dry_run:
            return len(rows)
        for r in rows:
            conn.execute(
                """
                UPDATE resources
                SET status = 'archived',
                    archive_reason = 'cold_reference_unused',
                    archived_at = datetime('now'),
                    lifecycle_state = 'archived',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (r["id"],),
            )
        return len(rows)

    def archive_cold_campaign(
        self,
        conn: sqlite3.Connection,
        *,
        days: int = 60,
        dry_run: bool = True,
        max_tokens: int | None = None,
        aggressive: bool = False,
    ) -> int:
        """Gap 1 demand-shaped campaign for invoke types.

        Archive never-triggered invoke resources that:
          - are older than ``days`` (ISO created_at)
          - never appear as skill_name in routing_log (no demand signal)
          - AND (token_count <= max_tokens OR bulk-import name pattern)
            — unless ``aggressive=True`` (any token size, demand filter only)
        """
        max_tokens = CAMPAIGN_MAX_TOKENS if max_tokens is None else max_tokens
        invoke_ph = ",".join("?" for _ in INVOKE_TYPES)
        has_routing_log = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='routing_log'"
        ).fetchone()
        demand_clause = ""
        if has_routing_log:
            demand_clause = (
                "AND NOT EXISTS ("
                "SELECT 1 FROM routing_log rl WHERE rl.skill_name = resources.name"
                ")"
            )
        if aggressive:
            quality_clause = "1=1"
            params: list[Any] = [*INVOKE_TYPES, f"-{int(days)} days"]
        else:
            quality_clause = """(
                COALESCE(token_count, 0) <= ?
                OR name LIKE 'ruflo-%%'
                OR name LIKE 'webclaw-%%'
                OR name LIKE '%%-ADR-%%'
                OR name LIKE 'claude-cmd-%%'
                OR name LIKE 'claude-skill-%%'
                OR name LIKE 'hermes-skill-%%'
              )"""
            params = [*INVOKE_TYPES, f"-{int(days)} days", max_tokens]
        rows = conn.execute(
            f"""
            SELECT id, name, resource_type, token_count FROM resources
            WHERE status = 'active'
              AND COALESCE(times_triggered, 0) = 0
              AND resource_type IN ({invoke_ph})
              AND created_at LIKE '____-__-__%'
              AND created_at < datetime('now', ?)
              {demand_clause}
              AND {quality_clause}
            """,
            params,
        ).fetchall()
        if dry_run:
            return len(rows)
        reason = f"cold_campaign_{days}d" + ("_agg" if aggressive else "")
        for r in rows:
            conn.execute(
                """
                UPDATE resources
                SET status = 'archived',
                    archive_reason = ?,
                    archived_at = datetime('now'),
                    lifecycle_state = 'archived',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (reason, r["id"]),
            )
        return len(rows)

    def run_gap1_campaign(
        self,
        *,
        dry_run: bool = True,
        campaign_days: int | None = None,
        rebuild_faiss: bool = True,
        aggressive: bool = False,
    ) -> LifecycleReport:
        """Gap 1 one-shot: reference cold + demand-shaped invoke campaign + FAISS."""
        return self.run_all(
            dry_run=dry_run,
            rebuild_faiss=rebuild_faiss,
            cold_campaign=True,
            campaign_days=campaign_days,
            aggressive=aggressive,
        )

    # ── cluster SoT ──────────────────────────────────────────────────

    def prune_multi_membership(
        self, conn: sqlite3.Connection, *, dry_run: bool = True
    ) -> int:
        """Keep one membership per resource (highest affinity, then name)."""
        multi = conn.execute(
            """
            SELECT resource_id FROM cluster_membership
            GROUP BY resource_id HAVING COUNT(*) > 1
            """
        ).fetchall()
        removed = 0
        for (rid,) in multi:
            rows = conn.execute(
                """
                SELECT id, cluster_name, affinity FROM cluster_membership
                WHERE resource_id = ?
                ORDER BY affinity DESC, cluster_name ASC
                """,
                (rid,),
            ).fetchall()
            if len(rows) <= 1:
                continue
            keep_id = rows[0]["id"]
            drop_ids = [r["id"] for r in rows[1:]]
            removed += len(drop_ids)
            if not dry_run:
                conn.executemany(
                    "DELETE FROM cluster_membership WHERE id = ?",
                    [(i,) for i in drop_ids],
                )
                # denorm primary
                conn.execute(
                    "UPDATE resources SET cluster = ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (rows[0]["cluster_name"], rid),
                )
        return removed

    def sync_cluster_denorm(
        self, conn: sqlite3.Connection, *, dry_run: bool = True
    ) -> int:
        """Set resources.cluster from single membership (SoT)."""
        mismatched = conn.execute(
            """
            SELECT r.id, r.cluster AS old_c, cm.cluster_name AS new_c
            FROM resources r
            JOIN cluster_membership cm ON cm.resource_id = r.id
            WHERE r.cluster IS NULL OR r.cluster != cm.cluster_name
            """
        ).fetchall()
        # also resources with membership missing cluster
        if dry_run:
            return len(mismatched)
        n = 0
        for row in mismatched:
            conn.execute(
                "UPDATE resources SET cluster = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (row["new_c"], row["id"]),
            )
            n += 1
        return n

    def sync_cluster_counts(
        self, conn: sqlite3.Connection, *, dry_run: bool = True
    ) -> int:
        """Refresh cluster_definitions.resource_count from active memberships."""
        clusters = conn.execute("SELECT name FROM cluster_definitions").fetchall()
        n = 0
        for (name,) in clusters:
            count = conn.execute(
                """
                SELECT COUNT(*) FROM cluster_membership cm
                JOIN resources r ON r.id = cm.resource_id
                WHERE cm.cluster_name = ? AND r.status IN ('active', 'degraded')
                """,
                (name,),
            ).fetchone()[0]
            if dry_run:
                n += 1
                continue
            conn.execute(
                """
                UPDATE cluster_definitions
                SET resource_count = ?, updated_at = datetime('now')
                WHERE name = ?
                """,
                (count, name),
            )
            n += 1
        return n

    # ── FAISS ────────────────────────────────────────────────────────

    def rebuild_faiss_index(self, *, active_only: bool = True) -> dict:
        """Rebuild FAISS from resource_embeddings; optionally active/degraded only."""
        import numpy as np
        import faiss
        import hashlib
        import subprocess

        dim = 384
        index_dir = NEXUS_ROOT / "db" / "faiss"
        index_path = index_dir / "resources.index"
        tmp_path = index_dir / "resources.index.tmp"
        meta_path = index_path.with_suffix(".index.meta.json")

        conn = _connect(self.db_path)
        try:
            if active_only:
                rows = conn.execute(
                    """
                    SELECT re.resource_id, re.embedding, r.name, r.status
                    FROM resource_embeddings re
                    JOIN resources r ON r.id = re.resource_id
                    WHERE re.embedding IS NOT NULL
                      AND r.status IN ('active', 'degraded')
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT re.resource_id, re.embedding, r.name, r.status
                    FROM resource_embeddings re
                    JOIN resources r ON r.id = re.resource_id
                    WHERE re.embedding IS NOT NULL
                    """
                ).fetchall()
        finally:
            conn.close()

        vectors = []
        resource_ids = []
        for rid, blob, name, status in rows:
            if blob is None:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if len(vec) != dim:
                logger.warning("skip rid=%s dim=%s", rid, len(vec))
                continue
            vectors.append(vec)
            resource_ids.append(int(rid))

        if not vectors:
            raise RuntimeError("no embeddings to index")

        emb = np.array(vectors, dtype=np.float32)
        faiss.normalize_L2(emb)
        index = faiss.IndexFlatIP(dim)
        index.add(emb)

        index_dir.mkdir(parents=True, exist_ok=True)
        try:
            git_hash = (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(NEXUS_ROOT),
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                .decode()
                .strip()
            )
        except Exception:
            git_hash = "unknown"

        faiss.write_index(index, str(tmp_path))
        h = hashlib.sha256()
        with open(tmp_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        checksum = h.hexdigest()
        os.replace(str(tmp_path), str(index_path))

        meta = {
            "resource_ids": resource_ids,
            "dim": dim,
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "n_total": len(resource_ids),
            "active_only": active_only,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit": git_hash,
            "checksum_sha256": checksum,
            "pipeline": "catalog_lifecycle.P2",
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        return {
            "n_total": len(resource_ids),
            "path": str(index_path),
            "checksum_sha256": checksum,
            "active_only": active_only,
        }


def run_cli(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="NEXUS catalog lifecycle (P2 + Gap1 campaign)")
    p.add_argument("--apply", action="store_true", help="Write changes (default dry-run)")
    p.add_argument("--no-faiss", action="store_true", help="Skip FAISS rebuild")
    p.add_argument("--cold-days", type=int, default=None, help="Legacy low-token cold age")
    p.add_argument(
        "--campaign",
        action="store_true",
        help="Gap 1: archive unused reference + demand-shaped invoke cold",
    )
    p.add_argument(
        "--campaign-days",
        type=int,
        default=None,
        help=f"Gap 1 campaign age threshold (default {CAMPAIGN_DAYS})",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    dry = not args.apply
    lc = CatalogLifecycle()
    report = lc.run_all(
        dry_run=dry,
        rebuild_faiss=not args.no_faiss,
        cold_days=args.cold_days,
        cold_campaign=args.campaign,
        campaign_days=args.campaign_days,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print("=== NEXUS Catalog Lifecycle ===")
        print(f"dry_run={report.dry_run} campaign={args.campaign}")
        print("before:", report.metrics_before)
        print(
            f"archive zero_token={report.archived_zero_token} "
            f"cold={report.archived_cold} "
            f"ref_cold={report.archived_reference_cold} "
            f"campaign={report.archived_campaign}"
        )
        print(
            f"cluster multi_pruned={report.multi_membership_pruned} "
            f"denorm_fixed={report.cluster_denorm_fixed} "
            f"counts={report.cluster_counts_synced}"
        )
        print("after:", report.metrics_after)
        if report.metrics_after:
            m = report.metrics_after
            print(
                f"KPI invoke_cold={m.get('invoke_never_triggered_pct')}% "
                f"(target ≤{m.get('invoke_cold_target_pct')}%) "
                f"gate={m.get('invoke_cold_gate_pass')}"
            )
        print("faiss:", report.faiss)
        if report.errors:
            print("ERRORS:", report.errors)
            return 1
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
