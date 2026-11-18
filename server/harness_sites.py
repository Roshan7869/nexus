"""Loader and validator for ``config/harness_sites.yaml`` (plan W1).

The YAML file is the user-editable registry of harness sites, grouped by use case
(``web-dev``, ``research``, ...). This module turns it into validated ``SiteDef``
records. It does not perform network I/O: URL checks are syntactic (https, no
private IP literal). DNS and address checks happen at fetch time via
``server.net_guard.assert_public_url``.

Status per site:
  ready         user site with a ``base_url`` (served by the generic HTTP channel)
                or a ``channel`` file that exists on disk
  builtin       name is already registered in code (server/context_harness/server.py)
  unconfigured  placeholder: no ``base_url`` and no channel file on disk
Invalid entries are not returned; they are recorded in ``SiteCatalog.errors``.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from server.context_harness.project_types import ProjectType
from server.net_guard import _check_address, UnsafeURLError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "config" / "harness_sites.yaml"
LOCAL_PATH = REPO_ROOT / "config" / "harness_sites.local.yaml"  # written by the dashboard UI
CHANNEL_ROOT = REPO_ROOT / "server" / "context_harness"

SITE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
AUTH_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
KNOWN_USE_CASES = frozenset(p.value for p in ProjectType)

# Names registered in code today (docs/design/05-SITES-REGISTRY.md §2).
BUILTIN_SITE_NAMES = frozenset({
    "scrapling_fetch", "scrapling_spider", "scrapecraft_pattern", "sherlock_probe",
    "target_site", "arxiv", "semantic_scholar", "github", "paperswithcode",
    "google_scholar", "openalex", "crossref", "pubmed",
})

# Keys that would hold a secret. Credentials must come from the environment via auth_env.
FORBIDDEN_SECRET_KEYS = frozenset({"api_key", "apikey", "token", "secret", "password", "auth_token", "auth_value"})

ALLOWED_KEYS = frozenset({
    "name", "channel", "weight", "cache_ttl", "timeout_seconds", "max_results",
    "use_cases", "notes", "base_url", "query_param", "results_path", "auth_env",
})


@dataclass(frozen=True)
class SiteDef:
    name: str
    use_cases: tuple[str, ...]
    weight: float
    cache_ttl: float
    timeout_seconds: float
    max_results: int
    status: str
    reason: str = ""
    base_url: str | None = None
    channel: str | None = None
    query_param: str = "q"
    results_path: str | None = None
    auth_env: str | None = None
    notes: str = ""


@dataclass
class SiteCatalog:
    sites: list[SiteDef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)  # template names like "<your-site>"

    def get(self, name: str) -> SiteDef | None:
        return next((s for s in self.sites if s.name == name), None)

    def for_use_case(self, use_case: str) -> list[SiteDef]:
        """Sites bound to ``use_case`` that are usable (ready or builtin)."""
        return [
            s for s in self.sites
            if use_case in s.use_cases and s.status in ("ready", "builtin")
        ]


def _is_public_host(host: str) -> bool:
    """Reject IP-literal hosts in blocked ranges. Hostnames pass here; DNS is checked at fetch."""
    try:
        _check_address(host)
        return True
    except (UnsafeURLError, ValueError):
        try:
            ipaddress.ip_address(host)
            return False  # an IP literal that failed the check
        except ValueError:
            return True  # a hostname


def _validate_base_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        return "base_url must use https"
    if not parsed.hostname:
        return "base_url has no host"
    if parsed.username or parsed.password:
        return "base_url must not embed credentials; use auth_env"
    if not _is_public_host(parsed.hostname):
        return f"base_url host {parsed.hostname} is a non-public address"
    return None


def _coerce_number(entry: dict, key: str, default: float, lo: float, hi: float) -> float:
    raw = entry.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{key} must be a number")
    if not lo <= raw <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}, got {raw}")
    return float(raw)


def _parse_entry(entry: Any, group: str, defaults: dict, seen: set[str]) -> SiteDef:
    if not isinstance(entry, dict):
        raise ValueError(f"entry under '{group}' must be a mapping")

    for key in entry:
        if key.lower() in FORBIDDEN_SECRET_KEYS:
            raise ValueError(f"'{key}' is not allowed; put credentials in an env var and set auth_env")
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unknown key '{key}'")

    name = entry.get("name")
    if not isinstance(name, str) or not SITE_NAME_RE.match(name):
        raise ValueError(f"invalid name {name!r} (lowercase letters, digits, '-', '_'; 2-64 chars)")
    if name in seen:
        raise ValueError(f"duplicate site name '{name}'")

    raw_cases = entry.get("use_cases", [group])
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("use_cases must be a non-empty list")
    bad = [c for c in raw_cases if c not in KNOWN_USE_CASES]
    if bad:
        raise ValueError(f"unknown use_case(s) {bad}; known: {sorted(KNOWN_USE_CASES)}")

    weight = _coerce_number(entry, "weight", defaults["weight"], 0.01, 3.0)
    cache_ttl = _coerce_number(entry, "cache_ttl", defaults["cache_ttl"], 0, 86400)
    timeout = _coerce_number(entry, "timeout_seconds", defaults["timeout_seconds"], 1, 120)
    max_results = int(_coerce_number(entry, "max_results", defaults["max_results"], 1, 50))

    auth_env = entry.get("auth_env")
    if auth_env is not None and (not isinstance(auth_env, str) or not AUTH_ENV_RE.match(auth_env)):
        raise ValueError("auth_env must be an environment variable name such as NEXUS_MY_KEY")

    base_url = entry.get("base_url")
    channel = entry.get("channel")
    notes = str(entry.get("notes", "")).strip()
    query_param = str(entry.get("query_param", "q"))
    results_path = entry.get("results_path")

    if base_url is not None:
        if not isinstance(base_url, str):
            raise ValueError("base_url must be a string")
        err = _validate_base_url(base_url)
        if err:
            raise ValueError(err)
        status, reason = "ready", ""
    elif channel is not None:
        if not isinstance(channel, str) or ".." in channel or channel.startswith("/"):
            raise ValueError("channel must be a relative path under server/context_harness/")
        if (CHANNEL_ROOT / channel).is_file():
            status, reason = "ready", ""
        else:
            status, reason = "unconfigured", f"channel file not found: {channel}"
    elif name in BUILTIN_SITE_NAMES:
        status, reason = "builtin", "registered in code"
    else:
        status, reason = "unconfigured", "placeholder: set base_url or channel"

    return SiteDef(
        name=name,
        use_cases=tuple(raw_cases),
        weight=weight,
        cache_ttl=cache_ttl,
        timeout_seconds=timeout,
        max_results=max_results,
        status=status,
        reason=reason,
        base_url=base_url,
        channel=channel,
        query_param=query_param,
        results_path=str(results_path) if results_path else None,
        auth_env=auth_env,
        notes=notes,
    )


def parse_catalog(data: dict | None) -> SiteCatalog:
    """Build a catalog from already-loaded YAML data. Never raises on bad entries."""
    catalog = SiteCatalog()
    if not data:
        return catalog
    if not isinstance(data, dict):
        catalog.errors.append("top level must be a mapping")
        return catalog

    defaults = {"weight": 1.0, "cache_ttl": 300, "timeout_seconds": 20, "max_results": 10}
    defaults.update({k: v for k, v in (data.get("defaults") or {}).items() if k in defaults})

    groups = data.get("sites") or {}
    if not isinstance(groups, dict):
        catalog.errors.append("'sites' must map use-case names to lists of sites")
        return catalog

    seen: set[str] = set()
    for group, entries in groups.items():
        if group not in KNOWN_USE_CASES:
            catalog.errors.append(f"group '{group}': unknown use case")
            continue
        if entries is None:
            continue
        if not isinstance(entries, list):
            catalog.errors.append(f"group '{group}': must be a list")
            continue
        for idx, entry in enumerate(entries):
            label = f"{group}[{idx}]"
            if isinstance(entry, dict) and _is_placeholder(entry.get("name")):
                catalog.placeholders.append(str(entry["name"]))
                continue
            # A bare builtin reference repeated in another group means "also bind to this use case".
            if _is_builtin_ref(entry) and isinstance(entry, dict) and entry["name"] in seen:
                existing = next(s for s in catalog.sites if s.name == entry["name"])
                if existing.status == "builtin":
                    cases = tuple(dict.fromkeys(existing.use_cases + (group,)))
                    catalog.sites[catalog.sites.index(existing)] = replace(existing, use_cases=cases)
                    continue
            try:
                site = _parse_entry(entry, group, defaults, seen)
            except ValueError as exc:
                catalog.errors.append(f"{label}: {exc}")
                continue
            seen.add(site.name)
            catalog.sites.append(site)
    return catalog


def _is_placeholder(name: object) -> bool:
    return isinstance(name, str) and name.startswith("<") and name.endswith(">")


def _is_builtin_ref(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and entry["name"] in BUILTIN_SITE_NAMES
        and "base_url" not in entry
        and "channel" not in entry
    )


def load_catalog(path: str | Path | None = None) -> SiteCatalog:
    """Load and validate one harness sites file. A missing file yields an empty catalog."""
    target = Path(path) if path else DEFAULT_PATH
    if not target.exists():
        return SiteCatalog(errors=[f"{target} not found"])
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return SiteCatalog(errors=[f"YAML parse error: {exc}"])
    return parse_catalog(data)


def merge_catalogs(*catalogs: SiteCatalog) -> SiteCatalog:
    """Combine catalogs. A name defined in two files is an error; the later copy is dropped."""
    merged = SiteCatalog()
    names: set[str] = set()
    for cat in catalogs:
        merged.errors.extend(cat.errors)
        merged.placeholders.extend(cat.placeholders)
        for site in cat.sites:
            if site.name in names:
                merged.errors.append(f"duplicate site name '{site.name}' across config files")
                continue
            names.add(site.name)
            merged.sites.append(site)
    return merged


def load_merged_catalog(
    main_path: str | Path | None = None,
    local_path: str | Path | None = None,
) -> SiteCatalog:
    """Hand-edited ``harness_sites.yaml`` plus UI-managed ``harness_sites.local.yaml``.

    A missing local file is normal (no UI-added sites yet) and is not reported as an error.
    """
    local = Path(local_path) if local_path else LOCAL_PATH
    main_cat = load_catalog(main_path)
    if not local.exists():
        return main_cat
    return merge_catalogs(main_cat, load_catalog(local))
