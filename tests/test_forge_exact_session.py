"""T2-7: forge matches the exact project's sessions, never a prefix of another project."""
from __future__ import annotations

import pytest

from server import forge


def _set_tmux_sessions(monkeypatch, names: list[str]):
    out = "\n".join(names) + "\n"

    def fake_tmux(*args):
        if args and args[0] == "list-sessions":
            return 0, out, ""
        return 0, "", ""

    monkeypatch.setattr(forge, "_tmux", fake_tmux)


def test_project_a_does_not_match_project_a_b(monkeypatch):
    _set_tmux_sessions(monkeypatch, [
        "nexus-forge-a--lead",
        "nexus-forge-a-b--lead",
        "nexus-forge-a-b--peer",
    ])
    assert forge._discover_sessions("a") == ["nexus-forge-a--lead"]
    assert forge._discover_sessions("a-b") == ["nexus-forge-a-b--lead", "nexus-forge-a-b--peer"]


def test_stop_team_kills_only_own_sessions(monkeypatch):
    _set_tmux_sessions(monkeypatch, ["nexus-forge-a--lead", "nexus-forge-a-b--lead"])
    killed: list[str] = []
    monkeypatch.setattr(forge, "_kill_session", lambda s: killed.append(s) or 0)
    monkeypatch.setitem(forge._TEAMS, "a", [])
    result = forge.stop_team("a")
    assert killed == ["nexus-forge-a--lead"]
    assert result["killed"] == 1


def test_session_name_round_trip():
    name = forge._session_name("a-b", "frontend_ui")
    assert forge._session_project(name) == "a-b"
    assert forge._session_cluster(name) == "frontend_ui"


def test_project_containing_separator_rejected():
    with pytest.raises(ValueError):
        forge._session_name("a--b", "lead")


def test_non_forge_session_ignored(monkeypatch):
    _set_tmux_sessions(monkeypatch, ["main", "nexus-forge-a"])
    assert forge._discover_sessions("a") == []
