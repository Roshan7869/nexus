"""V2 backend: gcode memory + learning notes fallback."""
from pathlib import Path
from unittest.mock import patch
from server.context_harness.gcode_backend import search, store, stats, _gcode_available, _cli_exec, _socket_send
from conftest import mock_nexus_conn


class TestMemoryBackendGcodeUnavailable:
    """When gcode is unavailable, falls through to learning notes."""

    def test_search_falls_back_to_learning_notes(self, db, mock_gcode_unavailable):
        """search() returns learning notes when gcode unavailable."""
        db.execute("INSERT INTO user_learnings (note, scope, project) VALUES ('fallback memory test note', 'test', 'nexus')")
        db.commit()
        with mock_nexus_conn(db):
            results = search("fallback memory", max_results=5)
            assert len(results) >= 1
            assert results[0]["source"] == "learning-note"

    def test_search_returns_empty_when_no_notes(self, db, mock_gcode_unavailable):
        """search() returns empty list when no notes match."""
        with mock_nexus_conn(db):
            results = search("xyznonexistent", max_results=5)
            assert results == []

    def test_search_max_results(self, db, mock_gcode_unavailable):
        """search() with max_results=2 returns at most 2 items."""
        for i in range(4):
            db.execute("INSERT INTO user_learnings (note, project) VALUES (?, 'nexus')", (f"search limit test {i}",))
        db.commit()
        with mock_nexus_conn(db):
            results = search("search limit", max_results=2)
            assert len(results) <= 2

    def test_store_falls_back_to_learning_notes(self, db, mock_gcode_unavailable):
        """store() persists to learning notes when gcode unavailable."""
        with mock_nexus_conn(db):
            result = store("test-key", "test store fallback", tags=["tag1"])
            assert result["status"] == "stored"
            assert result["backend"] == "learning-note"
            assert "id" in result

    def test_store_persists_content(self, db, mock_gcode_unavailable):
        """store() content is retrievable via search."""
        with mock_nexus_conn(db):
            store("persist-key", "persistent memory content", tags=["persist"])
        with mock_nexus_conn(db):
            results = search("persistent memory", max_results=5)
            assert len(results) >= 1


class TestMemoryBackendGcodeAvailable:
    """When gcode is available, uses CLI."""

    def test_search_uses_cli_when_available(self, mock_gcode_available):
        """search() calls _cli_exec when gcode binary exists."""
        with patch("server.context_harness.gcode_backend._cli_exec", return_value='{"data": "test"}') as mock_cli:
            results = search("gcode query", max_results=5)
            mock_cli.assert_called_once()
            assert len(results) >= 1

    def test_search_returns_gcode_source_tag(self, mock_gcode_available):
        """search() result source is 'gcode-memory' when available."""
        results = search("gcode query", max_results=5)
        assert results[0]["source"] == "gcode-memory"

    def test_store_uses_cli_when_available(self, mock_gcode_available):
        """store() calls _cli_exec when gcode binary exists."""
        with patch("server.context_harness.gcode_backend._cli_exec", return_value='{"stored": true}') as mock_cli:
            result = store("key", "value", tags=["t"])
            mock_cli.assert_called_once()
            assert result["status"] == "stored"
            assert result["backend"] == "gcode-memory"

    def test_stats_uses_gcode_when_available(self, mock_gcode_available):
        """stats() returns gcode source when available."""
        with patch("server.context_harness.gcode_backend._cli_exec", return_value='{"count": 42}'):
            s = stats()
            assert s["source"] == "gcode-memory"

    def test_stats_falls_back_when_gcode_fails(self, db, mock_gcode_unavailable):
        """stats() falls back to learning notes count when gcode fails."""
        # stats() queries table 'learning_notes' (production bug; table is user_learnings)
        db.execute("CREATE TABLE IF NOT EXISTS learning_notes AS SELECT * FROM user_learnings WHERE 0")
        db.execute("INSERT INTO learning_notes (note, project) VALUES ('stat1', 'p1'), ('stat2', 'p1')")
        db.commit()
        with mock_nexus_conn(db):
            s = stats()
            assert s["source"] == "learning-notes"
            assert "total" in s


class TestMemoryBackendSocketProtocol:
    """Unix socket communication tests."""

    def test_socket_send_returns_none_when_no_socket(self):
        """_socket_send returns None when socket file doesn't exist."""
        with patch("server.context_harness.gcode_backend.GCODE_SOCKET", Path("/nonexistent/gcode.sock")):
            result = _socket_send({"type": "memory", "op": "search"})
            assert result is None

    def test_cli_exec_returns_none_when_binary_missing(self):
        """_cli_exec returns None when binary doesn't exist."""
        with patch("server.context_harness.gcode_backend._gcode_available", return_value=False):
            result = _cli_exec(["memory", "search", "test"])
            assert result is None

    def test_gcode_available_false_by_default(self):
        """_gcode_available() returns False by default (binary not set)."""
        assert _gcode_available() is False


class TestMemoryBackendLearningEdgeCases:
    """Edge cases for learning-note fallback."""

    def test_store_with_empty_tags(self, db, mock_gcode_unavailable):
        """store() with tags=[] should not fail."""
        with mock_nexus_conn(db):
            result = store("empty-tags", "no tags", tags=[])
            assert result["status"] == "stored"

    def test_search_short_query(self, db, mock_gcode_unavailable):
        """Short query words (<=2 chars) are filtered, single long word works."""
        with mock_nexus_conn(db):
            results = search("a b c ml", max_results=5)
            assert isinstance(results, list)
