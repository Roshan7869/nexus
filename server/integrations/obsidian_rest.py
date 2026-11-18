"""Obsidian Local REST API client for NEXUS.

Talks to the `obsidian-local-rest-api` plugin (verified live: v5.5.2 on
`https://127.0.0.1:27124`, vault `C:\\Users\\khatr\\Downloads\\nexus`).

Credentials resolve in this order, so nothing is hardcoded:
  1. ``OBSIDIAN_API_KEY`` environment variable
  2. ``~/.config/nexus/obsidian.json`` -> ``{"api_key": "...", "base_url": "..."}``
  3. the plugin's own ``data.json`` inside the vault (if ``OBSIDIAN_VAULT`` is set)

The endpoint is HTTPS with a self-signed certificate, so TLS verification is
disabled only when ``OBSIDIAN_VERIFY_TLS`` is unset/false (it defaults to false
for this loopback-only plugin). Stdlib only (urllib + ssl).
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE = os.environ.get("OBSIDIAN_API_URL", "https://127.0.0.1:27124")
CONFIG_FILE = Path.home() / ".config" / "nexus" / "obsidian.json"
_PLUGIN_DATA = Path(".obsidian") / "plugins" / "obsidian-local-rest-api" / "data.json"


class ObsidianUnavailable(RuntimeError):
    """Raised when the REST API cannot be reached or is unauthorised."""


def _config_file() -> dict:
    try:
        if CONFIG_FILE.is_file():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        pass
    return {}


def resolve_credentials() -> tuple[str, str]:
    """Return (base_url, api_key). Raises if no key can be found."""
    cfg = _config_file()
    base = (os.environ.get("OBSIDIAN_API_URL") or cfg.get("base_url")
            or DEFAULT_BASE).rstrip("/")

    key = (os.environ.get("OBSIDIAN_API_KEY") or "").strip()
    if not key:
        key = (cfg.get("api_key") or "").strip()
    if not key:
        vault = os.environ.get("OBSIDIAN_VAULT") or cfg.get("vault")
        if vault:
            p = Path(vault) / _PLUGIN_DATA
            try:
                if p.is_file():
                    key = (json.loads(p.read_text(encoding="utf-8")).get("apiKey") or "").strip()
            except (OSError, ValueError):
                pass
    if not key:
        raise ObsidianUnavailable(
            "no Obsidian API key: set OBSIDIAN_API_KEY or write "
            f"{CONFIG_FILE} with {{\"api_key\": \"...\"}}")
    return base, key


def _opener():
    ctx = ssl.create_default_context()
    if (os.environ.get("OBSIDIAN_VERIFY_TLS") or "").strip().lower() not in ("1", "true", "yes"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def _request(method: str, path: str, *, body: str | None = None,
             content_type: str = "text/markdown",
             params: dict | None = None, timeout: float = 15.0) -> tuple[int, str]:
    base, key = resolve_credentials()
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "Accept": "*/*",
    })
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise ObsidianUnavailable(f"cannot reach Obsidian REST API at {base}: {exc}") from exc


# ── operations ───────────────────────────────────────────────────────────────

def health() -> dict:
    """Plugin status + manifest."""
    status, text = _request("GET", "/", content_type="application/json")
    if status != 200:
        raise ObsidianUnavailable(f"Obsidian REST returned HTTP {status}: {text[:200]}")
    data = json.loads(text)
    return {"status": data.get("status"), "plugin": (data.get("manifest") or {}).get("name"),
            "version": (data.get("manifest") or {}).get("version"),
            "authenticated": (data.get("authenticated") is not False)}


def list_root() -> list[str]:
    """Files at the vault root."""
    status, text = _request("GET", "/vault/", content_type="application/json")
    if status != 200:
        raise ObsidianUnavailable(f"list failed HTTP {status}: {text[:200]}")
    return list(json.loads(text).get("files") or [])


def read_note(path: str) -> str:
    """Read one note by vault-relative path."""
    p = "/vault/" + urllib.parse.quote(path.lstrip("/"), safe="/")
    status, text = _request("GET", p)
    if status == 404:
        raise FileNotFoundError(f"note not found: {path}")
    if status != 200:
        raise ObsidianUnavailable(f"read failed HTTP {status}: {text[:200]}")
    return text


def write_note(path: str, content: str) -> dict:
    """Create or overwrite a note."""
    p = "/vault/" + urllib.parse.quote(path.lstrip("/"), safe="/")
    status, text = _request("PUT", p, body=content)
    if status not in (200, 201, 204):
        raise ObsidianUnavailable(f"write failed HTTP {status}: {text[:200]}")
    return {"written": path, "bytes": len(content.encode("utf-8")), "status": status}


def append_note(path: str, content: str) -> dict:
    """Append to a note (creates it if absent)."""
    p = "/vault/" + urllib.parse.quote(path.lstrip("/"), safe="/")
    status, text = _request("POST", p, body=content)
    if status not in (200, 201, 204):
        raise ObsidianUnavailable(f"append failed HTTP {status}: {text[:200]}")
    return {"appended": path, "bytes": len(content.encode("utf-8")), "status": status}


def search(query: str, *, limit: int = 10) -> list[dict]:
    """Simple search across the vault."""
    status, text = _request("POST", "/search/simple/", params={"query": query},
                            body="", content_type="application/json")
    if status != 200:
        return []
    out: list[dict] = []
    try:
        for hit in json.loads(text)[:limit]:
            out.append({"filename": hit.get("filename"), "score": hit.get("score")})
    except (ValueError, TypeError):
        return []
    return out


def ensure_folder(folder: str) -> None:
    """Best-effort folder creation (the API ignores duplicates)."""
    p = "/vault/" + urllib.parse.quote(folder.strip("/") + "/", safe="/")
    try:
        _request("PUT", p, body="", content_type="text/markdown")
    except ObsidianUnavailable:
        pass
