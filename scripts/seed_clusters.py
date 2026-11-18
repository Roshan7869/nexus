#!/usr/bin/env python3
"""Seed `cluster_definitions` and assign resources to clusters.

`cluster_definitions` ships empty, which makes `NEXUS_CLUSTER_ROUTING` inert: v5's
cluster filter needs a pool, and its L1 short-circuit needs a pool of >= 50. This
script fills the table from the canonical `server/cluster_config.py` definitions and
classifies resources by cosine similarity to per-cluster keyword centroids, falling
back to `name_patterns` regex and finally to `general`.

    python scripts/seed_clusters.py --dry-run
    python scripts/seed_clusters.py
    python scripts/seed_clusters.py --no-assign      # definitions only

Writes: cluster_definitions (8 rows), resources.cluster + cluster_confidence,
cluster_membership, cluster_definitions.centroid_embedding / resource_count.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.cluster_config import CLUSTER_DEFINITIONS  # noqa: E402
from server.db import get_db_path  # noqa: E402

# A resource whose best centroid similarity is below this stays in `general`.
SIMILARITY_FLOOR = 0.30


def _centroid(embedder, keywords: list[str]) -> np.ndarray:
    """Mean of keyword embeddings, L2-normalised."""
    if not keywords:
        return np.zeros(384, dtype=np.float32)
    vecs = embedder.embed_texts(keywords)
    c = vecs.mean(axis=0)
    n = float(np.linalg.norm(c)) or 1.0
    return (c / n).astype(np.float32)


def _pattern_hit(name: str, description: str, patterns: list[str]) -> bool:
    hay = f"{name} {description}".lower()
    for p in patterns:
        try:
            if re.search(p, hay):
                return True
        except re.error:
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-assign", action="store_true", help="seed definitions only")
    ap.add_argument("--iterate", type=int, default=1, help="re-center pass count (0 disables)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else Path(get_db_path())
    if not db_path.exists():
        print(f"error: database not found: {db_path}", file=sys.stderr)
        return 2

    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row

    # â”€â”€ 1. definitions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    seeded = 0
    for name, spec in CLUSTER_DEFINITIONS.items():
        con.execute(
            """INSERT INTO cluster_definitions
                   (name, display_name, description, trigger_keywords, resource_types,
                    name_patterns, default_skill, default_agents,
                    auto_activate_threshold, max_resources)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                   display_name=excluded.display_name,
                   description=excluded.description,
                   trigger_keywords=excluded.trigger_keywords,
                   resource_types=excluded.resource_types,
                   name_patterns=excluded.name_patterns,
                   default_skill=excluded.default_skill,
                   default_agents=excluded.default_agents,
                   auto_activate_threshold=excluded.auto_activate_threshold,
                   updated_at=datetime('now')""",
            (name, spec["display_name"], spec["description"],
             json.dumps(spec["trigger_keywords"]), json.dumps(spec["resource_types"]),
             json.dumps(spec["name_patterns"]), spec["default_skill"],
             json.dumps(spec["default_agents"]),
             float(spec["auto_activate_threshold"]),
             int(spec.get("expected_size", 200)) * 2),
        )
        seeded += 1
    print(f"cluster_definitions: {seeded} seeded")

    if args.no_assign or args.dry_run:
        con.commit()
        rows = con.execute("SELECT name, display_name FROM cluster_definitions ORDER BY name").fetchall()
        for r in rows:
            print(f"  {r['name']:18} {r['display_name']}")
        con.close()
        return 0

    # â”€â”€ 2. centroids â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    from server.iso_embedder import IsoEmbedder

    embedder = IsoEmbedder(db_path=str(db_path))
    embedder.load_model()
    print(f"embedder: {type(embedder.model).__name__}")

    centroids: dict[str, np.ndarray] = {}
    for name, spec in CLUSTER_DEFINITIONS.items():
        if name == "general":
            continue  # fallback cluster, never a target
        centroids[name] = _centroid(embedder, list(spec["trigger_keywords"])[:40])
        con.execute("UPDATE cluster_definitions SET centroid_embedding=? WHERE name=?",
                    (centroids[name].astype("float32").tobytes(), name))

    # â”€â”€ 3. assign â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    resources = con.execute(
        "SELECT id, name, resource_type, description, body_text FROM resources").fetchall()
    con.execute("DELETE FROM cluster_membership")
    con.execute("UPDATE resources SET cluster='general', cluster_confidence=0.0")

    assigned: dict[str, int] = {}
    for r in resources:
        text = f"{r['name']}\n{r['description'] or ''}".strip()[:600]
        vec = embedder.embed_texts([text])[0]
        n = float(np.linalg.norm(vec)) or 1.0
        vec = vec / n

        best_name, best_score = "general", 0.0
        for cname, cen in centroids.items():
            score = float(vec @ cen)
            if score > best_score:
                best_name, best_score = cname, score

        # regex fallback promotes a weak cosine match only if the name clearly matches
        if best_score < SIMILARITY_FLOOR:
            for cname, spec in CLUSTER_DEFINITIONS.items():
                if cname == "general":
                    continue
                if _pattern_hit(r["name"], r["description"] or "", spec["name_patterns"]):
                    best_name, best_score = cname, max(best_score, SIMILARITY_FLOOR)
                    break

        confidence = round(best_score, 4)
        con.execute("UPDATE resources SET cluster=?, cluster_confidence=? WHERE id=?",
                    (best_name, confidence, r["id"]))
        con.execute(
            "INSERT INTO cluster_membership(cluster_name, resource_id, affinity) "
            "VALUES (?,?,?) ON CONFLICT(cluster_name, resource_id) DO UPDATE SET affinity=excluded.affinity",
            (best_name, r["id"], confidence),
        )
        assigned[best_name] = assigned.get(best_name, 0) + 1

    con.execute(
        """UPDATE cluster_definitions SET resource_count=(
               SELECT count(*) FROM resources WHERE resources.cluster = cluster_definitions.name),
               updated_at=datetime('now')"""
    )

    # â”€â”€ 4. re-center on actual members, then re-assign once â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Keyword-mean centroids sit far from real task phrasing: measured top-1 cosine
    # 0.14-0.34 against a 0.35 accept floor, so every decision was declined. Member
    # centroids are representative by construction.
    def _member_centroids() -> dict[str, np.ndarray]:
        rows = con.execute(
            "SELECT cluster, embedding FROM resources "
            "WHERE cluster IS NOT NULL AND cluster != 'general' AND embedding IS NOT NULL"
        ).fetchall()
        buckets: dict[str, list[np.ndarray]] = {}
        for r in rows:
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            if vec.size:
                buckets.setdefault(r["cluster"], []).append(vec)
        out: dict[str, np.ndarray] = {}
        for name, vecs in buckets.items():
            mean = np.vstack(vecs).mean(axis=0)
            n = float(np.linalg.norm(mean)) or 1.0
            out[name] = (mean / n).astype(np.float32)
        return out

    def _assign(centroids_in: dict[str, np.ndarray]) -> dict[str, int]:
        counts: dict[str, int] = {}
        con.execute("DELETE FROM cluster_membership")
        rows = con.execute(
            "SELECT id, name, resource_type, description, embedding FROM resources").fetchall()
        for r in rows:
            if r["embedding"] is not None:
                vec = np.frombuffer(r["embedding"], dtype=np.float32)
            else:
                text = f"{r['name']}\n{r['description'] or ''}".strip()[:600]
                vec = embedder.embed_texts([text])[0]
            n = float(np.linalg.norm(vec)) or 1.0
            vec = vec / n
            best_name, best_score = "general", 0.0
            for cname, cen in centroids_in.items():
                score = float(vec @ cen)
                if score > best_score:
                    best_name, best_score = cname, score
            if best_score < SIMILARITY_FLOOR:
                best_name, best_score = "general", best_score
            con.execute("UPDATE resources SET cluster=?, cluster_confidence=? WHERE id=?",
                        (best_name, round(best_score, 4), r["id"]))
            con.execute(
                "INSERT INTO cluster_membership(cluster_name, resource_id, affinity) "
                "VALUES (?,?,?) ON CONFLICT(cluster_name, resource_id) DO UPDATE SET affinity=excluded.affinity",
                (best_name, r["id"], best_score))
            counts[best_name] = counts.get(best_name, 0) + 1
        return counts

    for iteration in range(max(1, args.iterate)):
        member = _member_centroids()
        if not member:
            break
        centroids = member
        for name, cen in member.items():
            con.execute("UPDATE cluster_definitions SET centroid_embedding=? WHERE name=?",
                        (cen.astype("float32").tobytes(), name))
        counts = _assign(member)
        print(f"\nre-centered (pass {iteration + 1}) from {len(member)} member centroids: "
              + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    con.execute(
        """UPDATE cluster_definitions SET resource_count=(
               SELECT count(*) FROM resources WHERE resources.cluster = cluster_definitions.name),
               updated_at=datetime('now')"""
    )
    con.commit()

    print(f"\nassigned {len(resources)} resources:")
    for cname in sorted(assigned, key=lambda k: -assigned[k]):
        print(f"  {cname:18} {assigned[cname]}")
    print("\ncluster_membership rows:",
          con.execute("SELECT count(*) FROM cluster_membership").fetchone()[0])
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
