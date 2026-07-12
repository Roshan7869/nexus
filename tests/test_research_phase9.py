#!/usr/bin/env python3
"""Phase 9: Research synthesis pipeline tests."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.research_service import (
    get_research_status,
    list_research_sources,
    run_research_synthesis,
    _map_gaps,
    MIN_SOURCES_GATE,
)
from server.research_pipeline import ResearchPipeline, Finding


class TestResearchStatus(unittest.TestCase):
    def test_status_keys(self):
        status = get_research_status()
        self.assertEqual(status["phase"], 9)
        self.assertIn("arxiv", status["pipeline_sources"])
        self.assertEqual(status["min_sources_gate"], MIN_SOURCES_GATE)

    def test_list_sources(self):
        sources = list_research_sources()
        ids = {s["id"] for s in sources}
        self.assertIn("arxiv", ids)
        self.assertIn("local", ids)


class TestGapMapping(unittest.TestCase):
    def test_maps_router_gap(self):
        gaps = _map_gaps(
            "unified router cluster rerank",
            [{"title": "HGMF routing", "summary": "cluster rerank"}],
        )
        gap_ids = [g["gap"] for g in gaps]
        self.assertIn("I-05_router_v5", gap_ids)


class TestRunSynthesis(unittest.TestCase):
    @patch("server.research_pipeline.ArxivSource.search")
    @patch("server.research_pipeline.GithubSource.search")
    @patch("server.research_pipeline.LocalSource.search")
    def test_multi_source_gate(self, mock_local, mock_gh, mock_arxiv):
        mock_arxiv.return_value = [Finding("arxiv", "Paper A", "http://a", "routing")]
        mock_gh.return_value = [Finding("github", "Repo B", "http://b", "agent tools")]
        mock_local.return_value = []
        report = run_research_synthesis(
            "tool routing agents",
            sources=["arxiv", "github"],
            project="nexus",
        )
        self.assertEqual(report["total_findings"], 2)
        self.assertTrue(report["gate_pass"])
        self.assertIn("project_context", report)
        self.assertIn("nexus_gap_mapping", report)

    @patch("server.research_pipeline.ArxivSource.search", return_value=[])
    @patch("server.research_pipeline.GithubSource.search", return_value=[])
    def test_gate_fails_single_source(self, _gh, _arxiv):
        report = run_research_synthesis(
            "obscure topic xyz",
            sources=["arxiv", "github"],
        )
        self.assertFalse(report["gate_pass"])

    @patch("server.research_service._persist_summary")
    @patch("server.research_pipeline.LocalSource.search")
    def test_persist_flag(self, mock_local, mock_persist):
        mock_local.return_value = [Finding("local", "Doc", "resource://1", "harness FTSR")]
        mock_persist.return_value = {"persisted": True}
        report = run_research_synthesis("harness metrics", sources=["local"], persist=True)
        mock_persist.assert_called_once()
        self.assertTrue(report["persist"]["persisted"])


class TestPipelineLocal(unittest.TestCase):
    def test_local_source_runs(self):
        rp = ResearchPipeline()
        report = rp.research("routing cluster", sources=["local"])
        self.assertIn("findings", report)
        self.assertIn("per_source", report)


class TestMCPJSON(unittest.TestCase):
    def test_serializable(self):
        text = json.dumps(get_research_status())
        parsed = json.loads(text)
        self.assertEqual(parsed["phase"], 9)


if __name__ == "__main__":
    unittest.main()