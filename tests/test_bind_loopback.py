"""T2-3: unified server binds loopback only (start.sh and uvicorn.run in unified.py)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_start_sh_binds_loopback():
    text = (ROOT / "start.sh").read_text()
    hosts = re.findall(r"--host\s+(\S+)", text)
    assert hosts, "start.sh has no --host flag"
    assert all(h == "127.0.0.1" for h in hosts), hosts


def test_unified_uvicorn_run_binds_loopback():
    tree = ast.parse((ROOT / "server" / "unified.py").read_text())
    hosts = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "uvicorn"):
            for kw in node.keywords:
                if kw.arg == "host":
                    hosts.append(ast.literal_eval(kw.value))
    assert hosts == ["127.0.0.1"], hosts
