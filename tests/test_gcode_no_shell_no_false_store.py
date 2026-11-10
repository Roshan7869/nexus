"""FA-A10: gcode backend uses subprocess without a shell and never reports a false store."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from server.context_harness import gcode_backend as gb


def test_cli_exec_uses_argv_without_shell(monkeypatch, tmp_path):
    binary = tmp_path / "gcode"
    binary.write_text("")
    monkeypatch.setattr(gb, "GCODE_BINARY", Path(binary))
    calls: list[tuple] = []

    class Done:
        returncode = 0
        stdout = "  result  \n"

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Done()

    monkeypatch.setattr(gb.subprocess, "run", fake_run)
    monkeypatch.setattr(os, "popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("popen")))
    out = gb._cli_exec(["memory", "search", "x; rm -rf /"])
    assert out == "result"
    argv, kwargs = calls[0]
    assert argv == [str(binary), "memory", "search", "x; rm -rf /"]
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 10
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_cli_exec_nonzero_exit_returns_none(monkeypatch, tmp_path):
    binary = tmp_path / "gcode"
    binary.write_text("")
    monkeypatch.setattr(gb, "GCODE_BINARY", Path(binary))

    class Failed:
        returncode = 2
        stdout = "partial"

    monkeypatch.setattr(gb.subprocess, "run", lambda *a, **k: Failed())
    assert gb._cli_exec(["memory", "stats"]) is None


def test_store_never_reports_stored_without_write(monkeypatch, tmp_path):
    binary = tmp_path / "gcode"
    binary.write_text("")
    monkeypatch.setattr(gb, "GCODE_BINARY", Path(binary))
    monkeypatch.setattr(gb.subprocess, "run", lambda *a, **k: type("D", (), {"returncode": 0, "stdout": "ok"})())

    import server.context_harness.nexus_bridge as nb

    def no_write(*a, **k):
        raise AssertionError("must not write when gcode branch is active")

    monkeypatch.setattr(nb, "store_learning", no_write)
    result = gb.store("k", "v")
    assert result["status"] == "error"
    assert result["status"] != "stored"
