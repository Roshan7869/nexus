"""P1.3 — harvest a bound site and register its undo (Cordis effect inverse).

Each bound site is one ``project_contexts`` row (context_type='docs'). The row is
written through ``LocalHarnessSession.effect()``, so closing the session deletes it.
A failed harvest registers no inverse and marks the site unsatisfied; the session
still opens (degraded) and ``refresh()`` does not tear it down.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SITE_TIMEOUT_S = 15.0
MAX_CONTENT = 8000


def _harvest(url: str, timeout: float = SITE_TIMEOUT_S) -> dict[str, Any]:
    """Fetch one page via the target-site channel. Never raises; errors are data.

    Runs in its own thread + event loop so it is safe to call from sync code
    that may already be inside a running loop (MCP/FastAPI handlers).
    """
    async def _go() -> dict[str, Any]:
        from server.context_harness.channels.target_site import TargetSiteChannel
        channel = TargetSiteChannel()
        try:
            return await asyncio.wait_for(channel.fetch_page(url), timeout)
        finally:
            client = getattr(channel, "client", None)
            if client is not None:
                try:
                    await client.aclose()
                except Exception:  # pragma: no cover - best effort
                    pass

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_go())).result(timeout=timeout + 5)
    except Exception as exc:
        return {"url": url, "status": 0, "error": f"{type(exc).__name__}: {exc}",
                "text_content": ""}


def _insert_row(project: str, session_id: str, url: str, title: str, text: str) -> int:
    from server.db import db_conn
    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO project_contexts
                (project, context_type, title, content, source_session_id, tags_json)
            VALUES (?, 'docs', ?, ?, ?, ?)
            """,
            (project, title[:200], text[:MAX_CONTENT], session_id,
             json.dumps(["site", url])),
        )
        return int(cur.lastrowid)


def _delete_row(row_id: int) -> None:
    from server.db import db_conn
    with db_conn() as conn:
        conn.execute("DELETE FROM project_contexts WHERE id = ?", (row_id,))


def bind_site(lh: Any, key: str, url: str, *, session_id: str) -> tuple[bool, str]:
    """Harvest ``url`` and register its row's undo on ``lh``. Returns (ok, status)."""
    res = _harvest(url)
    text = str(res.get("text_content") or "").strip()
    if res.get("error") or not text:
        return False, str(res.get("error") or "empty content")

    title = str(res.get("title") or url)
    project = getattr(lh, "project", "") or "default"
    holder: dict[str, int] = {}

    def _write() -> Any:
        holder["id"] = _insert_row(project, session_id, url, title, text)
        return lambda: _delete_row(holder["id"])   # inverse: LIFO on dispose

    lh.effect(_write)
    return True, "bound"


def bind_all(lh: Any, *, session_id: str) -> dict[str, str]:
    """Bind every site in ``lh.site_urls``. Failed sites are unsatisfied, not committed."""
    for key, url in lh.site_urls.items():
        ok, status = bind_site(lh, key, url, session_id=session_id)
        lh.site_status[url] = status
        lh._site_live[key] = ok
        if not ok:
            logger.info("site bind failed (%s): %s", url, status)
            lh._committed.pop(key, None)
            if key in lh.ctx.deps:
                lh.ctx.deps[key].satisfied = False
    return dict(lh.site_status)
