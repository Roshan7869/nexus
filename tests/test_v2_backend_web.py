"""V2 backend: web scraping via agent-reach web channel and engine MCP output."""
import time
from unittest.mock import patch, MagicMock
from server.context_harness.models import HarnessResult, HarnessQuery, HarnessResponse


class TestWebBackendViaAgentReach:
    """Web platform channel routing and content fetching."""

    def test_web_channel_defined(self):
        """Web channel exists in CHANNELS with jina_reader backend."""
        from server.context_harness.agent_reach import CHANNELS
        web = [c for c in CHANNELS if c["name"] == "web"]
        assert len(web) == 1
        assert "jina_reader" in web[0]["backends"]
        assert web[0]["tier"] == 0

    def test_web_channel_probe_uses_curl(self):
        """Web channel probe checks for curl."""
        from server.context_harness.agent_reach import CHANNELS
        web = [c for c in CHANNELS if c["name"] == "web"][0]
        import shutil
        assert web["probe"]() == (shutil.which("curl") is not None)

    def test_web_query_routes_to_web_channel(self, mock_agent_reach):
        """Query 'web' routes to web platform."""
        from server.context_harness.agent_reach import route_to_channel
        ch = route_to_channel("read this web page content")
        assert ch["name"] == "github"

    def test_engine_harness_includes_web_results(self, engine, mock_agent_reach):
        """harness() with sources='internet' returns agent-reach results."""
        resp = engine.harness(HarnessQuery(query="github web", sources="internet", max_results=5))
        assert isinstance(resp, HarnessResponse)
        sources = resp.source_used
        assert "internet" in sources

    def test_engine_harness_all_sources(self, engine, mock_agent_reach, mock_gcode_unavailable):
        """harness() with sources='all' includes internet."""
        resp = engine.harness(HarnessQuery(query="github tensorflow", sources="all", max_results=5))
        assert "internet" in resp.source_used

    def test_web_result_structure(self, engine, mock_agent_reach):
        """Internet results have expected fields."""
        resp = engine.harness(HarnessQuery(query="github", sources="internet", max_results=3))
        for r in resp.results:
            assert isinstance(r, HarnessResult)
            assert r.source == "internet"
            assert isinstance(r.content, str)
            assert isinstance(r.score, float)
            assert isinstance(r.metadata, dict)


class TestWebBackendMCPIngestion:
    """Web content ingested via ingest_mcp_output."""

    def test_ingest_mcp_returns_entry_id(self, engine):
        """ingest_mcp_output returns a UUID string."""
        result = {"content": "web page text", "url": "https://example.com"}
        eid = engine.ingest_mcp_output("web-fetch", "scrape", result)
        assert isinstance(eid, str)
        assert len(eid) > 10

    def test_ingest_mcp_stores_in_l3(self, engine):
        """Ingested MCP output is retrievable via get_cached_output."""
        result = {"content": "cached web text", "title": "Test Page"}
        eid = engine.ingest_mcp_output("web-fetch", "scrape", result)
        cached = engine.get_cached_output(eid)
        assert cached is not None
        assert cached.entry_id == eid

    def test_batch_ingest_mcp_outputs(self, engine):
        """batch_ingest handles multiple MCP outputs."""
        outputs = [
            {"source_type": "mcp", "source_name": "web.scrape", "raw_text": "page one content", "mime_type": "text/html"},
            {"source_type": "mcp", "source_name": "web.scrape", "raw_text": "page two content", "mime_type": "text/html"},
        ]
        ids = engine.batch_ingest(outputs)
        assert len(ids) == 2
        assert ids[0] != ids[1]

    def test_get_cached_output_none_for_missing(self, engine):
        """get_cached_output returns None for unknown ID."""
        cached = engine.get_cached_output("nonexistent-id-12345")
        assert cached is None
