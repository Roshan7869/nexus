"""Isolated learning and propensity tests; no providers, models, or live DB."""
import json
import math
import random
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.capability_catalog import connect_db, initialize_schema
from server.capability_events import CapabilityEvents
from server.capability_recommender import CapabilityRecommender, score_distribution

PRINCIPAL = {"id": "owner", "harness": "codex", "projects": ["*"], "verifier": False}
VERIFIER = {**PRINCIPAL, "id": "trusted", "verifier": True}
CONTEXT = {"project": "alpha", "niche": "frontend_ui", "niche_version": "fixture-v1"}


class CatalogFixture:
    def __init__(self, items):
        self.items = items

    def list_capabilities(self, principal, cursor=None, limit=100, query=None):
        assert query is None  # Full eligible universe, including lexical nonmatches.
        offset = int(cursor or 0)
        end = offset + limit
        return {"items": self.items[offset:end], "catalog_revision": 1,
                "next_cursor": str(end) if end < len(self.items) else None}


@pytest.fixture
def learning(tmp_path):
    path = tmp_path / "learning.sqlite"
    initialize_schema(path)
    items = [{"capability_id": f"fixture:skill-{i:03d}", "canonical_id": f"fixture:skill-{i:03d}",
              "version": "v1", "kind": "skill", "name": f"skill {i}",
              "description": "react design" if i == 0 else "unrelated",
              "availability": "available"} for i in range(137)]
    items[0]["project_relevance"] = {"alpha": 1, "beta": 0}
    items[1]["project_relevance"] = {"alpha": 0, "beta": 1}
    items[-1]["availability"] = "unavailable"
    with connect_db(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in items:
            conn.execute("INSERT INTO capability_catalog "
                         "(capability_id,kind,publisher,logical_key,current_version) VALUES (?,'skill','fixture',?,'v1')",
                         (item["capability_id"], item["name"]))
            conn.execute("INSERT INTO capability_versions "
                         "(capability_id,version,content_hash,descriptor_json) VALUES (?,'v1','hash',?)",
                         (item["capability_id"], json.dumps(item)))
        conn.execute("INSERT INTO catalog_revisions(revision,manifest_hash) VALUES(1,'fixture')")
        for item in items:
            conn.execute("INSERT INTO catalog_revision_members "
                         "(revision,capability_id,version,availability,metadata_json) VALUES(1,?,'v1',?,?)",
                         (item["capability_id"], item["availability"], json.dumps(item)))
    events = CapabilityEvents(path)
    events.open_session(PRINCIPAL, CONTEXT, "session")
    return path, CatalogFixture(items), events


def invocation(path, name="inv", state="succeeded", capability="fixture:skill-000", version="v1", decision=None):
    with connect_db(path) as conn:
        conn.execute("INSERT INTO capability_invocations "
                     "(invocation_id,principal_id,session_id,capability_id,version,idempotency_key,args_hash,state,decision_id) "
                     "VALUES (?,'owner','session',?,?,?,'args',?,?)",
                     (name, capability, version, name, state, decision))
    return {"invocation_id": name, "capability_id": capability, "version": version, "evidence_ref": "test:" + name}


def rows(path, table):
    assert table in {"capability_beliefs", "capability_rewards", "capability_events", "recommendation_decisions"}
    with connect_db(path) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM " + table)]


@pytest.mark.parametrize("seed", [0, 1, 17, 20261112])
def test_full_distribution_replay_and_exact_conditional_logging(learning, seed):
    path, catalog, _ = learning
    engine = CapabilityRecommender(path, catalog)
    result = engine.recommend("session", "react design", 50, seed, PRINCIPAL)
    replay = engine.recommend("session", "react design", 50, seed, PRINCIPAL)
    assert result["recommendations"] == replay["recommendations"]
    assert result["draws"] == replay["draws"]
    assert len(result["distribution"]) == 136
    assert len(result["recommendations"]) == 50
    assert len({item["capability_id"] for item in result["recommendations"]}) == 50
    assert all(item["capability_id"] != "fixture:skill-136" for item in result["distribution"])
    rng = random.Random(seed)
    for draw in result["draws"]:
        distribution = draw["distribution"]
        assert math.fsum(item["probability"] for item in distribution) == pytest.approx(1, abs=1e-9)
        assert all(math.isfinite(item["probability"]) and item["probability"] > 0 for item in distribution)
        assert draw["remaining"] == [item["capability_id"] for item in distribution]
        expected = next(item for item in distribution if item["capability_id"] == draw["capability_id"])
        assert expected["probability"] == draw["conditional_probability"]
        assert draw["uniform"] == rng.random()
        mass = 0
        for item in distribution:
            mass += item["probability"]
            if draw["uniform"] < mass:
                assert item["capability_id"] == draw["capability_id"]
                break
    logged = json.loads(rows(path, "recommendation_decisions")[0]["draws_json"])
    assert logged["distribution"] == result["distribution"]
    assert logged["draws"] == result["draws"]
    assert len(logged["candidates"]) == 136
    assert not rows(path, "capability_beliefs")


def test_project_changes_relevance_and_invalid_scores_fall_back(learning, monkeypatch):
    path, catalog, events = learning
    events.open_session(PRINCIPAL, {**CONTEXT, "project": "beta"}, "beta")
    engine = CapabilityRecommender(path, catalog)
    alpha = engine.recommend("session", "", seed=1, principal=PRINCIPAL)
    beta = engine.recommend("beta", "", seed=1, principal=PRINCIPAL)
    assert alpha["distribution"][0]["probability"] > alpha["distribution"][1]["probability"]
    assert beta["distribution"][1]["probability"] > beta["distribution"][0]["probability"]
    monkeypatch.setattr(engine, "_lexical_relevance", lambda *args: ({"fixture:skill-000": float("nan")}, []))
    result = engine.recommend("session", "react", seed=1, principal=PRINCIPAL)
    assert "invalid_relevance_lexical_fallback" in result["degradation"]
    assert all(math.isfinite(item["probability"]) for item in result["distribution"])


def test_hierarchical_quality_and_legacy_counters():
    candidates = [{"capability_id": "a", "version": "v", "relevance": 0,
                   "times_correct": 999999, "times_wrong": -100}]
    assert score_distribution(candidates, {})[0]["predicted_success"] == .5
    counts = {("a", "v"): {"global_success": 8, "global_failure": 2, "niche_success": 1, "niche_failure": 3}}
    scored = score_distribution(candidates, counts)[0]
    assert scored["predicted_success"] == pytest.approx((2 * 9 / 12 + 1) / 6)
    assert scored["probability"] == 1
    assert score_distribution([{**candidates[0], "relevance": float("inf")}], {})[0]["relevance"] == 0


def test_concurrent_reward_replay_is_once_and_conflicts_fail(learning):
    path, _, events = learning
    evidence = invocation(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: events.record_verified_outcome("inv", "correct", evidence, VERIFIER), range(16)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(rows(path, "capability_rewards")) == 1
    belief = rows(path, "capability_beliefs")[0]
    assert (belief["successes"], belief["failures"]) == (1, 0)
    assert type(belief["successes"]) is int
    assert len(rows(path, "capability_events")) == 1
    with pytest.raises(ValueError, match="REWARD_CONFLICT"):
        events.record_verified_outcome("inv", "wrong", evidence, VERIFIER)
    assert len(rows(path, "capability_rewards")) == 1


@pytest.mark.parametrize("state", ["planned", "running", "unknown", "native_pending"])
def test_unexecuted_unknown_and_pending_have_no_reward(learning, state):
    path, _, events = learning
    evidence = invocation(path, state=state)
    with pytest.raises(ValueError, match="NOT_EXECUTED"):
        events.record_verified_outcome("inv", "correct", evidence, VERIFIER)
    assert not rows(path, "capability_rewards")
    assert not rows(path, "capability_beliefs")


def test_evidence_and_trust_validation(learning):
    path, _, events = learning
    evidence = invocation(path)
    with pytest.raises(PermissionError):
        events.record_verified_outcome("inv", "correct", evidence, PRINCIPAL)
    with pytest.raises(ValueError, match="BINDING"):
        events.record_verified_outcome("inv", "correct", {**evidence, "version": "other"}, VERIFIER)
    with pytest.raises(ValueError, match="INVALID_VERDICT"):
        events.record_verified_outcome("inv", .5, evidence, VERIFIER)
    with pytest.raises(PermissionError):
        events.record_verified_outcome("inv", "correct", evidence, {**VERIFIER, "projects": ["other"]})
    events.record_verified_outcome("inv", "timeout", evidence, VERIFIER)
    assert not rows(path, "capability_rewards")
    assert not rows(path, "capability_beliefs")


def test_session_feedback_does_not_spread_rewards(learning):
    path, catalog, events = learning
    CapabilityRecommender(path, catalog).recommend("session", "react", 5, 17, PRINCIPAL)
    for i in range(5):
        invocation(path, name=f"inv{i}", capability=f"fixture:skill-{i:03d}", state="unknown")
    assert events.close_session("session", "correct", PRINCIPAL)["status"] == "closed"
    assert not rows(path, "capability_rewards")
    assert not rows(path, "capability_beliefs")
    metrics = events.metrics({"session_id": "session"})
    assert metrics["exposure_count"] == 5
    assert metrics["invocation_count"] == metrics["unknown_outcome_count"] == 5
    assert metrics["success_posterior"] == .5
    assert metrics["legacy"]["included_in_quality"] is False


def test_version_and_decision_niche_are_pinned(learning):
    path, catalog, events = learning
    decision = CapabilityRecommender(path, catalog).recommend("session", "react", seed=1, principal=PRINCIPAL)
    evidence = invocation(path, decision=decision["decision_id"])
    with connect_db(path) as conn:
        conn.execute("UPDATE capability_sessions SET context_json=? WHERE session_id='session'",
                     (json.dumps({"niche": "changed", "niche_version": "changed"}),))
        conn.execute("INSERT INTO capability_versions(capability_id,version,content_hash,descriptor_json) "
                     "VALUES ('fixture:skill-000','v2','hash2','{}')")
        conn.execute("UPDATE capability_catalog SET current_version='v2' WHERE capability_id='fixture:skill-000'")
    events.record_verified_outcome("inv", "wrong", evidence, VERIFIER)
    belief = rows(path, "capability_beliefs")[0]
    assert (belief["version"], belief["niche"], belief["niche_version"]) == ("v1", "frontend_ui", "fixture-v1")
    assert (belief["successes"], belief["failures"]) == (0, 1)


def test_event_replay_and_owner_checks(learning):
    path, catalog, events = learning
    event = {"event_id": "fixed", "session_id": "session", "type": "skill_read", "payload": {"id": "skill"}}
    assert events.append_event(event) == events.append_event(event)
    with pytest.raises(ValueError, match="EVENT_CONFLICT"):
        events.append_event({**event, "payload": {"id": "other"}})
    assert events.metrics()["skill_read_count"] == 1
    for principal in ({**PRINCIPAL, "id": "other"}, {**PRINCIPAL, "harness": "other"}, {**PRINCIPAL, "projects": []}):
        with pytest.raises(PermissionError):
            CapabilityRecommender(path, catalog).recommend("session", "react", principal=principal)
    with connect_db(path) as conn:
        conn.execute("UPDATE capability_sessions SET expires_at=?", (time.time() - 1,))
    with pytest.raises(ValueError, match="INACTIVE"):
        CapabilityRecommender(path, catalog).recommend("session", "react", principal=PRINCIPAL)


@pytest.mark.parametrize("k,query", [(0, "ok"), (51, "ok"), (True, "ok"), (8, "a" * 16385), (8, "é" * 8193)])
def test_input_limits(learning, k, query):
    path, catalog, _ = learning
    with pytest.raises(ValueError):
        CapabilityRecommender(path, catalog).recommend("session", query, k, principal=PRINCIPAL)


def test_real_catalog_pagination_is_the_full_sampling_universe(learning):
    from server.capability_catalog import CapabilityCatalog
    path, _, _ = learning
    catalog = CapabilityCatalog(path)
    result = CapabilityRecommender(path, catalog).recommend("session", "react", 8, 42, PRINCIPAL)
    assert len(result["distribution"]) == 136
    recommended = {item["capability_id"] for item in result["recommendations"]}
    other = next(item for item in result["distribution"] if item["capability_id"] not in recommended)
    assert catalog.get_capability(other["capability_id"], "v1")["version"] == "v1"


def test_policy_masks_hidden_incompatible_and_unready_capabilities(learning):
    path, catalog, _ = learning
    catalog.items[2]["visibility"] = {"principals": ["other"]}
    catalog.items[3]["requirements"] = ["missing-provider"]
    catalog.items[4].update(kind="native_action", adapter_id="missing", method="click",
                            execution_policy={"allow": True})
    catalog.items[5]["execution_policy"] = {"principals": ["other"]}
    catalog.items[6]["execution_policy"] = {"harnesses": ["other"]}
    result = CapabilityRecommender(path, catalog).recommend("session", "", 8, 42, PRINCIPAL)
    eligible = {item["capability_id"] for item in result["distribution"]}
    assert all(f"fixture:skill-{i:03d}" not in eligible for i in range(2, 7))
    assert "fixture:skill-002" not in {item["capability_id"] for item in result["excluded"]}
    assert math.fsum(item["probability"] for item in result["distribution"]) == pytest.approx(1)


def test_empty_and_fully_ineligible_catalogs_return_discovery(learning, tmp_path):
    from server.capability_catalog import CapabilityCatalog
    empty_path = tmp_path / "empty.sqlite"
    initialize_schema(empty_path)
    CapabilityEvents(empty_path).open_session(PRINCIPAL, CONTEXT, "empty")
    result = CapabilityRecommender(empty_path, CapabilityCatalog(empty_path)).recommend(
        "empty", "query", principal=PRINCIPAL)
    assert result["recommendations"] == result["distribution"] == []
    assert result["decision_id"] is None
    assert result["discovery"]["method"] == "list_capabilities"
    path, catalog, _ = learning
    for item in catalog.items:
        item["availability"] = "unavailable"
    result = CapabilityRecommender(path, catalog).recommend("session", "query", principal=PRINCIPAL)
    assert result["distribution"] == []
    assert result["decision_id"] is not None
    assert len(result["excluded"]) == 137


def test_belief_counts_equal_verified_invocations_and_legacy_is_separate(learning):
    path, _, events = learning
    for i in range(6):
        evidence = invocation(path, name=f"inv{i}", capability=f"fixture:skill-{i % 2:03d}")
        events.record_verified_outcome(f"inv{i}", "wrong" if i < 2 else "correct", evidence, VERIFIER)
    assert sum(item["successes"] + item["failures"] for item in rows(path, "capability_beliefs")) == 6
    with connect_db(path) as conn:
        conn.execute("CREATE TABLE resources(times_triggered REAL,times_correct REAL,times_wrong REAL)")
        conn.execute("INSERT INTO resources VALUES (1000000,-99999,0.1)")
    metrics = events.metrics({"project": "alpha"})
    assert (metrics["successes"], metrics["failures"]) == (4, 2)
    assert metrics["success_posterior"] == 5 / 8
    assert metrics["legacy"]["counters"]["times_correct"] == -99999
    assert events.metrics({"project": "other"})["invocation_count"] == 0


def test_version_priors_are_isolated_and_harness_beliefs_are_pooled(learning):
    path, catalog, events = learning
    evidence = invocation(path)
    events.record_verified_outcome("inv", "correct", evidence, VERIFIER)
    other = {**PRINCIPAL, "harness": "hermes"}
    events.open_session(other, CONTEXT, "hermes")
    engine = CapabilityRecommender(path, catalog)
    codex = engine.recommend("session", "", seed=1, principal=PRINCIPAL)
    hermes = engine.recommend("hermes", "", seed=1, principal=other)
    assert codex["distribution"] == hermes["distribution"]
    assert codex["distribution"][0]["predicted_success"] > .5
    assert codex["distribution"][1]["predicted_success"] == .5
    catalog.items[0]["version"] = "v2"
    result = engine.recommend("session", "", seed=1, principal=PRINCIPAL)
    assert result["distribution"][0]["predicted_success"] == .5
