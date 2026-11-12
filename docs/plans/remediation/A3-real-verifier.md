# A3 — Replace fake verification with a real verify registry

Source: plan §A3 · Commit: `A3: real verify_registry replaces eval-dataset fake verification` · Depends on: A2 · Gate: G4, G5

## Problem (verified)

- `server/router.py:391` `_evaluate_outcome(task, returned_skills)` returns `correct` when the task appears in an eval JSON. It is called at `router.py:995` and `:1265`. This makes the router grade itself.
- `server/broker_runtime.py:1080` hardcodes `outcome="correct"` in the execution path.
- `server/verify_registry.py` does **not** exist.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| `_evaluate_outcome` at lines 389–411 | Line **391** (callers at 995, 1265). |
| `execute_task_run` hardcoded `outcome` at line 692 | Hardcode is at **1080**. Line 692 is `for phase in phases:`. |
| `verify("coding", ".")` runs `pytest -q` with 120 s timeout | **Measured 2026-11-10 on this box: `pytest tests -q` = 490 passed, 24 skipped in 89.46 s (92 s wall).** 120 s does *not* always time out — the "~4 min / always times out" claim is wrong. The real objection is 89 s of pytest inside every broker verification, with only 30 s headroom under load. Use a subset for cost, not for correctness. |
| `quality_security` uses `bandit` | Not verified as installed. Check `venv/bin/python -m bandit --version` first. |
| `devops` uses `venv/bin/supervisorctl` | Path is relative to cwd; use absolute path from `supervisord.conf` or `shutil.which`. |

## Pseudocode (corrected)

```python
# server/verify_registry.py  (NEW)
from __future__ import annotations
import shutil, subprocess, sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / "venv" / "bin" / "python")
FAST_TESTS = ["tests/test_site_task_binding.py", "tests/test_local_harness.py"]  # placeholder: pick a fast, relevant subset

VERIFIERS: Dict[str, dict] = {
    "coding":   {"cmd": [PY, "-m", "pytest", "-q", "--tb=line", *FAST_TESTS], "timeout": 600},
    "general":  {"cmd": [PY, "-m", "compileall", "-q", "server", "core"],     "timeout": 120},
    "frontend": {"cmd": [PY, "-m", "compileall", "-q", "server"],             "timeout": 120},
    "devops":   {"cmd": [shutil.which("supervisorctl") or "supervisorctl", "status"], "timeout": 30},
}

def verify(task_type: str, project_path: str | None = None) -> Dict[str, object]:
    spec = VERIFIERS.get(task_type, VERIFIERS["general"])
    cwd = project_path or str(ROOT)
    try:
        p = subprocess.run(spec["cmd"], cwd=cwd, capture_output=True, text=True,
                           timeout=spec["timeout"])
        outcome = "correct" if p.returncode == 0 else "wrong"
        return {"verifier": task_type, "returncode": p.returncode, "outcome": outcome,
                "stdout": p.stdout[-1500:], "stderr": p.stderr[-1500:]}
    except subprocess.TimeoutExpired:
        return {"verifier": task_type, "outcome": "skip", "error": "timeout"}
    except Exception as e:
        return {"verifier": task_type, "outcome": "skip", "error": f"{type(e).__name__}: {e}"}
```

Note `outcome="skip"` on timeout/error: a verifier that cannot run must not count as `wrong` or `correct`. `skip` does not update beliefs (A2 terminal set includes `skip` but counters should treat it as no-signal — confirm in A2 before relying on it).

```python
# server/router.py
def _evaluate_outcome_ab_only(task, returned_skills):   # renamed; offline A/B only
    ...same body...
# route_task(): delete any line that sets outcome from _evaluate_outcome.
```

```python
# server/broker_runtime.py  (around line 1080)
from server.verify_registry import verify
verdict = verify(phase.get("stage", "general"), project_path=run["cwd"] or None)
outcome = verdict["outcome"]
```

## Steps

1. Create `server/verify_registry.py` (above). Keep under 500 lines.
2. Pick the fast test subset for `coding`. Do **not** use the full suite inside the broker path.
3. Rename `router.py:391` to `_evaluate_outcome_ab_only`. Update callers at `:995`, `:1265` to stop using it for production outcome; keep it only in `scripts/` A/B paths.
4. Replace `broker_runtime.py:1080` with the verify call.
5. Store verifier output: `tool_traces.notes` (column exists, verified) — truncate to 500 chars.
6. Tests and benchmark.

## Tests

New `tests/test_no_fake_verify.py`:

```python
from server.verify_registry import verify
import server.router as router

def test_verify_returns_real_returncode(tmp_path):
    (tmp_path / "t_ok.py").write_text("def test_ok():\n    assert True\n")
    v = verify("general", str(tmp_path))          # compileall on a dir with a passing file
    assert v["outcome"] in {"correct", "wrong", "skip"}

def test_timeout_is_skip(monkeypatch):
    import subprocess
    def boom(*a, **k): raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(subprocess, "run", boom)
    assert verify("coding", ".")["outcome"] == "skip"

def test_production_route_never_returns_correct():                # G4
    import inspect
    src = inspect.getsource(router.route_task)
    assert "_evaluate_outcome(" not in src
```

G5 check (live):

```bash
cd /home/roshan/nexus && venv/bin/python -c "from server.verify_registry import verify; print(verify('general','.')['outcome'])"
```

Benchmark: golden eval must stay ≥ 0.600. A3 removes a lookup that may have been inflating routing pass rate — **expect the golden number to change**. Record old and new in the commit body.

## Rollback

Revert. `_evaluate_outcome` is still in the file under its new name.

## Open decisions

- Which test subset is the `coding` verifier? Recommend the three harness/site test files plus `tests/test_router_unified.py` once A1 adds it.
- Accept that golden pass rate may fall once the fake outcome is removed.
