"""Write path for UI-managed harness sites (plan W4).

Sites added from the dashboard are stored in ``config/harness_sites.local.yaml``. The
hand-edited ``config/harness_sites.yaml`` is never rewritten, so its comments survive.
Every write is validated against the full merged catalog before it touches disk, and the
file is replaced atomically.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from server.harness_sites import (
    DEFAULT_PATH,
    LOCAL_PATH,
    KNOWN_USE_CASES,
    SiteDef,
    SiteCatalog,
    load_merged_catalog,
    parse_catalog,
)

_LOCK = threading.Lock()
_ALLOWED_INPUT = frozenset({
    "name", "base_url", "weight", "cache_ttl", "timeout_seconds", "max_results",
    "query_param", "results_path", "auth_env", "notes",
})


class StoreError(ValueError):
    """Invalid input or a write that would produce an invalid catalog."""


def _read_local(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sites": {}}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise StoreError(f"{path.name} is not a mapping")
    data.setdefault("sites", {})
    if data["sites"] is None:
        data["sites"] = {}
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".harness_sites_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def add_site(use_case: str, entry: dict[str, Any],
             main_path: Path | None = None, local_path: Path | None = None) -> SiteDef:
    """Validate and append one site under ``use_case``. Returns the stored SiteDef."""
    main = Path(main_path) if main_path else DEFAULT_PATH
    local = Path(local_path) if local_path else LOCAL_PATH

    if use_case not in KNOWN_USE_CASES:
        raise StoreError(f"unknown use_case '{use_case}'")
    if not isinstance(entry, dict):
        raise StoreError("site must be an object")
    extra = set(entry) - _ALLOWED_INPUT
    if extra:
        raise StoreError(f"unsupported fields: {sorted(extra)}")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise StoreError("name is required")

    clean = {k: v for k, v in entry.items() if v not in (None, "")}
    clean["use_cases"] = [use_case]

    with _LOCK:
        data = _read_local(local)
        group = data["sites"].setdefault(use_case, []) or []
        data["sites"][use_case] = group
        group.append(clean)

        # Validate the whole candidate against the full catalog before writing.
        candidate_cat = parse_catalog({"sites": {use_case: [clean]}})
        if candidate_cat.errors:
            raise StoreError("; ".join(candidate_cat.errors))
        merged_before = load_merged_catalog(main, local)
        if name in {s.name for s in merged_before.sites}:
            raise StoreError(f"'{name}' already exists")

        _atomic_write(local, data)

    stored = load_merged_catalog(main, local).get(name)
    if stored is None:
        raise StoreError(f"'{name}' was written but did not validate on reload")
    return stored


def remove_site(name: str, local_path: Path | None = None) -> bool:
    """Remove a UI-added site. Sites in the hand-edited file are not removable here."""
    local = Path(local_path) if local_path else LOCAL_PATH
    with _LOCK:
        data = _read_local(local)
        removed = False
        for use_case, group in list(data["sites"].items()):
            if not group:
                continue
            kept = [e for e in group if not (isinstance(e, dict) and e.get("name") == name)]
            if len(kept) != len(group):
                removed = True
                data["sites"][use_case] = kept
        if removed:
            _atomic_write(local, data)
    return removed


def catalog_view(main_path: Path | None = None, local_path: Path | None = None) -> dict[str, Any]:
    """JSON-friendly view of the merged catalog for the UI."""
    cat: SiteCatalog = load_merged_catalog(main_path or DEFAULT_PATH, local_path or LOCAL_PATH)
    local_names = {e.get("name") for g in _read_local(local_path or LOCAL_PATH)["sites"].values()
                   for e in (g or []) if isinstance(e, dict)}
    return {
        "sites": [
            {
                "name": s.name,
                "use_cases": list(s.use_cases),
                "status": s.status,
                "reason": s.reason,
                "weight": s.weight,
                "cache_ttl": s.cache_ttl,
                "timeout_seconds": s.timeout_seconds,
                "max_results": s.max_results,
                "base_url": s.base_url,
                "auth_env": s.auth_env,
                "auth_env_set": bool(s.auth_env) and bool(os.environ.get(s.auth_env)),
                "notes": s.notes,
                "removable": s.name in local_names,
            }
            for s in cat.sites
        ],
        "errors": cat.errors,
        "placeholders": cat.placeholders,
    }
