#!/usr/bin/env python3
"""Exec-test verifiers for golden cases (DeepSeek-V3.2 auto-verified pattern).

A case with verifier.kind == "exec_tests" names a module
evals/verifiers/<case_id>.py exposing  verify(code: str) -> bool.
The candidate's first ``` code fence is extracted and verified IN A
SUBPROCESS with a hard timeout — never in-process, never without one.

Usage:
    python evals/verifier_runner.py <case_id> <answer_text_path>   # prints PASS/FAIL/SKIP
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIERS_DIR = REPO_ROOT / "evals" / "verifiers"
TIMEOUT_S = 10

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)


def extract_code_block(answer: str) -> str | None:
    m = _FENCE_RE.search(answer or "")
    return m.group(1) if m else None


def run_verifier(case: dict, answer: str) -> bool | None:
    """None = no exec verifier for this case; True/False = verified verdict."""
    if (case.get("verifier") or {}).get("kind") != "exec_tests":
        return None
    vpath = VERIFIERS_DIR / f"{case['id']}.py"
    if not vpath.is_file():
        return None
    code = extract_code_block(answer)
    if not code:
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        code_path = f.name
    try:
        r = subprocess.run(
            [sys.executable, __file__, "--exec", str(vpath), code_path],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        return r.returncode == 0 and r.stdout.strip().endswith("PASS")
    except subprocess.TimeoutExpired:
        return False
    finally:
        Path(code_path).unlink(missing_ok=True)


def _exec_mode(vpath: str, code_path: str) -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location("verifier", vpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok = bool(mod.verify(Path(code_path).read_text()))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--exec":
        raise SystemExit(_exec_mode(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 3:
        case_id, answer_path = sys.argv[1], sys.argv[2]
        # bare CLI mode: verifier must exist
        vpath = VERIFIERS_DIR / f"{case_id}.py"
        if not vpath.is_file():
            print("SKIP (no verifier module)")
            raise SystemExit(0)
        verdict = run_verifier(
            {"id": case_id, "verifier": {"kind": "exec_tests"}},
            Path(answer_path).read_text(),
        )
        print("PASS" if verdict else ("FAIL" if verdict is False else "SKIP"))
        raise SystemExit(0 if verdict else 1)
    print(__doc__)
    raise SystemExit(2)
