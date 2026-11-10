"""Scrapecraft Pattern Channel — Phase 3.

CSS selector pattern learning and semantic extraction.
- learn_pattern: from 2-3 (field, value) examples, learns CSS selectors
- apply_pattern: applies learned selectors to new URL
- semantic_extract: LLM-based extraction fallback (OpenRouter via Ollama)
- unified_extract: pattern-first, semantic-fallback, auto-learn

Patterns stored in NEXUS SQLite.
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DB_PATH = _resolve_db_path()
_SHARED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS harness_patterns (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            field_name TEXT NOT NULL,
            selector TEXT NOT NULL,
            example_value TEXT,
            success_count INTEGER DEFAULT 1,
            created_at REAL,
            updated_at REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS harness_domain_knowledge (
            domain TEXT PRIMARY KEY,
            common_fields TEXT,
            extraction_rules TEXT,
            updated_at REAL
        )"""
    )
    conn.commit()
    return conn


class PatternLearner:
    """Lightweight CSS selector learner — no Redis, no SQLAlchemy.

    Learns selectors by analyzing DOM structure from example (field, value) pairs.
    """

    def __init__(self) -> None:
        self._db = _get_db()

    async def learn(
        self, url: str, examples: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Learn selectors from 2-3 (field, value) examples.

        Args:
            url: page URL where examples were found
            examples: [{"field": "title", "value": "..."}, ...]

        Returns:
            {field: selector, ...} mapping
        """
        domain = urlparse(url).netloc
        async with httpx.AsyncClient(
            headers=_SHARED_HEADERS, timeout=15, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            html = resp.text

        learned: dict[str, str] = {}

        for ex in examples:
            field = ex.get("field", "")
            value = ex.get("value", "")
            if not field or not value:
                continue

            selector = self._find_selector(html, value)
            if selector:
                learned[field] = selector
                self._store_pattern(domain, field, selector, value)

        return {
            "domain": domain,
            "fields_learned": len(learned),
            "selectors": learned,
        }

    def _find_selector(self, html: str, value: str) -> str | None:
        """Find a CSS selector that matches an element containing the target value."""
        escaped = re.escape(value)

        id_match = re.search(
            r'<[^>]*\s+id=["\']([^"\']+)["\'][^>]*>[^<]*'
            + escaped
            + r'[^<]*</',
            html,
            re.IGNORECASE,
        )
        if id_match:
            return f"#{id_match.group(1)}"

        class_match = re.search(
            r'<([a-z0-9]+)[^>]*\s+class=["\']([^"\']*'
            + re.escape(value[:10])
            + r'[^"\']*)["\'][^>]*>',
            html,
            re.IGNORECASE,
        )
        if class_match:
            tag = class_match.group(1)
            cls = class_match.group(2).split()[0]
            return f"{tag}.{cls}"

        tag_match = re.search(
            r'<([a-z0-9]+)[^>]*>([^<]{0,50}'
            + re.escape(value[:20])
            + r'[^<]{0,50})</\1>',
            html,
            re.IGNORECASE,
        )
        if tag_match:
            return tag_match.group(1)

        return None

    def _store_pattern(
        self, domain: str, field: str, selector: str, example: str
    ) -> None:
        pid = hashlib.sha256(f"{domain}:{field}".encode()).hexdigest()[:16]
        now = time.time()
        self._db.execute(
            """INSERT INTO harness_patterns (id, domain, field_name, selector, example_value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   selector = excluded.selector,
                   success_count = success_count + 1,
                   updated_at = excluded.updated_at""",
            (pid, domain, field, selector, example, now, now),
        )
        self._db.commit()

    async def apply(
        self, url: str, fields: list[str] | None = None
    ) -> dict[str, Any]:
        """Apply learned selectors to extract data from a URL.

        Args:
            url: target URL
            fields: specific fields to extract (None = all known)

        Returns:
            {url, domain, extracted: {field: value, ...}, fields_requested, fields_found}
        """
        domain = urlparse(url).netloc
        rows = self._db.execute(
            "SELECT field_name, selector FROM harness_patterns WHERE domain = ?",
            (domain,),
        ).fetchall()

        if not rows:
            return {
                "url": url,
                "domain": domain,
                "extracted": {},
                "fields_requested": len(fields or []),
                "fields_found": 0,
            }

        try:
            async with httpx.AsyncClient(
                headers=_SHARED_HEADERS, timeout=15, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                html = resp.text
        except Exception as e:
            return {
                "url": url,
                "domain": domain,
                "error": str(e),
                "extracted": {},
                "fields_found": 0,
            }

        extracted: dict[str, str] = {}
        for field_name, selector in rows:
            if fields and field_name not in fields:
                continue

            if selector.startswith("#"):
                m = re.search(
                    r'<[^>]*\s+id=["\']'
                    + re.escape(selector[1:])
                    + r'["\'][^>]*>([^<]+)',
                    html,
                    re.IGNORECASE,
                )
            elif "." in selector:
                tag, cls = selector.split(".", 1)
                m = re.search(
                    r'<'
                    + tag
                    + r'[^>]*\s+class=["\'][^"\']*'
                    + re.escape(cls)
                    + r'[^"\']*["\'][^>]*>([^<]+)',
                    html,
                    re.IGNORECASE,
                )
            else:
                m = re.search(
                    r'<' + selector + r'[^>]*>([^<]+)',
                    html,
                    re.IGNORECASE,
                )

            if m:
                extracted[field_name] = m.group(1).strip()

        return {
            "url": url,
            "domain": domain,
            "extracted": extracted,
            "fields_requested": len(fields or []),
            "fields_found": len(extracted),
        }

    def get_known_domains(self) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT domain FROM harness_patterns ORDER BY domain"
        ).fetchall()
        return [r[0] for r in rows]


def _load_ollama_key() -> str:
    for src in (
        os.environ.get("OLLAMA_API_KEY"),
        _read_bashrc_var("OLLAMA_API_KEY"),
        _read_bashrc_var("OPENAI_API_KEY"),
    ):
        if src and src != "local":
            return src
    return ""


def _read_bashrc_var(name: str) -> str:
    try:
        bashrc = Path.home() / ".bashrc"
        if not bashrc.exists():
            return ""
        for line in bashrc.read_text().splitlines():
            if line.startswith(f"export {name}="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return ""


class SemanticExtractor:
    """LLM-based extraction via Ollama Cloud API (OpenAI-compatible endpoint).

    Fallback when pattern learning hasn't occurred yet.
    """

    def __init__(self) -> None:
        self._ollama_key = _load_ollama_key()

    async def extract(
        self, url: str, fields: list[str], html: str | None = None
    ) -> dict[str, Any]:
        if not html:
            async with httpx.AsyncClient(
                headers=_SHARED_HEADERS, timeout=15, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                html = resp.text

        snippet = html[:8000]

        prompt = (
            f"Extract the following fields from this HTML page at {url}.\n"
            f"Fields to extract: {', '.join(fields)}\n\n"
            f"Return ONLY a JSON object with these fields and their values.\n"
            f"If a field is not found, set it to null.\n\n"
            f"HTML:\n{snippet}"
        )

        try:
            headers = {"Content-Type": "application/json"}
            if self._ollama_key:
                headers["Authorization"] = f"Bearer {self._ollama_key}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "model": "deepseek-v4-flash:cloud",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You extract structured data from HTML. Return only JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.1,
                }
                resp = await client.post(
                    "http://localhost:11434/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "{}")
                )

                json_match = re.search(r"\{.*\}", content, re.DOTALL)
                if json_match:
                    extracted = json.loads(json_match.group())
                else:
                    extracted = {"_raw": content}

                return {
                    "url": url,
                    "fields_requested": fields,
                    "extracted": extracted,
                    "method": "semantic",
                }

        except Exception as e:
            return {
                "url": url,
                "fields_requested": fields,
                "error": str(e),
                "method": "semantic_failed",
            }


class ScrapecraftPatternChannel:
    """Unified pattern extraction — pattern-first, semantic-fallback."""

    def __init__(self) -> None:
        self._learner = PatternLearner()
        self._semantic = SemanticExtractor()

    async def extract(
        self, url: str, fields: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Unified extraction: pattern first, semantic fallback.

        Args:
            url: target URL
            fields: {field: example_value} for guided extraction, or list of field names

        Returns:
            extraction result
        """
        field_names = list(fields.keys()) if fields else []

        pattern_result = await self._learner.apply(url, field_names)
        extracted = pattern_result.get("extracted", {})
        fields_found = len(extracted)
        fields_requested = pattern_result.get("fields_requested", 0)

        if fields_requested > 0 and fields_found >= fields_requested:
            return {**pattern_result, "method": "pattern"}

        if fields:
            learn_result = await self._learner.learn(
                url,
                [{"field": k, "value": v} for k, v in fields.items()],
            )
            if learn_result.get("fields_learned", 0) > 0:
                pattern_result = await self._learner.apply(url, field_names)
                extracted = pattern_result.get("extracted", {})
                if extracted:
                    return {
                        **pattern_result,
                        "method": "pattern_with_learning",
                        "learned": learn_result,
                    }

        semantic_result = await self._semantic.extract(url, field_names)
        semantic_extracted = semantic_result.get("extracted", {})

        if semantic_extracted and not semantic_result.get("error"):
            try:
                await self._learner.learn(
                    url,
                    [
                        {"field": k, "value": str(v)}
                        for k, v in semantic_extracted.items()
                        if v
                    ],
                )
            except Exception as e:
                logger.warning("Auto-learn from semantic extract failed: %s", e)

        return {
            "url": url,
            "extracted": {**extracted, **semantic_extracted},
            "fields_requested": fields_requested or len(field_names),
            "fields_found": len(extracted) + len(semantic_extracted),
            "method": "semantic" if not extracted else "pattern+semantic",
            "pattern_result": pattern_result if not extracted else None,
        }


_channel: ScrapecraftPatternChannel | None = None


def get_channel() -> ScrapecraftPatternChannel:
    global _channel
    if _channel is None:
        _channel = ScrapecraftPatternChannel()
    return _channel
