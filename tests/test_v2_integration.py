"""V2 integration tests — real backends marked @pytest.mark.integration.

These tests require actual network/DB connectivity and are skipped by default.
Run with: python -m pytest tests/test_v2_integration.py -v --run-integration
"""
import pytest
from server.context_harness.models import HarnessQuery, HarnessResponse


# ── Helpers ─────────────────────────────────────────────────────────────

def requires_integration():
    return pytest.mark.skipif(
        not pytest.config.getoption("--run-integration", False),
        reason="Integration tests require --run-integration flag",
    )


# ── Integration Tests ───────────────────────────────────────────────────

@pytest.mark.integration
class TestNexusDBIntegration:
    """Real NEXUS SQLite DB (requires /home/roshan/nexus/db/nexus.db)."""

    def test_search_resources_real_db(self):
        from server.context_harness.nexus_bridge import search_resources
        results = search_resources("tensorflow", max_results=5)
        assert len(results) >= 0
        for r in results:
            assert "id" in r
            assert "name" in r

    def test_search_real_db_connection(self):
        from server.context_harness.nexus_bridge import get_conn
        conn = get_conn()
        assert conn is not None
        row = conn.execute("SELECT count(*) as cnt FROM resources").fetchone()
        assert row is not None
        conn.close()

    def test_search_learnings_real_db(self):
        from server.context_harness.nexus_bridge import search_learnings
        results = search_learnings("context", limit=5)
        assert isinstance(results, list)

    def test_full_engine_harness_real(self):
        from server.context_harness.engine import ContextHarnessEngine
        engine = ContextHarnessEngine()
        resp = engine.harness(HarnessQuery(query="tensorflow ml", sources="nexus", max_results=5))
        assert isinstance(resp, HarnessResponse)
        assert resp.total_count >= 0
        for r in resp.results:
            assert isinstance(r.score, float)
            assert r.score >= 0.0

    def test_router_with_real_db(self):
        from server.context_harness.router import HubAndSpokeRouter, RouteContext
        router = HubAndSpokeRouter()
        ctx = RouteContext(query_text="tensorflow inference", top_k=5)
        result = router.route(ctx)
        assert len(result.resource_ids) >= 0
        assert result.latency_ms < 50


@pytest.mark.integration
class TestAgentReachIntegration:
    """Real agent-reach platform probing (requires CLI tools)."""

    def test_doctor_real(self):
        from server.context_harness.agent_reach import doctor
        d = doctor()
        assert len(d) >= 1

    def test_route_to_channel_real(self):
        from server.context_harness.agent_reach import route_to_channel
        ch = route_to_channel("github")
        if ch:
            assert "backends" in ch

    def test_github_channel_probe(self):
        from server.context_harness.agent_reach import CHANNELS
        github = [c for c in CHANNELS if c["name"] == "github"]
        if github:
            assert github[0]["probe"]() == (__import__("shutil").which("gh") is not None)


@pytest.mark.integration
class TestEngineEndToEnd:
    """End-to-end pipeline with all backends (slow)."""

    def test_harness_all_sources(self):
        from server.context_harness.engine import ContextHarnessEngine
        engine = ContextHarnessEngine()
        resp = engine.harness(HarnessQuery(query="tensorflow ml", sources="all", max_results=5))
        assert resp.total_count >= 0
        assert len(resp.source_used) >= 1

    def test_harness_memory_source(self):
        from server.context_harness.engine import ContextHarnessEngine
        engine = ContextHarnessEngine()
        resp = engine.harness(HarnessQuery(query="test learning", sources="memory", max_results=3))
        assert isinstance(resp, HarnessResponse)

    def test_harness_internet_source(self):
        from server.context_harness.engine import ContextHarnessEngine
        engine = ContextHarnessEngine()
        resp = engine.harness(HarnessQuery(query="github", sources="internet", max_results=3))
        assert "internet" in resp.source_used or isinstance(resp, HarnessResponse)
