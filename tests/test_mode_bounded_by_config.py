"""T2-2: caller-supplied mode cannot exceed the server ceiling; unknown modes error."""
import pytest

from server import broker_runtime


def test_godmode_refused_from_caller():
    with pytest.raises(ValueError, match="exceeds server ceiling"):
        broker_runtime.normalize_mode("godmode")


def test_unknown_mode_is_error_not_safemode():
    with pytest.raises(ValueError, match="unknown mode"):
        broker_runtime.normalize_mode("turbo")


def test_safemode_and_default_accepted():
    assert broker_runtime.normalize_mode("safemode") == "safemode"
    assert broker_runtime.normalize_mode(None) == "safemode"
    assert broker_runtime.normalize_mode("  SafeMode ") == "safemode"


def test_create_task_run_returns_error_for_godmode(monkeypatch):
    monkeypatch.setattr(broker_runtime, "initialize_runtime",
                        lambda: pytest.fail("must not touch DB for refused mode"))
    result = broker_runtime.create_task_run("anything", mode="godmode")
    assert result["status"] == "error"
    assert "godmode" in result["error"]


def test_create_task_run_returns_error_for_unknown_mode(monkeypatch):
    monkeypatch.setattr(broker_runtime, "initialize_runtime",
                        lambda: pytest.fail("must not touch DB for refused mode"))
    result = broker_runtime.create_task_run("anything", mode="turbo")
    assert result["status"] == "error"
