#!/usr/bin/env python3
"""JEV + RLCD fix plan: cluster decider, decision classifier, RLCD gate.

Stdlib + numpy only; no network, no API key, no torch.
    python -m unittest tests.test_decision_layer
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.cluster_decider import (  # noqa: E402
    ACCEPT_THRESHOLD,
    MARGIN,
    ClusterDecision,
    LocalCentroidDecider,
)
from server.decision_classifier import (  # noqa: E402
    LocalHeuristicClassifier,
    TypedAnswer,
    TypeSafeJevClassifier,
    _extract_answer,
    get_classifier,
)
from server.router_v2 import rlcd_ranker as R  # noqa: E402


class FakeEncoder:
    def __init__(self, vec):
        self._vec = np.asarray(vec, dtype=np.float32)

    def encode(self, _text):
        return self._vec


def decider_with(centroids: dict[str, list[float]], query_vec: list[float]) -> LocalCentroidDecider:
    d = LocalCentroidDecider(db_path=":memory:")
    d._centroids = {k: np.asarray(v, dtype=np.float32) for k, v in centroids.items()}
    d._encoder = lambda: FakeEncoder(query_vec)
    return d


class TestClusterMargin(unittest.TestCase):
    """The margin rule is the starvation fix: ambiguity must decline, not narrow."""

    def test_clear_winner_is_accepted(self):
        d = decider_with({"a": [1.0, 0.0], "b": [0.0, 1.0]}, [1.0, 0.1])
        dec = d.decide("task")
        self.assertTrue(dec.ok)
        self.assertEqual(dec.cluster, "a")
        self.assertGreaterEqual(dec.confidence, ACCEPT_THRESHOLD)

    def test_ambiguous_top_two_declines(self):
        # both centroids nearly identical -> margin below threshold -> decline
        d = decider_with({"a": [1.0, 0.0], "b": [0.99, 0.02]}, [1.0, 0.01])
        dec = d.decide("task")
        self.assertFalse(dec.ok)
        self.assertEqual(dec.cluster, "general")
        self.assertIn("ambiguous_margin", dec.reason)

    def test_below_accept_threshold_declines(self):
        # orthogonal to the only centroid -> similarity ~0
        d = decider_with({"a": [1.0, 0.0]}, [0.0, 1.0])
        dec = d.decide("task")
        self.assertFalse(dec.ok)
        self.assertIn("below_accept", dec.reason)

    def test_no_centroids_declines(self):
        d = decider_with({}, [1.0, 0.0])
        dec = d.decide("task")
        self.assertFalse(dec.ok)
        self.assertEqual(dec.reason, "no_centroids")

    def test_empty_task_declines(self):
        d = decider_with({"a": [1.0, 0.0]}, [1.0, 0.0])
        self.assertFalse(d.decide("   ").ok)

    def test_decision_is_serialisable(self):
        dec = ClusterDecision(cluster="a", confidence=0.5, margin=0.1)
        self.assertIsInstance(dec.to_dict(), dict)

    def test_defaults_match_calibration(self):
        # guards against silently re-tightening the floor that caused total decline
        self.assertLessEqual(ACCEPT_THRESHOLD, 0.25)
        self.assertLessEqual(MARGIN, 0.05)


class TestDecisionClassifier(unittest.TestCase):
    def test_local_intent_accepts_clear_signal(self):
        c = LocalHeuristicClassifier()
        ans = c.classify("intent", "write a research survey of papers", ["research", "coding"])
        self.assertTrue(ans.ok)
        self.assertEqual(ans.value, "research")

    def test_local_intent_declines_without_signal(self):
        c = LocalHeuristicClassifier()
        ans = c.classify("intent", "hello there", ["research", "coding"])
        self.assertFalse(ans.ok)
        self.assertEqual(ans.value, None)

    def test_local_declines_unknown_kind(self):
        ans = LocalHeuristicClassifier().classify("cluster", "x", ["a", "b"])
        self.assertFalse(ans.ok)

    def test_jev_unavailable_without_key(self):
        jev = TypeSafeJevClassifier()
        # no key configured in this environment
        self.assertFalse(jev.available())
        ans = jev.classify("cluster", "x", ["a"])
        self.assertFalse(ans.ok)
        self.assertEqual(ans.reason, "no_api_key")

    def test_get_classifier_auto_falls_back_without_key(self):
        c = get_classifier("auto")
        self.assertTrue(c.available())  # local backend always available

    def test_extract_answer_shapes(self):
        self.assertEqual(_extract_answer({"cluster": "a"}, "cluster"), "a")
        self.assertEqual(_extract_answer({"answers": {"cluster": {"value": "b"}}}, "cluster"), "b")
        self.assertEqual(_extract_answer({"value": "c"}, "cluster"), "c")
        self.assertIsNone(_extract_answer({"nope": 1}, "cluster"))

    def test_out_of_set_value_is_refused(self):
        class Stub(TypeSafeJevClassifier):
            def __init__(self):
                self._key = "test"
        stub = Stub()
        stub_ans = TypedAnswer(None, source="jev", reason="out_of_set:x")
        self.assertFalse(stub_ans.ok)


class TestRlcdGate(unittest.TestCase):
    def _db(self, n_pairs: int) -> str:
        tmp = Path(tempfile.mkdtemp()) / "rlcd.db"
        con = sqlite3.connect(tmp)
        con.executescript("""
            CREATE TABLE reflection_fixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER,
                alternative_resource_id INTEGER,
                failure_summary TEXT
            );
            CREATE TABLE resources (
                id INTEGER PRIMARY KEY, embedding BLOB
            );
        """)
        for i in range(n_pairs):
            con.execute("INSERT INTO reflection_fixes(resource_id, alternative_resource_id, "
                        "failure_summary) VALUES (?,?,?)", (1, 2, f"task {i}"))
        for rid in (1, 2):
            con.execute("INSERT INTO resources(id, embedding) VALUES (?,?)",
                        (rid, np.ones(8, dtype=np.float32).tobytes()))
        con.commit()
        con.close()
        return str(tmp)

    def test_gate_refuses_below_min_pairs(self):
        db = self._db(3)
        with self.assertRaises(R.InsufficientPairs) as ctx:
            R.train(db)
        self.assertIn("MIN_PAIRS", str(ctx.exception))

    def test_train_runs_at_or_above_gate(self):
        db = self._db(R.MIN_PAIRS)
        model = R.train(db)
        self.assertEqual(model.n_pairs, R.MIN_PAIRS)
        self.assertEqual(model.weights.shape, (8,))
        self.assertGreaterEqual(model.train_accuracy, 0.0)

    def test_active_weight_defaults_to_zero(self):
        # AGENTS.md: no default weight change without an A/B receipt
        self.assertEqual(R.active_weight(), 0.0)

    def test_load_pairs_skips_self_pairs(self):
        db = self._db(0)
        con = sqlite3.connect(db)
        con.execute("INSERT INTO reflection_fixes(resource_id, alternative_resource_id) VALUES (5,5)")
        con.execute("INSERT INTO reflection_fixes(resource_id, alternative_resource_id) VALUES (6,7)")
        con.commit()
        con.close()
        pairs = R.load_pairs(db)
        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0].dispreferred_id, pairs[0].preferred_id), (6, 7))


if __name__ == "__main__":
    unittest.main(verbosity=2)
