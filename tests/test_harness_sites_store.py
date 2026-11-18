"""W4: validated write path for UI-managed harness sites."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.harness_sites import load_merged_catalog
from server.harness_sites_store import StoreError, add_site, catalog_view, remove_site

MAIN_YAML = """
sites:
  research:
    - { name: arxiv, weight: 1.1 }
  web-dev:
    - name: getdesign
      channel: channels/getdesign.py
"""


@pytest.fixture
def paths(tmp_path: Path):
    main = tmp_path / "harness_sites.yaml"
    main.write_text(MAIN_YAML, encoding="utf-8")
    local = tmp_path / "harness_sites.local.yaml"
    return main, local


def test_add_site_writes_local_file_and_is_loadable(paths):
    main, local = paths
    site = add_site("web-dev", {"name": "my-design-site",
                                "base_url": "https://design.example.com/api",
                                "weight": 1.3}, main, local)
    assert site.status == "ready"
    assert site.use_cases == ("web-dev",)
    assert local.exists()
    on_disk = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert on_disk["sites"]["web-dev"][0]["name"] == "my-design-site"
    # main file untouched
    assert main.read_text(encoding="utf-8") == MAIN_YAML
    assert "my-design-site" in [s.name for s in load_merged_catalog(main, local).for_use_case("web-dev")]


def test_add_rejects_name_already_in_main_file(paths):
    main, local = paths
    with pytest.raises(StoreError, match="already exists"):
        add_site("research", {"name": "arxiv", "base_url": "https://a.example.com"}, main, local)
    assert not local.exists()


def test_add_rejects_private_url_and_writes_nothing(paths):
    main, local = paths
    with pytest.raises(StoreError, match="non-public"):
        add_site("web-dev", {"name": "evil", "base_url": "https://10.0.0.5/x"}, main, local)
    assert not local.exists()


def test_add_rejects_inline_secret(paths):
    main, local = paths
    with pytest.raises(StoreError, match="unsupported fields"):
        add_site("web-dev", {"name": "k", "base_url": "https://a.example.com", "api_key": "sk-1"},
                 main, local)
    assert not local.exists()


def test_add_rejects_unknown_use_case(paths):
    main, local = paths
    with pytest.raises(StoreError, match="unknown use_case"):
        add_site("nonsense", {"name": "x1", "base_url": "https://a.example.com"}, main, local)


def test_add_rejects_bad_name(paths):
    main, local = paths
    with pytest.raises(StoreError):
        add_site("web-dev", {"name": "Has Spaces", "base_url": "https://a.example.com"}, main, local)


def test_duplicate_add_is_rejected_and_file_unchanged(paths):
    main, local = paths
    add_site("web-dev", {"name": "twice", "base_url": "https://a.example.com"}, main, local)
    before = local.read_text(encoding="utf-8")
    with pytest.raises(StoreError, match="already exists"):
        add_site("research", {"name": "twice", "base_url": "https://b.example.com"}, main, local)
    assert local.read_text(encoding="utf-8") == before


def test_remove_only_touches_local_file(paths):
    main, local = paths
    add_site("web-dev", {"name": "temp-site", "base_url": "https://a.example.com"}, main, local)
    assert remove_site("temp-site", local) is True
    assert remove_site("temp-site", local) is False
    assert remove_site("arxiv", local) is False  # hand-edited entries are not removable via UI
    assert main.read_text(encoding="utf-8") == MAIN_YAML


def test_catalog_view_marks_removable_and_never_leaks_secret_values(paths, monkeypatch):
    main, local = paths
    monkeypatch.setenv("NEXUS_VIEW_KEY", "super-secret-value")
    add_site("web-dev", {"name": "keyed", "base_url": "https://a.example.com",
                         "auth_env": "NEXUS_VIEW_KEY"}, main, local)
    view = catalog_view(main, local)
    by_name = {s["name"]: s for s in view["sites"]}
    assert by_name["keyed"]["removable"] is True
    assert by_name["keyed"]["auth_env_set"] is True
    assert by_name["arxiv"]["removable"] is False
    assert "super-secret-value" not in repr(view)
