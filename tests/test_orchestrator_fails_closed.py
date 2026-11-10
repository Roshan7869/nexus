"""T1-1: orchestrator refuses to execute or attest without an approval record."""
import pytest

from server import orchestrator as orch


@pytest.fixture
def sentinel(monkeypatch):
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise AssertionError("executor must not run without approval")

    monkeypatch.setattr(orch, "phase_1_state_analysis",
                        lambda task, project: {"project": project})
    monkeypatch.setattr(orch, "phase_3_execute", _boom)
    monkeypatch.setattr(orch, "_write_checkpoint", _boom)
    return calls


def test_no_approval_blocks_and_executes_nothing(sentinel):
    out = orch.orchestrate("design and build a thing", "proj", auto_execute=True)
    assert out["status"] == "blocked_pending_approval"
    assert out["approved"] is False
    assert out["execution"] is None
    assert out["verification"] is None
    assert sentinel == []


def test_unapproved_record_is_still_blocked(sentinel):
    out = orch.orchestrate("build x", "proj", approval={"approved": False})
    assert out["status"] == "blocked_pending_approval"
    assert out["approved"] is False
    assert sentinel == []


def test_verification_unverified_without_declared_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plan = orch.phase_2_plan_creation({"project": "p"}, "implement a feature")
    report = orch.phase_4_verification(plan, {})
    verdicts = {c["block"]: c["verdict"] for c in report["checks"]}
    assert verdicts and set(verdicts.values()) == {"unverified"}
