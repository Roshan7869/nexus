"""T2-1: ruflo_invoke refuses denied tool names before dispatch."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

MCP_PATH = Path(__file__).resolve().parent.parent / "server" / "nexus-mcp-enhanced.py"


@pytest.fixture(scope="module")
def mcp_mod():
    spec = importlib.util.spec_from_file_location("nexus_mcp_ruflo_allow", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", [
    "terminal_execute", "terminal_create", "terminal_close",
    "browser_eval", "config_set", "system_reset",
])
def test_denied_names_do_not_dispatch(mcp_mod, monkeypatch, name):
    calls = []

    async def sentinel(tool, args):
        calls.append(tool)
        raise AssertionError("dispatch must not happen for denied tool")

    import server.integrations.ruflo_async_proxy as proxy
    monkeypatch.setattr(proxy, "acall_ruflo_tool", sentinel)

    out = json.loads(asyncio.run(mcp_mod.ruflo_invoke.fn(name, "{}")
                                 if hasattr(mcp_mod.ruflo_invoke, "fn")
                                 else mcp_mod.ruflo_invoke(name, "{}")))
    assert out["isError"] is True
    assert calls == []


def test_allowed_name_dispatches(mcp_mod, monkeypatch):
    seen = {}

    async def fake(tool, args):
        seen["tool"] = tool
        return json.dumps({"ok": True})

    import server.integrations.ruflo_async_proxy as proxy
    monkeypatch.setattr(proxy, "acall_ruflo_tool", fake)
    fn = getattr(mcp_mod.ruflo_invoke, "fn", mcp_mod.ruflo_invoke)
    asyncio.run(fn("memory_search", '{"query": "x"}'))
    assert seen["tool"] == "memory_search"


def test_denylist_is_module_constant(mcp_mod):
    assert {"terminal_*", "browser_eval", "config_set", "system_reset"} <= set(
        mcp_mod.RUFLO_DENIED_TOOL_PATTERNS)
