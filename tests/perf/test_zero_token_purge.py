"""Tests for PERF-2 zero-token / empty-body resource purge."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from server.maintenance.resource_validator import (
    ResourceQualityContract,
    flag_anomalies,
    purge_zero_token_and_empty,
    validate_resources,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT,
    body_text TEXT,
    resource_type TEXT,
    cluster TEXT,
    status TEXT DEFAULT 'active',
    tier INTEGER DEFAULT 2,
    token_count INTEGER DEFAULT 0,
    times_correct INTEGER DEFAULT 0,
    times_wrong INTEGER DEFAULT 0,
    times_triggered INTEGER DEFAULT 0,
    last_used_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """In-memory NEXUS database with the resources table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    yield conn
    conn.close()


@pytest.fixture
def noop_embedder() -> Iterator[Callable[[str], Any]]:
    """Embedder that always succeeds."""
    def _embed(text: str) -> list[float]:
        return [0.0] * 384
    yield _embed


@pytest.fixture
def failing_embedder() -> Iterator[Callable[[str], Any]]:
    """Embedder that always fails."""
    def _embed(text: str) -> list[float]:
        raise RuntimeError("model unavailable")
    yield _embed


class TestResourceQualityContract:
    def test_default_rules(self) -> None:
        contract = ResourceQualityContract()
        assert contract.min_tokens == 1
        assert contract.require_non_empty_body is True
        assert contract.z_score_threshold == -2.0
        assert contract.token_count_is_valid(1) is True
        assert contract.token_count_is_valid(0) is False
        assert contract.body_is_non_empty("hello") is True
        assert contract.body_is_non_empty("") is False
        assert contract.body_is_non_empty("   ") is False

    def test_custom_rules(self) -> None:
        contract = ResourceQualityContract(min_tokens=5, z_score_threshold=-1.5)
        assert contract.token_count_is_valid(4) is False
        assert contract.token_count_is_valid(5) is True


class TestValidateResources:
    def test_valid_resource_passes(self, noop_embedder: Callable[[str], Any]) -> None:
        resources = [
            {"id": 1, "name": "good", "body_text": "useful body", "token_count": 10},
        ]
        valid, invalid = validate_resources(resources, embedder=noop_embedder)
        assert len(valid) == 1
        assert len(invalid) == 0
        assert valid[0]["id"] == 1

    def test_zero_token_flagged(self, noop_embedder: Callable[[str], Any]) -> None:
        resources = [
            {"id": 1, "name": "zero-tokens", "body_text": "some body", "token_count": 0},
        ]
        valid, invalid = validate_resources(resources, embedder=noop_embedder)
        assert len(valid) == 0
        assert len(invalid) == 1
        assert any("token_count" in v for v in invalid[0]["__violations"])

    def test_empty_body_flagged(self, noop_embedder: Callable[[str], Any]) -> None:
        resources = [
            {"id": 1, "name": "empty-body", "body_text": "", "token_count": 10},
        ]
        valid, invalid = validate_resources(resources, embedder=noop_embedder)
        assert len(valid) == 0
        assert len(invalid) == 1
        assert any("body_text" in v for v in invalid[0]["__violations"])

    def test_embed_failure_flagged(self, failing_embedder: Callable[[str], Any]) -> None:
        resources = [
            {"id": 1, "name": "bad-embed", "body_text": "body", "token_count": 10},
        ]
        valid, invalid = validate_resources(resources, embedder=failing_embedder)
        assert len(valid) == 0
        assert len(invalid) == 1
        assert any("embed failed" in v for v in invalid[0]["__violations"])

    def test_multiple_violations(self, failing_embedder: Callable[[str], Any]) -> None:
        resources = [
            {"id": 1, "name": "bad", "body_text": "", "token_count": 0},
        ]
        valid, invalid = validate_resources(resources, embedder=failing_embedder)
        assert len(invalid) == 1
        assert len(invalid[0]["__violations"]) == 2

    def test_no_embedder_skips_embed_gate(self) -> None:
        resources = [
            {"id": 1, "name": "ok", "body_text": "body", "token_count": 10},
        ]
        valid, invalid = validate_resources(resources)
        assert len(valid) == 1
        assert len(invalid) == 0


class TestFlagAnomalies:
    def test_low_token_anomaly(self) -> None:
        resources = [
            {"id": 1, "name": "tiny", "token_count": 1},
            *[
                {"id": i, "name": f"huge{i}", "token_count": 1000}
                for i in range(2, 12)
            ],
        ]
        flagged = flag_anomalies(resources)
        assert len(flagged) == 1
        assert flagged[0]["id"] == 1
        assert flagged[0]["__anomaly"] is True
        assert "__z_score" in flagged[0]

    def test_no_anomaly_when_uniform(self) -> None:
        resources = [
            {"id": 1, "name": "a", "token_count": 100},
            {"id": 2, "name": "b", "token_count": 100},
        ]
        flagged = flag_anomalies(resources)
        assert len(flagged) == 0

    def test_insufficient_data(self) -> None:
        assert flag_anomalies([]) == []
        assert flag_anomalies([{"id": 1, "token_count": 10}]) == []


class TestPurgeZeroTokenAndEmpty:
    def seed(self, conn: sqlite3.Connection) -> None:
        conn.executemany(
            """
            INSERT INTO resources
                (id, name, body_text, token_count, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "good", "useful body", 10, "active"),
                (2, "zero-tokens", "some body", 0, "active"),
                (3, "empty-body", "", 5, "active"),
                (4, "both-bad", "", 0, "active"),
            ],
        )
        conn.commit()

    def test_marks_purged_by_default(
        self, db: sqlite3.Connection, noop_embedder: Callable[[str], Any]
    ) -> None:
        self.seed(db)
        result = purge_zero_token_and_empty(db, embedder=noop_embedder)
        assert result["purged"] == 3
        assert sorted(result["invalid_ids"]) == [2, 3, 4]
        assert result["valid_count"] == 1

        statuses = dict(
            db.execute("SELECT id, status FROM resources ORDER BY id").fetchall()
        )
        assert statuses[1] == "active"
        assert statuses[2] == "purged"
        assert statuses[3] == "purged"
        assert statuses[4] == "purged"

    def test_delete_mode(
        self, db: sqlite3.Connection, noop_embedder: Callable[[str], Any]
    ) -> None:
        self.seed(db)
        result = purge_zero_token_and_empty(db, embedder=noop_embedder, delete=True)
        assert result["deleted"] == 3
        ids = [r[0] for r in db.execute("SELECT id FROM resources").fetchall()]
        assert ids == [1]

    def test_no_invalid_resources(
        self, db: sqlite3.Connection, noop_embedder: Callable[[str], Any]
    ) -> None:
        db.execute(
            "INSERT INTO resources (id, name, body_text, token_count, status) VALUES (?, ?, ?, ?, ?)",
            (1, "good", "body", 10, "active"),
        )
        db.commit()
        result = purge_zero_token_and_empty(db, embedder=noop_embedder)
        assert result["purged"] == 0
        assert result["valid_count"] == 1

    def test_embed_failure_causes_purge(
        self, db: sqlite3.Connection, failing_embedder: Callable[[str], Any]
    ) -> None:
        db.execute(
            "INSERT INTO resources (id, name, body_text, token_count, status) VALUES (?, ?, ?, ?, ?)",
            (1, "good", "body", 10, "active"),
        )
        db.commit()
        result = purge_zero_token_and_empty(db, embedder=failing_embedder)
        assert result["purged"] == 1
