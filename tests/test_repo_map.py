"""repo_map v0 — regex defs + mention-count ranking (Aider-style, zero deps).

Upgrade path (documented in module): tree-sitter grammars when repos get
polyglot; ranking > extraction is the load-bearing part.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/roshan/nexus")

from server.repo_map import build


def make_project(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "class Router:\n"
        "    def route(self, x):\n"
        "        return helpers.format(x)\n"
        "\n"
        "def bootstrap():\n"
        "    r = Router()\n"
        "    return r.route(1)\n"
        "\n"
        "def unused_thing():\n"
        "    pass\n")
    (tmp_path / "helpers.py").write_text(
        "def format(x):\n"
        "    return str(x)\n"
        "\n"
        "def never_called():\n"
        "    pass\n")
    (tmp_path / "main.py").write_text(
        "from app import bootstrap, Router\n"
        "\n"
        "bootstrap()\n"
        "Router().route('q')\n")
    # noise that must be skipped
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "pkg.py").write_text("def should_not_appear(): pass\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("function alsoSkipped() {}\n")
    return tmp_path


class TestBuild:
    def test_finds_defs_and_ranks_by_mentions(self, tmp_path):
        root = make_project(tmp_path)
        r = build(str(root))
        assert r["defs"] >= 5
        m = r["map"]
        # Aider-style contract: ranking is FILE-level (PageRank over the
        # cross-reference graph). Referenced symbols appear; unused defs ride
        # their file's rank but sink WITHIN the file (they never outrank a
        # used sibling from the same file).
        assert "Router" in m and "bootstrap" in m and "format" in m
        assert m.index("Router") < m.index("unused_thing")
        assert m.index("format") < m.index("never_called")

    def test_skips_vendor_dirs(self, tmp_path):
        root = make_project(tmp_path)
        r = build(str(root))
        assert "should_not_appear" not in r["map"]
        assert "alsoSkipped" not in r["map"]

    def test_budget_respected(self, tmp_path):
        root = make_project(tmp_path)
        r = build(str(root), budget_tokens=10)  # ~40 chars — tiny
        assert len(r["map"]) <= 10 * 4 + 200  # budget + per-line slack
        assert r["truncated"] is True

    def test_empty_dir_is_safe(self, tmp_path):
        r = build(str(tmp_path))
        assert r["defs"] == 0 and r["map"] == "" and r["truncated"] is False

    def test_meta_counts(self, tmp_path):
        root = make_project(tmp_path)
        r = build(str(root))
        assert r["files"] == 3  # app, helpers, main — not venv/node_modules
