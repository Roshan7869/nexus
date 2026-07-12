#!/usr/bin/env python3
"""Phase 2: unified MCP surface + project-scoped context."""
import json
import sys
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.harness_mcp_bridge import (
    HARNESS_TOOL_NAMES,
    call_harness_tool,
    context_store_unified,
    is_research_query,
    research_harness_tools_for_query,
    _scoped_cache_key,
)
from server.ephemeral_mcp_proxy import CONTEXT_BASELINE_TOOLS, merge_tool_surface, filter_mcp_tool_names
from server.context_harness.cache import L3Cache


class TestHarnessBridge(unittest.TestCase):
    def test_harness_tool_registry_nonempty(self):
        self.assertGreater(len(HARNESS_TOOL_NAMES), 15)
        self.assertIn("harness_smart_research", HARNESS_TOOL_NAMES)
        self.assertIn("context_store", HARNESS_TOOL_NAMES)

    def test_research_query_detection(self):
        self.assertTrue(is_research_query("study Thompson Sampling arxiv papers"))
        self.assertFalse(is_research_query("fix docker deploy script"))

    def test_research_harness_tools(self):
        tools = research_harness_tools_for_query("arxiv SSRN finance research")
        self.assertIn("harness_smart_research", tools)
        self.assertIn("harness_arxiv", tools)

    def test_context_store_project_scoped(self):
        key = _scoped_cache_key("kaari", "frontend_ui", "cloudinary-notes")
        self.assertEqual(key, "kaari:frontend_ui:cloudinary-notes")
        raw = context_store_unified(
            key="cloudinary-notes",
            value="use signed uploads",
            source="learning",
            project="kaari",
            task_type="frontend_ui",
        )
        payload = json.loads(raw)
        self.assertEqual(payload["project"], "kaari")
        self.assertEqual(payload["scoped_key"], key)

    def test_context_doctor_callable(self):
        raw = call_harness_tool("context_doctor")
        data = json.loads(raw)
        self.assertIn("nexus_db", data)


class TestEphemeralProxyPhase2(unittest.TestCase):
    def test_context_baseline_in_merge(self):
        tools = merge_tool_surface(["ship"], include_harness=True, harness_tools=["harness_arxiv"])
        self.assertIn("context_store", tools)
        self.assertIn("harness_smart_research", tools)
        self.assertIn("ship", tools)

    def test_filter_includes_context_without_session(self):
        all_tools = ["task_start", "find_skills", "harness_arxiv", "ship", "context_search"]
        visible = filter_mcp_tool_names(all_tools, None)
        self.assertIn("context_search", visible)
        self.assertNotIn("ship", visible)


class TestL3ProjectScope(unittest.TestCase):
    def test_l3_project_isolation(self):
        l3 = L3Cache()
        l3.clear()
        l3.set("query", [{"id": 1}], project="kaari", task_type="general")
        l3.set("query", [{"id": 2}], project="nexus", task_type="general")
        kaari = l3.get("query", project="kaari", task_type="general")
        nexus = l3.get("query", project="nexus", task_type="general")
        self.assertEqual(kaari[0]["id"], 1)
        self.assertEqual(nexus[0]["id"], 2)


if __name__ == "__main__":
    unittest.main()