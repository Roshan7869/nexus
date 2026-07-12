"""V2: Fusion tests — RRF scoring, dedup, timeout, weight application."""
import time, math
from server.context_harness.fusion import fuse, rank_by_relevance, RRF_K
from server.context_harness.models import HarnessResult


class TestFusionRRF:
    """RRF scoring correctness: K=60, rank-based weight, cross-source dedup."""

    def test_rrf_formula_correctness(self):
        """RRF score for rank-0 should be 1.0/60.0 = 0.01667, rank-1 = 1.0/61.0 = 0.01639."""
        r0 = [HarnessResult(source="nexus", content="alpha", score=1.0, metadata={})]
        r1 = [HarnessResult(source="nexus", content="beta", score=1.0, metadata={})]
        fused = fuse(r0, [], [], max_results=10)
        assert len(fused) == 1
        assert round(fused[0].score, 6) == round(1.0 / RRF_K, 6)

    def test_three_source_fusion_ordering(self):
        """Fused results should be sorted by RRF score descending."""
        n = [HarnessResult(source="nexus", content=f"n:{i}", score=1.0, metadata={}) for i in range(3)]
        i = [HarnessResult(source="internet", content=f"i:{i}", score=1.0, metadata={}) for i in range(3)]
        m = [HarnessResult(source="memory", content=f"m:{i}", score=1.0, metadata={}) for i in range(3)]
        fused = fuse(n, i, m, max_results=9)
        for j in range(len(fused) - 1):
            assert fused[j].score >= fused[j + 1].score, f"Position {j} out of order"

    def test_cross_source_no_false_dedup(self):
        """Results with same content from different sources are NOT deduped (key includes source_name)."""
        n = [HarnessResult(source="nexus", content="same content here for dedup testing purposes only", score=1.0, metadata={})]
        m = [HarnessResult(source="memory", content="same content here for dedup testing purposes only", score=1.0, metadata={})]
        fused = fuse(n, [], m, max_results=10)
        assert len(fused) == 2

    def test_within_source_dedup_by_content(self):
        """Same source with identical first-80-char content counts as duplicate."""
        n = [
            HarnessResult(source="nexus", content="identical content prefix for within-source dedup check", score=1.0, metadata={}),
            HarnessResult(source="nexus", content="identical content prefix for within-source dedup check", score=1.0, metadata={}),
        ]
        fused = fuse(n, [], [], max_results=10)
        assert len(fused) == 1

    def test_dedup_within_source_cumulative_rrf(self):
        """Within-source dedup: rank-0 + rank-1 same content -> combined RRF score."""
        n = [
            HarnessResult(source="nexus", content="within source dedup content abcdefgh", score=1.0, metadata={}),
            HarnessResult(source="nexus", content="within source dedup content abcdefgh", score=1.0, metadata={}),
        ]
        fused = fuse(n, [], [], max_results=10)
        expected = (1.0 / RRF_K) + (1.0 / (RRF_K + 1))
        assert abs(fused[0].score - expected) < 1e-6

    def test_max_results_clamping(self):
        """max_results=3 should return at most 3 items."""
        n = [HarnessResult(source="nexus", content=f"max:{i}", score=1.0, metadata={}) for i in range(10)]
        fused = fuse(n, [], [], max_results=3)
        assert len(fused) == 3

    def test_max_results_less_than_available(self):
        """When max_results exceeds available count, return all."""
        n = [HarnessResult(source="nexus", content="only one", score=1.0, metadata={})]
        fused = fuse(n, [], [], max_results=50)
        assert len(fused) == 1

    def test_empty_inputs(self):
        """All empty lists should produce empty result."""
        fused = fuse([], [], [], max_results=10)
        assert fused == []

    def test_one_empty_source(self):
        """One empty source should not crash."""
        n = [HarnessResult(source="nexus", content="test a", score=1.0, metadata={})]
        fused = fuse(n, [], [], max_results=10)
        assert len(fused) == 1
        assert fused[0].source == "nexus"

    def test_score_weight_application(self):
        """Result score weights the RRF contribution: score=0.5 -> 0.5/60 = 0.00833."""
        n = [HarnessResult(source="nexus", content="weighted content score test", score=0.5, metadata={})]
        fused = fuse(n, [], [], max_results=10)
        assert abs(fused[0].score - (0.5 / RRF_K)) < 1e-6

    def test_high_rank_decay(self):
        """Deep rank should have small RRF contribution (<0.01 for rank 99)."""
        n = [HarnessResult(source="nexus", content=f"deep:{i}", score=1.0, metadata={}) for i in range(100)]
        fused = fuse(n, [], [], max_results=100)
        assert fused[-1].score < 0.01

    def test_source_maintained_for_unique_results(self):
        """Each unique result retains its original source tag."""
        n = [HarnessResult(source="nexus", content="unique nexus", score=1.0, metadata={})]
        i = [HarnessResult(source="internet", content="unique internet", score=1.0, metadata={})]
        m = [HarnessResult(source="memory", content="unique memory", score=1.0, metadata={})]
        fused = fuse(n, i, m, max_results=3)
        sources = {r.source for r in fused}
        assert "nexus" in sources
        assert "internet" in sources
        assert "memory" in sources


class TestFusionRankByRelevance:
    """Tiebreaker ranking by keyword overlap."""

    def test_keyword_overlap_boost(self):
        """Results with query keyword overlap get +0.1 per overlapping word."""
        results = [
            {"content": "machine learning tensorflow", "score": 0.5},
            {"content": "react frontend css", "score": 0.5},
        ]
        ranked = rank_by_relevance(results, "tensorflow machine", score_key="score")
        assert ranked[0]["content"] == "machine learning tensorflow"

    def test_tiebreaker_with_different_scores(self):
        """Higher base score still wins even with fewer keyword matches."""
        results = [
            {"content": "javascript node npm", "score": 0.9},
            {"content": "react js frontend react hooks", "score": 0.3},
        ]
        ranked = rank_by_relevance(results, "react hooks", score_key="score")
        assert ranked[0]["score"] == 0.9

    def test_empty_query_no_keyword_overlap(self):
        """Empty query should rank by score only."""
        results = [
            {"content": "some result", "score": 0.3},
            {"content": "better result", "score": 0.9},
        ]
        ranked = rank_by_relevance(results, "", score_key="score")
        assert ranked[0]["score"] == 0.9

    def test_score_key_fallback(self):
        """Uses 'description' or 'note' if 'content' key is missing."""
        results = [
            {"description": "machine learning model", "score": 0.5},
            {"note": "react component library", "score": 0.5},
        ]
        ranked = rank_by_relevance(results, "machine", score_key="score")
        assert "machine" in ranked[0].get("description", "") or "machine" in ranked[0].get("note", "")


class TestFusionEdgeCases:
    """Edge cases: numerical stability, mixed content lengths."""

    def test_long_content_prefix_match(self):
        """Content longer than 80 chars: same prefix + same source dedups."""
        long = "x" * 200
        n = [
            HarnessResult(source="nexus", content=long, score=0.9, metadata={}),
            HarnessResult(source="nexus", content=long, score=0.9, metadata={}),
        ]
        fused = fuse(n, [], [], max_results=10)
        assert len(fused) == 1

    def test_short_content_no_false_dedup(self):
        """Very short similar prefixes should NOT false-dedup if content differs."""
        n = [HarnessResult(source="nexus", content="ab", score=0.9, metadata={})]
        m = [HarnessResult(source="memory", content="ac", score=0.9, metadata={})]
        fused = fuse(n, [], m, max_results=10)
        assert len(fused) == 2

    def test_three_source_all_unique(self):
        """Three sources with same content but different source names are NOT merged (key includes source)."""
        n = [HarnessResult(source="nexus", content="three sources unique content", score=0.8, metadata={})]
        i = [HarnessResult(source="internet", content="three sources unique content", score=0.9, metadata={})]
        m = [HarnessResult(source="memory", content="three sources unique content", score=0.7, metadata={})]
        fused = fuse(n, i, m, max_results=10)
        assert len(fused) == 3

    def test_fuse_with_mixed_empty_lists(self):
        """Mix of empty and non-empty lists should work."""
        n = [HarnessResult(source="nexus", content="only nexus result", score=1.0, metadata={})]
        fused = fuse(n, [], [], max_results=5)
        assert len(fused) == 1
        assert fused[0].source == "nexus"

    def test_rrf_k_constant(self):
        """RRF_K must be 60.0 as defined in the module."""
        assert RRF_K == 60.0

    def test_fuse_deterministic(self):
        """Same inputs produce same output order."""
        n = [HarnessResult(source="nexus", content=f"det:{i}", score=1.0 - i * 0.01, metadata={}) for i in range(5)]
        r1 = fuse(n, [], [], max_results=5)
        r2 = fuse(n, [], [], max_results=5)
        for a, b in zip(r1, r2):
            assert a.content == b.content
            assert a.score == b.score

    def test_large_fusion_performance(self):
        """Fusing 100 items from 3 sources should complete under 100ms."""
        n = [HarnessResult(source="nexus", content=f"n:{i}".ljust(80, "_"), score=1.0, metadata={}) for i in range(100)]
        i = [HarnessResult(source="internet", content=f"i:{i}".ljust(80, "_"), score=1.0, metadata={}) for i in range(100)]
        m = [HarnessResult(source="memory", content=f"m:{i}".ljust(80, "_"), score=1.0, metadata={}) for i in range(100)]
        t0 = time.perf_counter()
        fuse(n, i, m, max_results=100)
        dt = (time.perf_counter() - t0) * 1000
        assert dt < 100, f"Fuse took {dt:.2f}ms, expected <100ms"
