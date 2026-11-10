"""S1-b: harness bridge must not derive an outcome from the payload."""
from unittest.mock import patch

from server.harness_mcp_bridge import _record_smart_research_outcome


def _run(payload):
    with patch("server.learning.record_outcome") as rec:
        _record_smart_research_outcome("q", payload, project="p")
    assert rec.call_count == 1
    return rec.call_args.kwargs


def test_non_empty_payload_is_unverified():
    kw = _run({"fused": [{"x": 1}], "channels": [{"channel": "a"}]})
    assert kw["outcome"] == "unverified"


def test_empty_payload_is_unverified_not_partial():
    kw = _run({})
    assert kw["outcome"] == "unverified"
