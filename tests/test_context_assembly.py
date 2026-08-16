"""assemble() seam tests — budget, adapters, empty-corpus safety."""
import sys
from pathlib import Path

sys.path.insert(0, "/home/roshan/nexus")

import pytest

from server.context_assembly import assemble


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    db = tmp_path / "asm.db"
    monkeypatch.delenv("NEXUS_BROKER_DB_PATH", raising=False)
    monkeypatch.setenv("NEXUS_DB_PATH", str(db))
    from server.db import get_db_path
    if hasattr(get_db_path, "_cached"):
        del get_db_path._cached
    yield tmp_path
    # restore the process-wide cache for downstream tests in the same run
    if hasattr(get_db_path, "_cached"):
        del get_db_path._cached


class TestAssemble:
    def test_empty_corpus_is_safe_and_flagged(self, tmp_env):
        r = assemble("anything", "ghost-project")
        assert r["block"] == ""
        assert any("empty" in s or "skipped" in s for s in r["sources"])

    def test_notes_adapter_fires(self, tmp_env):
        from server.db import get_connection
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS project_notes ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, text TEXT,"
                     "ide TEXT, created_at TEXT DEFAULT (datetime('now')))")
        conn.execute("INSERT INTO project_notes (project, text) VALUES ('p1', 'prefer htmx')")
        conn.commit()
        conn.close()
        r = assemble("build page", "p1")
        assert "prefer htmx" in r["block"] and "Project notes" in r["block"]

    def test_agents_md_and_repo_map_via_git_root(self, tmp_env):
        root = tmp_env / "proj"
        root.mkdir()
        (root / "AGENTS.md").write_text("# P\nTrading dashboard conventions.")
        (root / "app.py").write_text("def router():\n    return helpers.go()\n")
        (root / "helpers.py").write_text("def go():\n    return 1\n")
        r = assemble("query", "proj", git_root=str(root))
        assert "Trading dashboard conventions." in r["block"]
        assert "Project map" in r["block"] and "router" in r["block"]

    def test_hard_budget_stops_adapters(self, tmp_env, monkeypatch):
        import server.context_assembly as ca

        def slow(q, p, g):
            import time
            time.sleep(0.05)
            return "", "repo_map"

        monkeypatch.setattr(ca, "_adapter_repo_map", slow)
        r = ca.assemble("q", "p", budget_ms=1)
        assert r["elapsed_ms"] >= 1
        assert any("budget" in s for s in r["sources"])

    def test_block_is_text_with_header(self, tmp_env):
        root = tmp_env / "p2"
        root.mkdir()
        (root / "AGENTS.md").write_text("# X\nconv.")
        r = assemble("q", "p2", task_type="coding", git_root=str(root))
        assert r["block"].startswith("# NEXUS context — p2 · coding")
