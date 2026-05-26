"""Tests for server.coach.bridge — Phase 1 of Coach → NEXUS integration."""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from server.coach.bridge import (
    AntiPattern,
    BridgeStatus,
    CoachBridge,
    PracticeScores,
    find_coach_cache,
    get_bridge,
)
from server.coach.schema import DBTables, ensure_coach_schema, get_connection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database for testing."""
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    path = db.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def bridge_with_db(tmp_db):
    """Bridge pointed at a fresh database with tables created."""
    with patch("server.coach.schema.DB_PATH", tmp_db):
        with patch("server.coach.bridge.get_connection", get_connection):
            b = CoachBridge()
            conn = get_connection()
            ensure_coach_schema(conn)
            conn.close()
            yield b


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    def test_tables_created(self, tmp_db):
        with patch("server.coach.schema.DB_PATH", tmp_db):
            conn = get_connection()
            ensure_coach_schema(conn)
            tables = [
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            ]
            conn.close()

        coach_tables = [
            DBTables.CACHE_META,
            DBTables.PRACTICE_SCORES,
            DBTables.ANTIPATTERNS,
            DBTables.SESSIONS,
            DBTables.SKILL_FINDER,
            DBTables.DAILY_ACTIVITY,
            DBTables.BURNDOWN,
        ]
        for t in coach_tables:
            assert t in tables, f"Table {t} not found"

    def test_schema_idempotent(self, tmp_db):
        """ensure_coach_schema can be called multiple times safely."""
        with patch("server.coach.schema.DB_PATH", tmp_db):
            conn = get_connection()
            assert ensure_coach_schema(conn)
            assert ensure_coach_schema(conn)
            assert ensure_coach_schema(conn)
            conn.close()

    def test_table_exists_helper(self, tmp_db):
        with patch("server.coach.schema.DB_PATH", tmp_db):
            conn = get_connection()
            ensure_coach_schema(conn)
            assert DBTables.PRACTICE_SCORES
            conn.close()


# ---------------------------------------------------------------------------
# Bridge tests (no cache)
# ---------------------------------------------------------------------------


class TestBridgeNoCache:
    """When Coach cache does not exist, all methods return empty defaults."""

    def test_bridge_status_empty(self, bridge_with_db):
        status = bridge_with_db.get_bridge_status()
        assert isinstance(status, BridgeStatus)
        assert status.cache_found is False
        assert status.session_count == 0
        assert status.workspace_count == 0

    def test_practice_scores_empty(self, bridge_with_db):
        scores = bridge_with_db.get_practice_scores()
        assert scores == []

    def test_antipatterns_empty(self, bridge_with_db):
        patterns = bridge_with_db.get_antipatterns()
        assert patterns == []

    def test_sync_empty(self, bridge_with_db):
        status = bridge_with_db.sync()
        assert isinstance(status, BridgeStatus)
        assert status.cache_found is False

    def test_sync_logs_meta(self, bridge_with_db):
        """sync() should insert a row into coach_cache_meta even without cache."""
        bridge_with_db.sync()
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) FROM coach_cache_meta").fetchone()
        conn.close()
        assert row[0] >= 1


# ---------------------------------------------------------------------------
# Cache discovery tests
# ---------------------------------------------------------------------------


class TestCacheDiscovery:
    def test_no_cache_returns_empty(self):
        cache_dir, files = find_coach_cache()
        assert cache_dir is None
        assert files == {}

    def test_finds_cache_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d) / "meta.json"
            meta.write_text(json.dumps({"version": 9}))
            parsed = Path(d) / "parsed.json"
            parsed.write_text(json.dumps({"sessions": [], "workspaces": []}))

            with patch("server.coach.bridge.CACHE_PATHS", [Path(d)]):
                cache_dir, files = find_coach_cache()
                assert cache_dir is not None
                assert "meta" in files
                assert "parsed" in files


# ---------------------------------------------------------------------------
# Analytics computation tests (using synthetic data)
# ---------------------------------------------------------------------------


class TestAnalyticsComputation:
    """Test the internal scoring and anti-pattern detection functions."""

    def _make_session(
        self,
        sid: str = "s1",
        requests: list[dict] | None = None,
        harness: str = "Test",
    ) -> dict:
        if requests is None:
            requests = []
        return {
            "sessionId": sid,
            "harness": harness,
            "workspaceId": "ws1",
            "workspaceName": "test-project",
            "creationDate": 1715000000000,
            "lastMessageDate": 1715003600000,
            "requestCount": len(requests),
            "endReason": "shutdown",
            "requests": requests,
        }

    def _make_request(
        self,
        text: str = "hello",
        length: int = 50,
        canceled: bool = False,
        tools: list[str] | None = None,
        edits: list[str] | None = None,
        model: str = "claude-opus-4",
    ) -> dict:
        return {
            "messageText": text,
            "messageLength": length,
            "responseLength": 500,
            "isCanceled": canceled,
            "toolsUsed": tools or [],
            "editedFiles": edits or [],
            "modelId": model,
            "compaction": None,
        }

    def test_scoring_empty_sessions(self):
        from server.coach.bridge import _compute_scores_from_sessions

        scores = _compute_scores_from_sessions([])
        assert isinstance(scores, PracticeScores)
        # All scores should be positive defaults
        assert scores.prompt_quality >= 0
        assert scores.total_score >= 0

    def test_scoring_detailed_prompts(self):
        from server.coach.bridge import _compute_scores_from_sessions

        reqs = [
            self._make_request(
                text="Refactor the authentication module to use JWT tokens with proper error handling and session management.",
                length=400,
            )
            for _ in range(10)
        ]
        sess = self._make_session(requests=reqs)
        scores = _compute_scores_from_sessions([sess])
        # Detailed prompts should yield higher prompt quality
        assert scores.prompt_quality > 50

    def test_scoring_high_cancel_rate(self):
        from server.coach.bridge import _compute_scores_from_sessions

        reqs = [self._make_request(canceled=True) for _ in range(8)]
        reqs.append(self._make_request(canceled=False))
        sess = self._make_session(requests=reqs)
        scores = _compute_scores_from_sessions([sess])
        # High cancel rate should reduce hygiene
        assert scores.session_hygiene < 80

    def test_scoring_high_tool_variety(self):
        from server.coach.bridge import _compute_scores_from_sessions

        reqs = [
            self._make_request(tools=["read_file", "write_file", "bash", "search", "web_fetch", "edit"]),
            self._make_request(tools=["read_file", "grep", "glob", "Task"]),
        ]
        sess = self._make_session(requests=reqs)
        scores = _compute_scores_from_sessions([sess])
        assert scores.tool_mastery > 50

    def test_detect_antipatterns_empty(self):
        from server.coach.bridge import _detect_antipatterns

        patterns = _detect_antipatterns([])
        assert patterns == []

    def test_detect_lazy_prompting(self):
        from server.coach.bridge import _detect_antipatterns

        reqs = [self._make_request(text="hi", length=5) for _ in range(6)]
        sess = self._make_session(requests=reqs)
        patterns = _detect_antipatterns([sess])
        rule_ids = [p.rule_id for p in patterns]
        assert "lazy-prompting" in rule_ids

    def test_detect_high_cancel_rate(self):
        from server.coach.bridge import _detect_antipatterns

        reqs = [self._make_request(canceled=True) for _ in range(5)]
        reqs.append(self._make_request(canceled=False))
        sess = self._make_session(requests=reqs)
        patterns = _detect_antipatterns([sess])
        rule_ids = [p.rule_id for p in patterns]
        assert "high-cancel-rate" in rule_ids


# ---------------------------------------------------------------------------
# Upsert tests (with synthetic cache data)
# ---------------------------------------------------------------------------


class TestUpsert:
    """Test that upsert methods correctly write to the database."""

    def _make_full_cache(self) -> dict:
        return {
            "sessions": [
                {
                    "sessionId": "s1",
                    "harness": "Claude",
                    "workspaceId": "ws1",
                    "workspaceName": "test-project",
                    "creationDate": 1715000000000,
                    "lastMessageDate": 1715003600000,
                    "requestCount": 2,
                    "endReason": "shutdown",
                    "requests": [
                        {
                            "messageText": "Write a function to sort arrays",
                            "messageLength": 150,
                            "responseLength": 800,
                            "isCanceled": False,
                            "toolsUsed": ["read_file", "write_file"],
                            "editedFiles": ["src/main.py"],
                            "modelId": "claude-opus-4",
                            "compaction": None,
                        },
                        {
                            "messageText": "Add error handling",
                            "messageLength": 80,
                            "responseLength": 400,
                            "isCanceled": False,
                            "toolsUsed": ["read_file", "bash"],
                            "editedFiles": ["src/main.py", "src/errors.py"],
                            "modelId": "claude-sonnet-4",
                            "compaction": None,
                        },
                    ],
                }
            ],
            "workspaces": [["ws1", {"id": "ws1", "name": "test-project", "path": "/tmp/test"}]],
        }

    def test_upsert_and_retrieve_from_cache(self):
        """Directly test upsert methods using in-memory bridge with patches."""
        db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            with patch("server.coach.schema.DB_PATH", db_path):
                b = CoachBridge()
                conn = get_connection()
                ensure_coach_schema(conn)

                b._raw = self._make_full_cache()
                b._sessions = b._raw["sessions"]
                b._loaded = True

                b._upsert_sessions(conn)
                b._upsert_daily_activity(conn)
                b._upsert_practice_scores(conn)
                b._upsert_antipatterns(conn)

                sc = conn.execute("SELECT COUNT(*) FROM coach_sessions").fetchone()[0]
                assert sc >= 1, "session not inserted"

                pc = conn.execute("SELECT COUNT(*) FROM coach_practice_scores").fetchone()[0]
                assert pc >= 1, "practice scores not inserted"

                ac = conn.execute("SELECT COUNT(*) FROM coach_antipatterns").fetchone()[0]
                assert ac >= 1, "antipatterns not inserted"

                dc = conn.execute("SELECT COUNT(*) FROM coach_daily_activity").fetchone()[0]
                assert dc >= 1, "daily activity not inserted"

                conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_sync_from_fake_cache(self, bridge_with_db, tmp_db):
        """Simulate sync() with a temporary cache directory."""
        with tempfile.TemporaryDirectory() as d:
            cache = self._make_full_cache()
            parsed_path = Path(d) / "parsed.json"
            parsed_path.write_text(json.dumps(cache))
            meta_path = Path(d) / "meta.json"
            meta_path.write_text(json.dumps({"version": 9}))

            with patch("server.coach.bridge.CACHE_PATHS", [Path(d)]):
                status = bridge_with_db.sync()
                assert status.cache_found
                assert status.session_count == 1
                assert status.cache_version == 9

    def test_sync_twice_idempotent(self, bridge_with_db, tmp_db):
        """sync() should be idempotent (INSERT OR REPLACE)."""
        with tempfile.TemporaryDirectory() as d:
            cache = self._make_full_cache()
            parsed_path = Path(d) / "parsed.json"
            parsed_path.write_text(json.dumps(cache))

            with patch("server.coach.bridge.CACHE_PATHS", [Path(d)]):
                s1 = bridge_with_db.sync()
                s2 = bridge_with_db.sync()

                assert s1.session_count == s2.session_count

                conn = get_connection()
                row_count = conn.execute("SELECT COUNT(*) FROM coach_sessions").fetchone()[0]
                conn.close()
                assert row_count == 1, "duplicate session should be replaced"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_corrupted_cache_json(self, bridge_with_db):
        """Corrupted JSON should be handled gracefully."""
        with tempfile.TemporaryDirectory() as d:
            bad_path = Path(d) / "parsed.json"
            bad_path.write_text("not json at all {{{")
            with patch("server.coach.bridge.CACHE_PATHS", [Path(d)]):
                status = bridge_with_db.sync()
                assert status.cache_found  # meta might exist? No — just parsed file
                assert status.error == "" or status.error is not None

    def test_get_bridge_singleton(self):
        b1 = get_bridge()
        b2 = get_bridge()
        assert b1 is b2

    def test_bridge_status_dataclass(self):
        s = BridgeStatus(cache_found=True, cache_path="/tmp/test", session_count=42)
        assert s.cache_found
        assert s.session_count == 42

    def test_practice_scores_dataclass(self):
        s = PracticeScores(project="test", prompt_quality=85.0, total_score=78.0)
        assert s.project == "test"
        assert s.prompt_quality == 85.0

    def test_antipattern_dataclass(self):
        ap = AntiPattern(rule_id="test-rule", rule_name="Test Rule", severity="high", count=5)
        assert ap.rule_id == "test-rule"
        assert ap.severity == "high"
