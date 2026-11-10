"""T1-2: reflector makes no seeded random decisions; no executor means 'unverified'."""
from unittest.mock import patch

from server.self_reflector import SelfReflector


def _reflector():
    # Bypass __init__ so no live DB is opened; the no-executor path never touches it.
    return SelfReflector.__new__(SelfReflector)


def test_no_executor_returns_unverified_without_rng():
    cands = [{"name": "tool-a", "times_correct": 3, "times_triggered": 4}]
    with patch("random.random", side_effect=AssertionError("RNG must not be used")):
        out = _reflector().execute_with_retry("some task", cands)
    assert out["status"] == "unverified"
    assert out["attempts"] == []


def test_simulation_removed():
    assert not hasattr(SelfReflector, "_simulate_execution")
