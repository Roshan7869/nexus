"""W4: dashboard handlers for harness sites. Store calls are stubbed so no real config is touched."""
from __future__ import annotations

import json

import pytest

from server.dashboard import sites_api
from server.harness_sites import SiteDef
from server.harness_sites_store import StoreError


@pytest.fixture
def stub_store(monkeypatch):
    calls = {"add": [], "remove": []}

    def fake_add(use_case, site):
        calls["add"].append((use_case, site))
        if site.get("name") == "bad":
            raise StoreError("nope")
        return SiteDef(name=site["name"], use_cases=(use_case,), weight=1.0, cache_ttl=300,
                       timeout_seconds=20, max_results=10, status="ready",
                       base_url=site.get("base_url"))

    def fake_remove(name):
        calls["remove"].append(name)
        return name == "mine"

    monkeypatch.setattr(sites_api, "add_site", fake_add)
    monkeypatch.setattr(sites_api, "remove_site", fake_remove)
    return calls


def _post(body: dict, ip="127.0.0.1", origin=None):
    return sites_api.handle_write("POST", "/api/harness/sites", json.dumps(body).encode(), ip, origin)


def test_post_from_loopback_adds_site(stub_store):
    status, raw = _post({"use_case": "web-dev",
                         "site": {"name": "design", "base_url": "https://d.example.com"}})
    assert status == 201
    assert json.loads(raw)["added"] == "design"
    assert stub_store["add"][0][0] == "web-dev"


def test_post_from_lan_is_forbidden_and_never_reaches_store(stub_store):
    status, raw = _post({"use_case": "web-dev", "site": {"name": "x"}}, ip="192.168.1.20")
    assert status == 403
    assert stub_store["add"] == []


def test_cross_origin_post_refused(stub_store):
    status, _ = _post({"use_case": "web-dev", "site": {"name": "x"}}, origin="https://evil.example")
    assert status == 403
    assert stub_store["add"] == []


def test_localhost_origin_allowed(stub_store):
    status, _ = _post({"use_case": "web-dev", "site": {"name": "x", "base_url": "https://a.example.com"}},
                      origin="http://localhost:8765")
    assert status == 201


def test_store_validation_error_maps_to_400(stub_store):
    status, raw = _post({"use_case": "web-dev", "site": {"name": "bad"}})
    assert status == 400
    assert "nope" in json.loads(raw)["error"]


def test_oversized_and_malformed_bodies_rejected(stub_store):
    big = sites_api.handle_write("POST", "/api/harness/sites", b"x" * (sites_api.MAX_BODY_BYTES + 1),
                                 "127.0.0.1", None)
    assert big[0] == 413
    bad = sites_api.handle_write("POST", "/api/harness/sites", b"not json", "127.0.0.1", None)
    assert bad[0] == 400
    arr = sites_api.handle_write("POST", "/api/harness/sites", b"[1,2]", "127.0.0.1", None)
    assert arr[0] == 400
    assert stub_store["add"] == []


def test_delete_only_removes_ui_added(stub_store):
    ok = sites_api.handle_write("DELETE", "/api/harness/sites/mine", b"", "127.0.0.1", None)
    assert ok[0] == 200
    missing = sites_api.handle_write("DELETE", "/api/harness/sites/arxiv", b"", "127.0.0.1", None)
    assert missing[0] == 404
    lan = sites_api.handle_write("DELETE", "/api/harness/sites/mine", b"", "10.0.0.9", None)
    assert lan[0] == 403


def test_unrelated_paths_return_none(stub_store):
    assert sites_api.handle_write("POST", "/api/other", b"{}", "127.0.0.1", None) is None
    assert sites_api.handle_get("/api/metrics") is None
