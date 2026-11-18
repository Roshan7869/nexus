"""W1: validated loader for config/harness_sites.yaml."""
from __future__ import annotations

from pathlib import Path

import pytest

from server.harness_sites import (
    DEFAULT_PATH,
    load_catalog,
    parse_catalog,
)


def _cat(data):
    return parse_catalog(data)


def test_valid_base_url_site_is_ready():
    cat = _cat({
        "sites": {
            "web-dev": [{"name": "getdesign", "base_url": "https://design.example.com/search",
                         "weight": 1.4, "auth_env": "NEXUS_GETDESIGN_KEY"}]
        }
    })
    assert cat.errors == []
    site = cat.get("getdesign")
    assert site.status == "ready"
    assert site.use_cases == ("web-dev",)
    assert site.weight == 1.4
    assert site.auth_env == "NEXUS_GETDESIGN_KEY"
    assert [s.name for s in cat.for_use_case("web-dev")] == ["getdesign"]
    assert cat.for_use_case("research") == []


def test_placeholder_without_base_url_or_channel_is_unconfigured_not_error():
    cat = _cat({"sites": {"web-dev": [{"name": "getdesign", "channel": "channels/nope.py"}]}})
    assert cat.errors == []
    site = cat.get("getdesign")
    assert site.status == "unconfigured"
    assert "not found" in site.reason
    assert cat.for_use_case("web-dev") == []  # unconfigured sites are never consulted


def test_builtin_name_without_url_is_builtin():
    cat = _cat({"sites": {"research": [{"name": "arxiv", "weight": 1.1}]}})
    assert cat.get("arxiv").status == "builtin"
    assert [s.name for s in cat.for_use_case("research")] == ["arxiv"]


def test_defaults_apply_when_entry_omits_fields():
    cat = _cat({
        "defaults": {"weight": 0.9, "cache_ttl": 60},
        "sites": {"research": [{"name": "paper-site", "base_url": "https://papers.example.org"}]},
    })
    site = cat.get("paper-site")
    assert site.weight == 0.9
    assert site.cache_ttl == 60
    assert site.timeout_seconds == 20
    assert site.max_results == 10


@pytest.mark.parametrize("entry, fragment", [
    ({"name": "x1", "base_url": "http://plain.example.com"}, "https"),
    ({"name": "x2", "base_url": "https://user:pw@example.com"}, "credentials"),
    ({"name": "x3", "base_url": "https://127.0.0.1/api"}, "non-public"),
    ({"name": "x4", "base_url": "https://169.254.169.254/latest"}, "non-public"),
    ({"name": "x5", "api_key": "sk-live-123", "base_url": "https://a.example.com"}, "auth_env"),
    ({"name": "x6", "token": "abc", "base_url": "https://a.example.com"}, "auth_env"),
    ({"name": "Bad Name", "base_url": "https://a.example.com"}, "invalid name"),
    ({"name": "x7", "weight": 9, "base_url": "https://a.example.com"}, "weight"),
    ({"name": "x8", "weight": True, "base_url": "https://a.example.com"}, "weight"),
    ({"name": "x9", "use_cases": ["nonsense"], "base_url": "https://a.example.com"}, "unknown use_case"),
    ({"name": "x10", "auth_env": "lower_case", "base_url": "https://a.example.com"}, "auth_env"),
    ({"name": "x11", "channel": "../../etc/passwd"}, "relative path"),
    ({"name": "x12", "mystery": 1, "base_url": "https://a.example.com"}, "unknown key"),
])
def test_invalid_entries_are_rejected_with_reason(entry, fragment):
    cat = _cat({"sites": {"research": [entry]}})
    assert cat.sites == []
    assert len(cat.errors) == 1
    assert fragment in cat.errors[0]


def test_duplicate_name_second_entry_rejected():
    cat = _cat({"sites": {
        "web-dev": [{"name": "dup-site", "base_url": "https://a.example.com"}],
        "research": [{"name": "dup-site", "base_url": "https://b.example.com"}],
    }})
    assert [s.name for s in cat.sites] == ["dup-site"]
    assert any("duplicate" in e for e in cat.errors)


def test_unknown_group_recorded_but_other_groups_still_load():
    cat = _cat({"sites": {
        "bogus-group": [{"name": "lost", "base_url": "https://a.example.com"}],
        "research": [{"name": "kept", "base_url": "https://b.example.com"}],
    }})
    assert [s.name for s in cat.sites] == ["kept"]
    assert any("bogus-group" in e for e in cat.errors)


def test_group_default_use_case_is_its_key():
    cat = _cat({"sites": {"trading": [{"name": "tsite", "base_url": "https://t.example.com"}]}})
    assert cat.get("tsite").use_cases == ("trading",)


def test_missing_file_yields_error_not_exception(tmp_path: Path):
    cat = load_catalog(tmp_path / "absent.yaml")
    assert cat.sites == []
    assert any("not found" in e for e in cat.errors)


def test_yaml_syntax_error_yields_error_not_exception(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("sites: [unclosed", encoding="utf-8")
    cat = load_catalog(bad)
    assert cat.sites == []
    assert any("YAML parse error" in e for e in cat.errors)


def test_shipped_config_loads_without_errors():
    """The real config/harness_sites.yaml must validate cleanly."""
    assert DEFAULT_PATH.exists()
    cat = load_catalog()
    assert cat.errors == [], cat.errors
    getdesign = cat.get("getdesign")
    assert getdesign is not None
    assert getdesign.status == "unconfigured"  # placeholder until the user fills it in
    assert cat.get("arxiv").status == "builtin"
    assert "arxiv" in [s.name for s in cat.for_use_case("research")]
    # arxiv is bound to both ai-ml and research in the file; merged, not duplicated
    assert cat.get("arxiv").use_cases == ("ai-ml", "research")
    assert "<your-trading-site>" in cat.placeholders


def test_builtin_repeated_across_groups_merges_use_cases():
    cat = _cat({"sites": {
        "devops": [{"name": "github", "weight": 1.1}],
        "general-dev": [{"name": "github", "weight": 1.1}],
    }})
    assert cat.errors == []
    assert len(cat.sites) == 1
    assert cat.get("github").use_cases == ("devops", "general-dev")


def test_placeholder_template_names_are_skipped_silently():
    cat = _cat({"sites": {"trading": [{"name": "<your-site>", "weight": 1.4}]}})
    assert cat.errors == [] and cat.sites == []
    assert cat.placeholders == ["<your-site>"]
