"""Unit tests for evals.scorer — pure functions, no server imports (card 3.3)."""
import pytest


def decision(skills=("investigate", "qa"), cluster="analyzer_planner", tier=2, latency_ms=42.0):
    return {
        "skills": [{"name": n, "score": 1.0} for n in skills],
        "cluster": cluster,
        "tier": tier,
        "latency_ms": latency_ms,
    }


class TestScoreCase:
    def test_skill_match_in_top_k(self):
        case = {"id": "x", "prompt": "p", "expect_skills": ["investigate"], "top_k": 5}
        r = decision()
        s = __import__("evals.scorer", fromlist=["score_case"]).score_case(case, r)
        assert s["skill_match"] is True and s["passed"] is True

    def test_skill_match_respects_top_k(self):
        case = {"id": "x", "prompt": "p", "expect_skills": ["qa"], "top_k": 1}
        s = __import__("evals.scorer", fromlist=["score_case"]).score_case(case, decision())
        assert s["skill_match"] is False  # qa is rank 2, top_k=1

    def test_cluster_check(self):
        case = {"id": "x", "prompt": "p", "accept_clusters": ["quality_security"]}
        mod = __import__("evals.scorer", fromlist=["score_case"])
        s = mod.score_case(case, decision())
        assert s["cluster_ok"] is False
        ok = mod.score_case(case, decision(cluster="quality_security"))
        assert ok["cluster_ok"] is True

    def test_omitted_checks_are_none_and_health_only_passes(self):
        case = {"id": "ood", "prompt": "p"}  # OOD case: no expectations
        mod = __import__("evals.scorer", fromlist=["score_case"])
        s = mod.score_case(case, decision())
        assert s["skill_match"] is None and s["cluster_ok"] is None and s["tier_ok"] is None
        assert s["passed"] is True  # health-only case: no checks, healthy → pass

    def test_empty_skills_is_unhealthy(self):
        mod = __import__("evals.scorer", fromlist=["score_case"])
        s = mod.score_case({"id": "x", "expect_skills": ["a"]}, decision(skills=()))
        assert s["healthy"] is False and s["passed"] is False

    def test_tier_check(self):
        mod = __import__("evals.scorer", fromlist=["score_case"])
        s = mod.score_case({"id": "x", "expect_tier": 2}, decision())
        assert s["tier_ok"] is True


class TestSummarize:
    def test_rates_ignore_none_checks(self):
        mod = __import__("evals.scorer", fromlist=["summarize"])
        results = [
            {"passed": True, "skill_match": True, "cluster_ok": None, "tier_ok": None, "latency_ms": 10},
            {"passed": False, "skill_match": False, "cluster_ok": None, "tier_ok": None, "latency_ms": 30},
            {"passed": True, "skill_match": None, "cluster_ok": True, "tier_ok": None, "latency_ms": 20},
        ]
        s = mod.summarize(results)
        assert s["cases"] == 3 and s["passed"] == 2
        assert s["skill_match_rate"] == 0.5  # 1 of 2 non-None
        assert s["cluster_ok_rate"] == 1.0
        assert s["avg_latency_ms"] == 20.0

    def test_empty(self):
        mod = __import__("evals.scorer", fromlist=["summarize"])
        assert mod.summarize([])["cases"] == 0
