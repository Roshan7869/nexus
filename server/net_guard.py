"""SSRF guard for outbound HTTP fetches.

assert_public_url() validates a URL before any request: scheme, then a single
getaddrinfo() resolution where EVERY returned address must be public.
guarded_request() wraps an httpx client with follow_redirects disabled and
follows 3xx responses manually, re-validating each Location (max 5 hops).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_HOPS = 5

_BLOCKED_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "0.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "::ffff:0:0/96",
    )
)


class UnsafeURLError(ValueError):
    """Raised when a URL is not allowed for outbound fetching."""


def _check_address(addr: str) -> None:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])  # drop IPv6 zone id
    for net in _BLOCKED_NETS:
        if ip.version == net.version and ip in net:
            raise UnsafeURLError(f"address {addr} is in blocked range {net}")
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:  # embedded IPv4 must also be public
        _check_address(str(mapped))


def assert_public_url(url: str, allow_http: bool = False) -> str:
    """Validate url and return the first resolved (validated) address.

    Raises UnsafeURLError if the scheme is not https (unless allow_http),
    the host is missing, resolution fails, or ANY resolved address is non-public.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "https" or (scheme == "http" and allow_http):
        pass
    else:
        raise UnsafeURLError(f"scheme {scheme!r} not allowed for {url!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"no host in {url!r}")
    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve {host!r}: {exc}") from exc
    if not infos:
        raise UnsafeURLError(f"no addresses for {host!r}")
    addresses = [info[4][0] for info in infos]
    for addr in addresses:
        _check_address(addr)
    return addresses[0]


async def guarded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    allow_http: bool = False,
    **kwargs,
) -> httpx.Response:
    """Send a request, following redirects manually with a guard on every hop.

    The client should be created with follow_redirects=False.
    """
    current = url
    verb = method.upper()
    for _hop in range(MAX_HOPS + 1):
        assert_public_url(current, allow_http=allow_http)
        resp = await client.request(verb, current, **kwargs)
        if not resp.is_redirect:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        if resp.status_code == 303 or (
            resp.status_code in (301, 302) and verb == "POST"
        ):
            verb = "GET"
            kwargs.pop("json", None)
            kwargs.pop("data", None)
            kwargs.pop("content", None)
        current = urljoin(current, location)
    raise UnsafeURLError(f"more than {MAX_HOPS} redirects from {url!r}")
