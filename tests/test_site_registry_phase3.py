#!/usr/bin/env python3
"""Phase 3: site registry + research channel expansion."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.site_registry import (
    load_site_registry,
    match_publishers,
    get_project_urls,
    publisher_channel_spec,
    compress_harvest_results,
    list_publishers,
    list_projects,
)
from server.context_harness.channels.dispatcher import get_dispatcher
from server.research_ingest import ingest_paper, _resource_name


class TestSiteRegistry(unittest.TestCase):
    def test_registry_loads(self):
        reg = load_site_registry()
        self.assertGreaterEqual(reg.get("version", 0), 1)
        self.assertIn("harvard", reg.get("publishers", {}))
        self.assertIn("nexus", reg.get("projects", {}))

    def test_harvard_publisher_match(self):
        pubs = match_publishers("HBR digital transformation harvard business review")
        self.assertIn("harvard_business_review", pubs)

    def test_ssrn_publisher_match(self):
        pubs = match_publishers("SSRN finance working paper")
        self.assertIn("ssrn", pubs)

    def test_project_urls(self):
        urls = get_project_urls("kaari")
        self.assertTrue(any("cloudinary" in u for u in urls))

    def test_publisher_spec_has_channel(self):
        spec = publisher_channel_spec("harvard")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.get("channel"), "openalex_harvard")
        self.assertIn("openalex_filter", spec)

    def test_list_endpoints(self):
        self.assertGreater(len(list_publishers()), 5)
        self.assertGreater(len(list_projects()), 2)

    def test_compress_harvest(self):
        block = compress_harvest_results([
            {"title": "Doc", "url": "https://example.com", "text_content": "Hello world " * 20},
        ])
        self.assertIn("Doc", block)
        self.assertLessEqual(len(block), 4000)


class TestDispatcherPhase3(unittest.TestCase):
    def test_harvard_in_plan(self):
        plan = get_dispatcher().plan("Harvard Business Review digital transformation", project="general")
        channels = [c.channel for c in plan.channels]
        self.assertTrue(
            "openalex_harvard" in channels or "openalex_publisher" in channels,
            f"expected Harvard channel, got {channels}",
        )

    def test_ssrn_finance_plan(self):
        plan = get_dispatcher().plan("portfolio optimization finance SSRN", project="general")
        channels = [c.channel for c in plan.channels]
        self.assertIn("openalex_ssrn", channels)

    def test_project_sites_in_plan(self):
        plan = get_dispatcher().plan("study cloudinary image transforms", project="kaari")
        target = [c for c in plan.channels if c.channel == "target_site"]
        self.assertTrue(any("cloudinary" in c.params.get("url", "") for c in target))


class TestResearchIngest(unittest.TestCase):
    def test_resource_name_doi(self):
        name = _resource_name({"doi": "10.1234/abc", "title": "Test"}, source="arxiv")
        self.assertEqual(name, "doc:doi:10.1234/abc")

    @patch("server.research_ingest.db_conn")
    def test_ingest_paper_skips_empty(self, mock_db):
        result = ingest_paper({})
        self.assertEqual(result["status"], "skipped")
        mock_db.assert_not_called()


class TestHarnessToolsPhase3(unittest.TestCase):
    def test_research_sites_list_callable(self):
        from server.harness_mcp_bridge import call_harness_tool
        raw = call_harness_tool("research_sites_list")
        data = json.loads(raw)
        self.assertIn("publishers", data)
        self.assertIn("projects", data)

    def test_harness_research_plan_project(self):
        from server.harness_mcp_bridge import call_harness_tool
        raw = call_harness_tool(
            "harness_research_plan",
            query="Harvard law review constitutional",
            project="general",
            explain=False,
        )
        data = json.loads(raw)
        self.assertEqual(data.get("project"), "general")
        self.assertTrue(data.get("channels"))


if __name__ == "__main__":
    unittest.main()