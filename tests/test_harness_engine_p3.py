#!/usr/bin/env python3
"""P3 harness engine: toolsets, plan, no paper-as-skill injection."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))


class TestToolsets(unittest.TestCase):
    def test_research_intent_loads_research_toolset(self):
        from server.harness_engine.toolsets import plan_toolsets, detect_intent

        self.assertEqual(detect_intent("survey arxiv papers on thompson sampling"), "research")
        ts = plan_toolsets("research arxiv papers on tool routing")
        self.assertIn("core", ts)
        self.assertIn("research", ts)

    def test_general_skips_research_toolset(self):
        from server.harness_engine.toolsets import plan_toolsets, detect_intent

        self.assertEqual(detect_intent("fix login button CSS"), "general")
        ts = plan_toolsets("fix login button CSS")
        self.assertIn("core", ts)
        self.assertNotIn("research", ts)

    def test_browse_toolset(self):
        from server.harness_engine.toolsets import plan_toolsets

        ts = plan_toolsets("scrape this website with browser harness")
        self.assertIn("browser", ts)


class TestPlan(unittest.TestCase):
    def test_plan_has_tools_not_empty_for_research(self):
        from server.harness_engine.plan import plan_harness

        p = plan_harness("literature review of academic papers on MCP tools")
        self.assertEqual(p.intent, "research")
        self.assertIn("research", p.channels)
        self.assertTrue(any("harness_research" in t for t in p.tools))


class TestAttachNoSkillPollution(unittest.TestCase):
    def test_strips_legacy_harness_skill_rows(self):
        from server.harness_engine.attach import attach_harness_to_result

        result = {
            "skills": [
                {"name": "qa", "score": 0.9, "_source": "iso"},
                {
                    "name": "Some Paper Title About Agents",
                    "score": 0.4,
                    "_source": "harness_research",
                    "type": "doc",
                },
            ],
            "pipeline": ["iso_scoring", "harness_research"],
        }
        out = attach_harness_to_result(result, "fix css layout", execute_research=False)
        names = [s["name"] for s in out["skills"]]
        self.assertIn("qa", names)
        self.assertNotIn("Some Paper Title About Agents", names)
        self.assertIn("toolsets", out)
        self.assertIn("harness_plan", out)
        self.assertEqual(out.get("research_artifacts"), [])

    def test_research_artifacts_not_in_skills(self):
        from server.harness_engine.attach import attach_harness_to_result

        fake_research = {
            "query": "q",
            "channels": ["arxiv"],
            "artifacts": [
                {
                    "title": "Fake Paper XYZ",
                    "url": "https://arxiv.org/abs/0000",
                    "snippet": "s",
                    "score": 0.9,
                    "channel": "arxiv",
                    "kind": "document",
                    "meta": {},
                }
            ],
            "n_artifacts": 1,
            "latency_ms": 1.0,
            "source": "harness_engine",
        }
        with patch(
            "server.harness_engine.attach.run_research_orchestrator",
            return_value=fake_research,
        ):
            out = attach_harness_to_result(
                {"skills": [{"name": "investigate", "_source": "intent"}]},
                "research arxiv papers on multi-agent systems",
                execute_research=True,
            )
        skill_names = [s["name"] for s in out["skills"]]
        self.assertNotIn("Fake Paper XYZ", skill_names)
        self.assertEqual(out["research_artifacts"][0]["title"], "Fake Paper XYZ")


class TestOrchestratorMapping(unittest.TestCase):
    def test_item_to_artifact(self):
        from server.harness_engine.orchestrator import _item_to_artifact

        a = _item_to_artifact(
            {"title": "T", "url": "http://x", "abstract": "hello", "score": 0.5},
            "arxiv",
        )
        self.assertIsNotNone(a)
        self.assertEqual(a.title, "T")
        self.assertEqual(a.channel, "arxiv")


if __name__ == "__main__":
    unittest.main()
