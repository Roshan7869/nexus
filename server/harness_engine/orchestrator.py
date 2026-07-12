"""Research orchestrator-worker: channel workers → fused Artifact[] (Anthropic-shaped).

Never injects titles into the skill ranking list.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from server.harness_engine.models import Artifact, ChannelResult

logger = logging.getLogger(__name__)


def _item_to_artifact(item: dict, channel: str) -> Optional[Artifact]:
    title = (
        item.get("title")
        or item.get("name")
        or item.get("full_name")
        or ""
    ).strip()
    if not title:
        return None
    url = (
        item.get("url")
        or item.get("html_url")
        or item.get("link")
        or item.get("pdf_url")
        or ""
    )
    snippet = (
        item.get("snippet")
        or item.get("summary")
        or item.get("abstract")
        or item.get("description")
        or ""
    )
    if isinstance(snippet, str) and len(snippet) > 500:
        snippet = snippet[:497] + "..."
    score = float(
        item.get("fused_score")
        or item.get("score")
        or item.get("rrf_score")
        or 0.0
    )
    kind = "repo" if channel == "github" else "document"
    return Artifact(
        title=title,
        url=str(url or ""),
        snippet=str(snippet or ""),
        score=score,
        channel=channel,
        kind=kind,
        meta={k: item.get(k) for k in ("year", "authors", "venue", "doi") if item.get(k)},
    )


def run_research_orchestrator(
    query: str,
    *,
    channels: str = "arxiv,s2,github,pwc,scholar",
    max_results: int = 5,
    max_artifacts: int = 15,
) -> dict[str, Any]:
    """Lead + parallel channel workers via existing harness_research.

    Returns:
      {
        query, channels, artifacts: [Artifact...],
        per_channel: [...], latency_ms, source: 'harness_engine'
      }
    """
    t0 = time.perf_counter()
    try:
        from server.context_harness.server import harness_research
    except Exception as e:
        logger.warning("harness_research import failed: %s", e)
        return {
            "query": query,
            "channels": [],
            "artifacts": [],
            "per_channel": [],
            "error": f"import_failed: {e}",
            "latency_ms": 0.0,
            "source": "harness_engine",
        }

    try:
        raw = harness_research(query, channels=channels, max_results=max_results)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning("harness_research failed: %s", e)
        return {
            "query": query,
            "channels": channels.split(","),
            "artifacts": [],
            "per_channel": [],
            "error": str(e),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "source": "harness_engine",
        }

    artifacts: list[Artifact] = []
    channel_results: list[ChannelResult] = []

    for block in data.get("per_channel") or []:
        ch = block.get("channel") or "unknown"
        err = block.get("error")
        arts: list[Artifact] = []
        for item in block.get("results") or []:
            if not isinstance(item, dict):
                continue
            a = _item_to_artifact(item, ch)
            if a:
                arts.append(a)
        channel_results.append(
            ChannelResult(channel_id=ch, artifacts=arts, error=err)
        )

    # Prefer fused list if present (RRF), else flatten workers
    fused = data.get("fused") or []
    if fused:
        for item in fused:
            if not isinstance(item, dict):
                continue
            ch = item.get("channel") or item.get("source") or "fused"
            a = _item_to_artifact(item, str(ch))
            if a:
                artifacts.append(a)
    else:
        for cr in channel_results:
            artifacts.extend(cr.artifacts)

    # de-dupe by title
    seen: set[str] = set()
    unique: list[Artifact] = []
    for a in sorted(artifacts, key=lambda x: -x.score):
        key = a.title.lower().strip()[:200]
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
        if len(unique) >= max_artifacts:
            break

    return {
        "query": query,
        "channels": data.get("channels_searched") or channels.split(","),
        "artifacts": [a.to_dict() for a in unique],
        "per_channel": [cr.to_dict() for cr in channel_results],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "source": "harness_engine",
        "n_artifacts": len(unique),
    }


def browser_harness_status() -> dict[str, Any]:
    """Optional browser-harness channel adapter status (thin stub)."""
    import shutil
    from pathlib import Path

    candidates = [
        Path.home() / "browser-harness",
        Path.home() / "Downloads" / "browser-harness",
        Path("/home/roshan/browser-harness"),
    ]
    found = next((str(p) for p in candidates if p.is_dir()), None)
    return {
        "channel": "browser",
        "adapter": "browser-harness",
        "installed_path": found,
        "available": found is not None,
        "cdp_hint": "chrome://inspect/#remote-debugging",
        "note": (
            "Thin CDP harness pattern (agent-editable domain-skills). "
            "Install from github.com/Roshan7869/browser-harness when browsing."
            if not found
            else "Local checkout detected; load browser toolset to operate."
        ),
        "which_python": shutil.which("python3"),
    }
