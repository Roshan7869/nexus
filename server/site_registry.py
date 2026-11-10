"""Site registry loader — Harvard, SSRN, and per-project research URLs.

Phase 3: single source of truth at ``config/site_registry.yaml`` for the
channel dispatcher, target-site harvester, and ephemeral session broker.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "site_registry.yaml"


@lru_cache(maxsize=1)
def load_site_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache the site registry YAML."""
    p = Path(path) if path else _REGISTRY_PATH
    if not p.exists():
        logger.warning("site registry missing: %s", p)
        return {"version": 0, "publishers": {}, "projects": {}, "domain_rules": {}}
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("publishers", {})
    data.setdefault("projects", {})
    data.setdefault("domain_rules", {})
    return data


def reload_site_registry() -> dict[str, Any]:
    load_site_registry.cache_clear()
    return load_site_registry()


def list_publishers() -> list[dict[str, Any]]:
    reg = load_site_registry()
    out: list[dict[str, Any]] = []
    for key, spec in reg.get("publishers", {}).items():
        if not isinstance(spec, dict):
            continue
        out.append({
            "id": key,
            "label": spec.get("label", key),
            "channel": spec.get("channel", ""),
            "keywords": spec.get("keywords", []),
            "priority": spec.get("priority", 0.8),
        })
    return sorted(out, key=lambda x: -x["priority"])


def list_projects() -> list[dict[str, Any]]:
    reg = load_site_registry()
    out: list[dict[str, Any]] = []
    for key, spec in reg.get("projects", {}).items():
        if not isinstance(spec, dict):
            continue
        sites = spec.get("sites") or []
        out.append({
            "id": key,
            "label": spec.get("label", key),
            "site_count": len(sites),
            "keywords": spec.get("keywords", []),
            "urls": [s.get("url") for s in sites if isinstance(s, dict) and s.get("url")],
        })
    return out


def get_project_spec(project: str) -> dict[str, Any]:
    reg = load_site_registry()
    key = (project or "general").strip().lower()
    projects = reg.get("projects", {})
    spec = projects.get(key)
    if spec:
        return dict(spec)
    return dict(projects.get("general", {}))


def get_project_urls(project: str, *, include_local: bool = False) -> list[str]:
    spec = get_project_spec(project)
    urls: list[str] = []
    for entry in spec.get("sites") or []:
        if isinstance(entry, dict) and entry.get("url"):
            urls.append(str(entry["url"]))
    if include_local:
        for path in spec.get("local_paths") or []:
            p = Path(str(path))
            if p.exists():
                urls.append(p.as_uri())
    return urls


def get_task_sites(project: str, task_type: str,
                   registry: dict[str, Any] | None = None) -> list[str]:
    """URLs for one task of one project (P1.1).

    Uses ``projects.<project>.tasks.<task_type>.sites`` when present and
    non-empty; otherwise falls back to the project-wide ``sites``.
    Unknown project → []. Never raises on malformed entries.
    """
    reg = registry if registry is not None else load_site_registry()
    projects = reg.get("projects") or {}
    key = (project or "").strip().lower()
    spec = projects.get(key) if isinstance(projects, dict) else None
    if not isinstance(spec, dict):
        return []

    def _urls(entries: Any) -> list[str]:
        if not isinstance(entries, list):
            return []
        return [str(e["url"]) for e in entries if isinstance(e, dict) and e.get("url")]

    tasks = spec.get("tasks") or {}
    task = tasks.get((task_type or "").strip().lower()) if isinstance(tasks, dict) else None
    if isinstance(task, dict):
        urls = _urls(task.get("sites"))
        if urls:
            return urls
    return _urls(spec.get("sites"))


def get_domain_rules() -> dict[str, dict]:
    reg = load_site_registry()
    rules = reg.get("domain_rules") or {}
    return {str(k): dict(v) if isinstance(v, dict) else {"type": "generic"} for k, v in rules.items()}


def match_publishers(query: str) -> list[str]:
    """Return publisher ids whose keywords appear in *query*."""
    q = (query or "").lower()
    matched: list[tuple[float, str]] = []
    reg = load_site_registry()
    for pub_id, spec in reg.get("publishers", {}).items():
        if not isinstance(spec, dict):
            continue
        keywords = spec.get("keywords") or []
        if any(kw.lower() in q for kw in keywords):
            matched.append((float(spec.get("priority", 0.8)), pub_id))
    matched.sort(key=lambda x: -x[0])
    return [pid for _, pid in matched]


def publisher_channel_spec(pub_id: str) -> dict[str, Any] | None:
    reg = load_site_registry()
    spec = reg.get("publishers", {}).get(pub_id)
    if not isinstance(spec, dict):
        return None
    return {"id": pub_id, **spec}


def project_matches_query(project: str, query: str) -> bool:
    spec = get_project_spec(project)
    keywords = spec.get("keywords") or []
    if not keywords:
        return False
    q = (query or "").lower()
    return any(kw.lower() in q for kw in keywords) or project.lower() in q


def research_context_for_project(project: str, query: str = "") -> dict[str, Any]:
    """Summarize registry entries relevant to a project + query."""
    spec = get_project_spec(project)
    urls = get_project_urls(project)
    publishers = match_publishers(query) if query else []
    return {
        "project": project,
        "label": spec.get("label", project),
        "sites": spec.get("sites") or [],
        "urls": urls,
        "local_paths": [p for p in (spec.get("local_paths") or []) if Path(str(p)).exists()],
        "matched_publishers": publishers,
    }


def compress_harvest_results(results: list[dict], *, max_chars: int = 4000) -> str:
    """Build an LLM-ready context block from target-site harvest results."""
    parts: list[str] = ["# Project doc harvest"]
    used = 0
    for row in results:
        title = row.get("title") or row.get("url") or "page"
        text = (row.get("text_content") or row.get("abstract") or "")[:1200]
        block = f"\n## {title}\nURL: {row.get('url', '')}\n{text}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "".join(parts)[:max_chars]


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len] or "paper"