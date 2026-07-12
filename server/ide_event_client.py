"""ide_event_client.py — post IDE feedback events to the NEXUS resource brain.

Gap 4: primary target is the HTTP execution plane on :8080
(``POST /v1/event``), not the optional LLM gateway on :8000.

Example::

    from server.ide_event_client import post_event

    post_event(
        ide="claude-code",
        model="sonnet",
        provider="anthropic",
        task_type="coding",
        event_type="code_accepted",
        task_description="implement user auth",
        metadata={"skill_name": "tdd-workflow", "project": "nexus"},
    )

If the daemon is down, returns False without raising — IDE UX must not fail
because NEXUS is unreachable.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Gap 4: resource brain daemon (systemd nexus-daemon) — not LLM gateway :8000
DEFAULT_DAEMON_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 2.0


def _event_urls() -> list[str]:
    """Ordered endpoints to try (daemon first, optional gateway fallback)."""
    daemon = os.environ.get("NEXUS_DAEMON_URL") or os.environ.get(
        "NEXUS_HTTP_URL", DEFAULT_DAEMON_URL
    )
    urls = [
        daemon.rstrip("/") + "/v1/event",
        daemon.rstrip("/") + "/nexus/event",
    ]
    gateway = os.environ.get("NEXUS_GATEWAY_URL")
    if gateway:
        urls.append(gateway.rstrip("/") + "/nexus/event")
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def post_event(
    event_type: str,
    model: str = "unknown",
    provider: str = "unknown",
    task_type: str = "general",
    task_description: str = "",
    reward: Optional[float] = None,
    confidence: float = 1.0,
    metadata: Optional[dict] = None,
    ide: str = "opencode",
    project: str = "default",
    skill_name: str = "",
) -> bool:
    """Post an IDE feedback event to NEXUS. Returns True if accepted."""
    timeout = float(os.environ.get("NEXUS_EVENT_TIMEOUT", DEFAULT_TIMEOUT))
    meta: dict[str, Any] = dict(metadata or {})
    if project and "project" not in meta:
        meta["project"] = project
    if skill_name and "skill_name" not in meta:
        meta["skill_name"] = skill_name
    if task_description and "query" not in meta:
        meta["query"] = task_description[:500]
        meta["task_summary"] = task_description[:500]

    payload = {
        "event_type": event_type,
        "model": model,
        "provider": provider,
        "task_type": task_type,
        "task_description": (task_description or "")[:500],
        "confidence": confidence,
        "metadata": meta,
        "ide": ide,
        "project": project,
    }
    if reward is not None:
        payload["reward"] = reward

    data = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for url in _event_urls():
        try:
            req = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.URLError as e:
            last_err = e
            logger.debug("ide_event_client: %s unreachable: %s", url, e)
        except Exception as e:
            last_err = e
            logger.debug("ide_event_client: post to %s failed: %s", url, e)
    if last_err:
        logger.debug("ide_event_client: all endpoints failed: %s", last_err)
    return False


def code_accepted(model: str = "unknown", provider: str = "unknown", task_type: str = "coding", **kwargs) -> bool:
    return post_event("code_accepted", model, provider, task_type, **kwargs)


def code_rejected(model: str = "unknown", provider: str = "unknown", task_type: str = "coding", **kwargs) -> bool:
    return post_event("code_rejected", model, provider, task_type, **kwargs)


def test_passed(model: str = "unknown", provider: str = "unknown", task_type: str = "coding", **kwargs) -> bool:
    return post_event("test_passed", model, provider, task_type, **kwargs)


def test_failed(model: str = "unknown", provider: str = "unknown", task_type: str = "coding", **kwargs) -> bool:
    return post_event("test_failed", model, provider, task_type, **kwargs)


def task_completed(model: str = "unknown", provider: str = "unknown", task_type: str = "general", **kwargs) -> bool:
    return post_event("task_completed", model, provider, task_type, **kwargs)
