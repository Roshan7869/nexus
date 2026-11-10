"""S1-e: event_bridge.record_outcome delegates to learning.record_outcome."""
from unittest.mock import patch

from server import event_bridge


def test_delegates_terminal_outcome():
    with patch.object(event_bridge, "LEARNING_ENABLED", True), \
         patch("server.learning.record_outcome", return_value={"ok": 1}) as rec:
        out = event_bridge.record_outcome("correct", {"skill_name": "s1", "project": "p"}, ide="t")
    assert out == {"ok": 1}
    kw = rec.call_args.kwargs
    assert kw["skill_name"] == "s1" and kw["outcome"] == "correct" and kw["ide"] == "t"


def test_error_maps_to_wrong_timeout_to_skip():
    with patch.object(event_bridge, "LEARNING_ENABLED", True), \
         patch("server.learning.record_outcome", return_value={}) as rec:
        event_bridge.record_outcome("error", {"skill_name": "s"})
        event_bridge.record_outcome("timeout", {"skill_name": "s"})
    assert [c.kwargs["outcome"] for c in rec.call_args_list] == ["wrong", "skip"]


def test_disabled_returns_none_without_calling_learning():
    with patch.object(event_bridge, "LEARNING_ENABLED", False), \
         patch("server.learning.record_outcome") as rec:
        assert event_bridge.record_outcome("correct", {"skill_name": "s"}) is None
    rec.assert_not_called()
