"""V2 backend: NEXUS resource DB and learning notes."""
import json
from unittest.mock import patch
from server.context_harness.nexus_bridge import search_resources, search_learnings, store_learning
from server.context_harness.models import HarnessResult
from conftest import mock_nexus_conn


class TestNexusBackendSearchResources:
    """5+ test cases for nexus_bridge.search_resources."""

    def test_search_by_keyword(self, db):
        """Search 'tensorflow' returns LLM Agent resource."""
        with mock_nexus_conn(db):
            results = search_resources("tensorflow", max_results=5)
            names = [r["name"] for r in results]
            assert "LLM Agent" in names

    def test_search_no_match_returns_empty(self, db):
        """Search for gibberish returns empty list."""
        with mock_nexus_conn(db):
            results = search_resources("xyznonexistentkeyword12345", max_results=5)
            assert results == []

    def test_max_results_respected(self, db):
        """max_results=2 returns at most 2 results."""
        with mock_nexus_conn(db):
            results = search_resources("a", max_results=2)
            assert len(results) <= 2

    def test_results_ordered_by_times_correct(self, db):
        """Results are sorted by times_correct DESC."""
        with mock_nexus_conn(db):
            results = search_resources("", max_results=10)
            for i in range(len(results) - 1):
                assert results[i]["times_correct"] >= results[i + 1]["times_correct"]

    def test_only_active_resources_returned(self, db):
        """Resources with status != 'active' should be excluded."""
        db.execute("UPDATE resources SET status='archived' WHERE id=7")
        db.commit()
        with mock_nexus_conn(db):
            results = search_resources("legacy", max_results=5)
            ids = [r["id"] for r in results]
            assert 7 not in ids

    def test_multi_word_query(self, db):
        """Multi-word query uses AND across all words."""
        with mock_nexus_conn(db):
            results = search_resources("tensorflow inference", max_results=5)
            assert len(results) >= 1

    def test_result_dict_has_all_expected_keys(self, db):
        """Each result has: id, name, resource_type, description, cluster, token_count, times_triggered, times_correct, times_wrong, status."""
        with mock_nexus_conn(db):
            results = search_resources("tensorflow", max_results=1)
            if results:
                keys = set(results[0].keys())
                expected = {"id", "name", "resource_type", "description", "cluster",
                            "token_count", "times_triggered", "times_correct", "times_wrong", "status"}
                assert keys.issuperset(expected)

    def test_search_with_stopwords_filtered(self, db):
        """Words <= 2 chars are filtered out from query ('a', 'an', 'is', 'to')."""
        with mock_nexus_conn(db):
            results = search_resources("a an react", max_results=5)
            names = [r["name"] for r in results]
            assert "React Dashboard" in names


class TestNexusBackendSearchLearnings:
    """5+ test cases for nexus_bridge.search_learnings."""

    def test_search_learning_by_content(self, db):
        """Search for stored learning note content."""
        db.execute("INSERT INTO user_learnings (note, scope, project) VALUES ('ml agent test learning note', 'test', 'nexus')")
        db.commit()
        with mock_nexus_conn(db):
            results = search_learnings("ml agent", limit=5)
            assert len(results) >= 1

    def test_search_learning_no_match(self, db):
        """No matching learning returns empty."""
        with mock_nexus_conn(db):
            results = search_learnings("xyznonexistent", limit=5)
            assert results == []

    def test_limit_respected(self, db):
        """limit=2 returns at most 2 results."""
        for i in range(5):
            db.execute("INSERT INTO user_learnings (note, project) VALUES (?, 'nexus')", (f"test note {i}",))
        db.commit()
        with mock_nexus_conn(db):
            results = search_learnings("test", limit=2)
            assert len(results) <= 2

    def test_learning_result_structure(self, db):
        """Each learning result has: id, note, scope, project, task_type, created_at."""
        db.execute("INSERT INTO user_learnings (note, scope, project, task_type) VALUES ('test', 'scope1', 'proj1', 'type1')")
        db.commit()
        with mock_nexus_conn(db):
            results = search_learnings("test", limit=1)
            if results:
                keys = set(results[0].keys())
                expected = {"id", "note", "scope", "project", "task_type", "created_at"}
                assert keys.issuperset(expected)

    def test_learning_ordered_by_id_desc(self, db):
        """Results ordered by id DESC (newest first)."""
        for i in range(3):
            db.execute("INSERT INTO user_learnings (note, project) VALUES (?, 'nexus')", (f"ordered note {i}",))
        db.commit()
        with mock_nexus_conn(db):
            results = search_learnings("ordered", limit=3)
            assert len(results) >= 2
            for i in range(len(results) - 1):
                assert results[i]["id"] >= results[i + 1]["id"]


class TestNexusBackendStoreLearning:
    """5+ test cases for nexus_bridge.store_learning."""

    def test_store_learning_returns_int_id(self, db):
        """store_learning returns the inserted row ID."""
        with mock_nexus_conn(db):
            rid = store_learning("test-key", "test value content")
            assert isinstance(rid, int)
            assert rid > 0

    def test_store_learning_persists_value(self, db):
        """Stored value can be retrieved."""
        with mock_nexus_conn(db):
            rid = store_learning("persist-key", "persist value data")
            row = db.execute("SELECT note FROM user_learnings WHERE id=?", (rid,)).fetchone()
            assert row is not None
            assert "persist value data" in row["note"]

    def test_store_learning_with_tags(self, db):
        """Tags are stored as comma-separated string."""
        with mock_nexus_conn(db):
            store_learning("tagged-key", "tagged content", tags=["ml", "test"])
            row = db.execute("SELECT note FROM user_learnings WHERE note LIKE '%tagged content%'").fetchone()
            assert row is not None
            assert "ml" in row["note"]
            assert "test" in row["note"]

    def test_store_learning_without_tags(self, db):
        """Tags=None should not fail."""
        with mock_nexus_conn(db):
            rid = store_learning("no-tags-key", "no tags content")
            assert rid > 0

    def test_store_learning_sets_default_fields(self, db):
        """Created_by defaults to 'engine', scope defaults to 'context-harness'."""
        with mock_nexus_conn(db):
            rid = store_learning("defaults-key", "defaults check")
            row = db.execute("SELECT created_by, scope FROM user_learnings WHERE id=?", (rid,)).fetchone()
            assert row["created_by"] == "engine"
            assert row["scope"] == "context-harness"
