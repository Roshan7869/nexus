"""P0-S5: Resource Seeder — seeds bundle_membership from classifications.

Loads active resources, matches against bundle definitions, writes assignments.
"""

import sqlite3
from typing import NamedTuple, TypedDict

from server.context_harness.classifier import Classification
from server.context_harness.bundles.definitions import (
    BundleDefinition,
    BundleRegistry,
    get_bundles,
    get_canonical_bundle,
    match_criteria,
)


class SeedConfig(TypedDict):
    batch_size: int
    dry_run: bool
    replace_existing: bool
    max_bundle_size: int | None


_DEFAULT_SEED_CONFIG: SeedConfig = {
    "batch_size": 500,
    "dry_run": False,
    "replace_existing": False,
    "max_bundle_size": None,
}


class BundleSeedResult(NamedTuple):
    bundle_assigned: int
    canonical_assigned: int
    bundle_counts: dict[str, int]
    skipped: list[int]
    errors: list[str]


class BundleSeeder:
    def __init__(
        self,
        db_path: str,
        bundle_registry: BundleRegistry | None = None,
        config: SeedConfig | None = None,
    ):
        self._db_path = db_path
        self._bundle_registry = bundle_registry or get_bundles()
        self._config = {**_DEFAULT_SEED_CONFIG, **(config or {})}

    def seed(self, classifications: dict[int, dict]) -> BundleSeedResult:
        self._current_classifications = classifications
        self._bundle_counts: dict[str, int] = {}
        bundle_assigned = 0
        canonical_assigned = 0
        skipped: list[int] = []
        errors: list[str] = []

        conn = sqlite3.connect(self._db_path)
        try:
            offset = 0
            while True:
                resources = self._load_active_resources(
                    conn, offset=offset, limit=self._config["batch_size"]
                )
                if not resources:
                    break

                for resource in resources:
                    rid = resource["id"]
                    classification = classifications.get(rid)

                    try:
                        assignments = self._assign_to_bundles(resource, classification)
                    except Exception as exc:
                        errors.append(f"Resource {rid}: {exc}")
                        continue

                    if not assignments:
                        skipped.append(rid)
                        continue

                    for bundle_name, bundle_kind, priority, is_canonical in assignments:
                        if not self._config["dry_run"]:
                            self._write_assignment(
                                conn, rid, bundle_name, bundle_kind, priority, is_canonical,
                            )
                        bundle_assigned += 1
                        if is_canonical:
                            canonical_assigned += 1

                offset += len(resources)

            if not self._config["dry_run"]:
                conn.commit()
        finally:
            conn.close()

        return BundleSeedResult(
            bundle_assigned=bundle_assigned,
            canonical_assigned=canonical_assigned,
            bundle_counts=dict(self._bundle_counts),
            skipped=skipped,
            errors=errors,
        )

    def seed_all(self, classifications: dict[int, dict]) -> BundleSeedResult:
        if self._config["replace_existing"]:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("DELETE FROM bundle_membership")
                conn.commit()
            finally:
                conn.close()

        return self.seed(classifications)

    def _assign_to_bundles(
        self,
        resource: dict,
        classification: dict | None,
    ) -> list[tuple[str, str, int, bool]]:
        canonical_name = get_canonical_bundle(
            resource, self._current_classifications, self._bundle_registry,
        )

        sorted_defs = sorted(
            self._bundle_registry.items(),
            key=lambda item: (
                0 if item[0] == "core_infra" else 1,
                item[1].default_priority,
            ),
        )

        assignments: list[tuple[str, str, int, bool]] = []
        for name, defn in sorted_defs:
            if not match_criteria(resource, classification, defn.criteria):
                continue

            is_canonical = (name == canonical_name)
            priority = defn.criteria["priority_base"] + (0 if is_canonical else 5)

            max_size = self._config.get("max_bundle_size") or defn.criteria.get("max_resources")
            if max_size is not None and self._bundle_counts.get(name, 0) >= max_size:
                continue

            assignments.append((name, defn.kind, priority, is_canonical))
            self._bundle_counts[name] = self._bundle_counts.get(name, 0) + 1

        return assignments

    @staticmethod
    def _load_active_resources(
        conn: sqlite3.Connection,
        offset: int = 0,
        limit: int = 500,
    ) -> list[dict]:
        cursor = conn.execute(
            """
            SELECT r.id, r.name, r.description, r.body_text,
                   r.resource_type, r.cluster, r.tier
            FROM resources r
            WHERE r.status = 'active'
            ORDER BY r.id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    @staticmethod
    def _write_assignment(
        conn: sqlite3.Connection,
        resource_id: int,
        bundle_name: str,
        bundle_kind: str,
        priority: int,
        is_canonical: bool,
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO bundle_membership
            (resource_id, bundle_name, bundle_kind, priority, is_canonical)
            VALUES (?, ?, ?, ?, ?)
            """,
            (resource_id, bundle_name, bundle_kind, priority, int(is_canonical)),
        )


def bulk_seed_from_classifier(
    db_path: str,
    classifier,
) -> BundleSeedResult:
    classifications = classifier.classify_all()

    cleaned: dict[int, dict] = {}
    for rid, cls in classifications.items():
        if isinstance(cls, Classification):
            cleaned[rid] = {
                "project_type": cls.project_type,
                "confidence": cls.confidence,
                "scores": cls.scores,
                "dominant_keyword": cls.dominant_keyword,
                "match_count": cls.match_count,
                "model_version": cls.model_version,
            }
        else:
            cleaned[rid] = cls

    seeder = BundleSeeder(db_path=db_path)
    return seeder.seed_all(cleaned)


def load_bundle_membership(
    db_path: str,
    bundle_names: list[str] | None = None,
) -> dict[str, list[int]]:
    conn = sqlite3.connect(db_path)
    try:
        if bundle_names:
            placeholders = ",".join("?" for _ in bundle_names)
            cursor = conn.execute(
                f"""
                SELECT bm.bundle_name, bm.resource_id
                FROM bundle_membership bm
                WHERE bm.bundle_name IN ({placeholders})
                ORDER BY bm.bundle_name, bm.resource_id
                """,
                bundle_names,
            )
        else:
            cursor = conn.execute(
                """
                SELECT bm.bundle_name, bm.resource_id
                FROM bundle_membership bm
                ORDER BY bm.bundle_name, bm.resource_id
                """,
            )

        result: dict[str, list[int]] = {}
        for bundle_name, resource_id in cursor.fetchall():
            result.setdefault(bundle_name, []).append(resource_id)
        return result
    finally:
        conn.close()
