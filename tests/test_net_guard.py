"""Tests for server.net_guard (SSRF guard). Resolver is faked via monkeypatch."""
from __future__ import annotations

import socket

import httpx
import pytest

from server import net_guard
from server.net_guard import UnsafeURLError, assert_public_url, guarded_request


def _fake_resolver(monkeypatch, mapping: dict[str, list[str]]):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        addrs = mapping.get(host)
        if addrs is None:
            raise socket.gaierror(f"no such host {host}")
        out = []
        for addr in addrs:
            if ":" in addr:
                sockaddr = (addr, port, 0, 0)
                family = socket.AF_INET6
            else:
                sockaddr = (addr, port)
                family = socket.AF_INET
            out.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return out

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "addr",
    ["127.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "::ffff:127.0.0.1",
     "10.1.2.3", "172.16.0.1", "192.168.1.1", "0.0.0.0", "fc00::1", "fe80::1",
     "::ffff:10.0.0.1"],
)
def test_private_addresses_refused(monkeypatch, addr):
    _fake_resolver(monkeypatch, {"target.example": [addr]})
    with pytest.raises(UnsafeURLError):
        assert_public_url("https://target.example/x")


def test_public_address_allowed(monkeypatch):
    _fake_resolver(monkeypatch, {"public.example": ["93.184.216.34"]})
    assert assert_public_url("https://public.example/") == "93.184.216.34"


def test_multi_record_one_private_refused(monkeypatch):
    _fake_resolver(monkeypatch, {"mixed.example": ["93.184.216.34", "10.0.0.7"]})
    with pytest.raises(UnsafeURLError):
        assert_public_url("https://mixed.example/")


def test_http_refused_unless_allowed(monkeypatch):
    _fake_resolver(monkeypatch, {"public.example": ["93.184.216.34"]})
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://public.example/")
    assert assert_public_url("http://public.example/", allow_http=True) == "93.184.216.34"


def test_non_http_scheme_refused(monkeypatch):
    _fake_resolver(monkeypatch, {"public.example": ["93.184.216.34"]})
    with pytest.raises(UnsafeURLError):
        assert_public_url("file:///etc/passwd")


async def test_redirect_to_private_refused_on_second_hop(monkeypatch):
    _fake_resolver(monkeypatch, {
        "front.example": ["93.184.216.34"],
        "internal.example": ["169.254.169.254"],
    })
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://internal.example/meta"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    async with client:
        with pytest.raises(UnsafeURLError):
            await guarded_request(client, "GET", "https://front.example/start")
    assert seen == ["https://front.example/start"]


async def test_redirect_chain_to_public_followed(monkeypatch):
    _fake_resolver(monkeypatch, {
        "a.example": ["93.184.216.34"],
        "b.example": ["93.184.216.35"],
    })

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example":
            return httpx.Response(301, headers={"location": "https://b.example/final"})
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    async with client:
        resp = await guarded_request(client, "GET", "https://a.example/")
    assert resp.status_code == 200
    assert str(resp.url) == "https://b.example/final"


async def test_redirect_loop_capped_at_max_hops(monkeypatch):
    _fake_resolver(monkeypatch, {"loop.example": ["93.184.216.34"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://loop.example/again"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    async with client:
        with pytest.raises(UnsafeURLError):
            await guarded_request(client, "GET", "https://loop.example/")
    assert net_guard.MAX_HOPS == 5
