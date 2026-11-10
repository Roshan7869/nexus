"""S1-c: report_session must keep quality 0.0 and None as given."""
from unittest.mock import patch

from server.tool_session_broker import ToolSession


def _report(quality):
    from server import execution_plane as ep
    sess = ToolSession(session_id="qzero-sess", project="p", query="q",
                       tools_loaded=["tool_a"], ide="http")
    broker = type("B", (), {})()
    broker.get_session = lambda sid: sess
    with patch("server.tool_session_broker.get_tool_session_broker", return_value=broker), \
         patch.object(ep, "artifact_manifest", return_value=(None, None)), \
         patch("server.learning.record_outcome", return_value={}) as rec, \
         patch.object(broker, "end_session", create=True, return_value={"status": "dumped"}, ):
        out = ep.report_session("qzero-sess", "correct", quality=quality)
    return out, rec


def test_zero_quality_is_kept():
    out, rec = _report(0.0)
    assert rec.call_args.kwargs["quality"] == 0.0
    assert out["quality"] == 0.0


def test_none_quality_is_not_coerced_to_one():
    out, rec = _report(None)
    assert rec.call_args.kwargs["quality"] is None
    assert out["quality"] is None
