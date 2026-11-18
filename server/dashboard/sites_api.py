"""HTTP handlers for the harness sites UI (plan W4).

Reads are open to whatever can reach the dashboard. Writes are accepted only from loopback
clients with a same-origin (or absent) Origin header, because the dashboard listens on all
interfaces and has no authentication.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from server.harness_sites_store import StoreError, add_site, catalog_view, remove_site

MAX_BODY_BYTES = 16_384
LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
LOCAL_ORIGINS = ("http://localhost", "http://127.0.0.1", "http://[::1]")


def _json(status: int, payload: Any) -> tuple[int, bytes]:
    return status, json.dumps(payload, indent=2, default=str).encode("utf-8")


def handle_get(path: str) -> tuple[int, bytes] | None:
    if urlparse(path).path != "/api/harness/sites":
        return None
    return _json(200, catalog_view())


def _write_allowed(client_ip: str, origin: str | None) -> str | None:
    if client_ip not in LOOPBACK:
        return "writes are only accepted from localhost"
    if origin and not origin.startswith(LOCAL_ORIGINS):
        return "cross-origin write refused"
    return None


def handle_write(method: str, path: str, body: bytes, client_ip: str,
                 origin: str | None) -> tuple[int, bytes] | None:
    route = urlparse(path).path
    if method == "POST" and route == "/api/harness/sites":
        pass
    elif method == "DELETE" and route.startswith("/api/harness/sites/"):
        pass
    else:
        return None

    denied = _write_allowed(client_ip, origin)
    if denied:
        return _json(403, {"error": denied})

    if method == "DELETE":
        name = route.rsplit("/", 1)[-1]
        try:
            removed = remove_site(name)
        except (StoreError, ValueError) as exc:
            return _json(400, {"error": str(exc)})
        if not removed:
            return _json(404, {"error": f"'{name}' is not a UI-added site"})
        return _json(200, {"removed": name})

    if len(body) > MAX_BODY_BYTES:
        return _json(413, {"error": "body too large"})
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json(400, {"error": "body must be JSON"})
    if not isinstance(data, dict):
        return _json(400, {"error": "body must be an object"})
    try:
        site = add_site(str(data.get("use_case", "")), data.get("site") or {})
    except StoreError as exc:
        return _json(400, {"error": str(exc)})
    return _json(201, {"added": site.name, "status": site.status, "use_cases": list(site.use_cases)})
