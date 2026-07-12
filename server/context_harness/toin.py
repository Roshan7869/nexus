"""Tool Output Integration Node — format tool outputs into uniform cache entries.

P5-ST1 (TOIN) of the NEXUS Context Harness Engine.

Handles ingestion, volatile normalisation, tag extraction, formatting,
batching, merging, and cache-level routing for all tool output types
(MCP, skill, CLI, API, agent, file).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, NamedTuple

from .compression import VolatileContentDetector, estimate_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ToolOutputSource(NamedTuple):
    source_type: str  # 'mcp' | 'skill' | 'cli' | 'api' | 'agent' | 'file'
    source_name: str  # e.g. 'agent_reach', 'filesystem', 'fetch_documentation'
    invocation_id: str  # UUID
    started_at: float  # timestamp
    duration_ms: float


class ToolOutput(NamedTuple):
    source: ToolOutputSource
    raw_text: str
    structured: dict | list | None
    mime_type: str  # 'text/plain' | 'application/json' | 'text/markdown'
    token_count: int
    status: str  # 'success' | 'error' | 'timeout' | 'partial'
    error_message: str | None


class FormattedEntry(NamedTuple):
    entry_id: str  # UUID
    tool_output: ToolOutput
    normalized_text: str
    normalized_tokens: int
    compression_savings: int
    section_title: str  # auto-generated: "{source_name} output"
    priority: int  # 1-100
    tags: list[str]


# ---------------------------------------------------------------------------
# Regex patterns for tag extraction
# ---------------------------------------------------------------------------

_TAG_MENTION = re.compile(r"@(\w+)")
_TAG_HASHTAG = re.compile(r"#(\w+)")
_TAG_BRACKET = re.compile(r"\[([a-zA-Z0-9_\- ]+)\]")
_TAG_PREFIX = re.compile(r"^(ERROR|WARN|INFO|DEBUG|TRACE)\b", re.MULTILINE)

# Volatile content detector singleton
_detector = VolatileContentDetector()


# ---------------------------------------------------------------------------
# TOIN — Tool Output Integration Node
# ---------------------------------------------------------------------------


class TOIN:
    """Integrate tool outputs into cache-friendly formatted entries.

    Usage::

        toin = TOIN()
        source = ToolOutputSource(
            source_type='skill',
            source_name='agent_reach',
            invocation_id=str(uuid.uuid4()),
            started_at=time.time(),
            duration_ms=1234.5,
        )
        entry = toin.ingest(source, raw_text, mime_type='text/markdown')
        cache_str = toin.format_for_cache(entry)
    """

    def __init__(self) -> None:
        self._detector = _detector

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(
        self,
        source: ToolOutputSource,
        raw_text: str,
        structured: dict | list | None = None,
        mime_type: str = "text/plain",
    ) -> FormattedEntry:
        """Ingest a raw tool output and return a normalised ``FormattedEntry``.

        Steps:
        1. Count tokens via ``estimate_tokens``.
        2. Detect status — 'error' if ``raw_text`` contains common error
           indicators, otherwise 'success'.
        3. Run volatile detection and produce normalised text.
        4. Auto-generate section title and tags.
        5. Assign priority (errors/small outputs get lower priority).
        """
        token_count = estimate_tokens(raw_text)

        status = self._detect_status(raw_text, source)
        error_message: str | None = None
        if status == "error":
            error_message = self._extract_error(raw_text)

        tags = self.extract_tags(raw_text)
        if status == "error":
            tags = list(set(tags + ["error"]))
        if "warning" in raw_text.lower():
            tags = list(set(tags + ["warning"]))
        if mime_type == "application/json":
            tags = list(set(tags + ["structured"]))
        if mime_type == "text/markdown":
            tags = list(set(tags + ["markdown"]))

        volatile_tags = self._detector.detect_all(raw_text)
        normalized_text = self._detector.normalize(raw_text, volatile_tags)
        normalized_tokens = estimate_tokens(normalized_text)
        compression_savings = token_count - normalized_tokens

        priority = self._compute_priority(status, token_count)

        section_title = f"{source.source_name} output"

        return FormattedEntry(
            entry_id=str(uuid.uuid4()),
            tool_output=ToolOutput(
                source=source,
                raw_text=raw_text,
                structured=structured,
                mime_type=mime_type,
                token_count=token_count,
                status=status,
                error_message=error_message,
            ),
            normalized_text=normalized_text,
            normalized_tokens=normalized_tokens,
            compression_savings=compression_savings,
            section_title=section_title,
            priority=priority,
            tags=tags,
        )

    def ingest_json(
        self,
        source_name: str,
        json_data: dict | list,
    ) -> FormattedEntry:
        """Convenience: ingest a JSON object as a ``FormattedEntry``.

        Automatically builds a ``ToolOutputSource`` with ``source_type='api'``,
        serialises *json_data* to formatted JSON, and sets
        ``mime_type='application/json'``.
        """
        raw_text = json.dumps(json_data, indent=2, default=str, ensure_ascii=False)
        started_at = time.time()
        source = ToolOutputSource(
            source_type="api",
            source_name=source_name,
            invocation_id=str(uuid.uuid4()),
            started_at=started_at,
            duration_ms=0.0,
        )
        return self.ingest(
            source=source,
            raw_text=raw_text,
            structured=json_data,
            mime_type="application/json",
        )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_for_cache(entry: FormattedEntry) -> str:
        """Format a ``FormattedEntry`` into a cache-friendly string block.

        For JSON mimetypes the normalised text is pretty-printed; for all
        other types the text is included as-is.
        """
        text = entry.normalized_text
        if entry.tool_output.mime_type == "application/json":
            try:
                parsed = json.loads(text)
                text = json.dumps(parsed, indent=2, default=str, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass

        return f"--- {entry.section_title} ---\n{text}"

    @staticmethod
    def batch_format(
        entries: list[FormattedEntry],
        max_tokens: int = 4096,
    ) -> list[str]:
        """Format a batch of entries, discarding oldest if token budget exceeded.

        Entries are truncated oldest-first (stable order) until the combined
        normalised token count fits within *max_tokens*.
        Returns a list of formatted strings, one per surviving entry.
        """
        budget = max_tokens
        surviving: list[FormattedEntry] = []
        for entry in entries:  # iterate in insertion order (oldest first)
            if budget <= 0:
                break
            if entry.normalized_tokens <= budget:
                surviving.append(entry)
                budget -= entry.normalized_tokens

        return [TOIN.format_for_cache(e) for e in surviving]

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    @staticmethod
    def merge_results(
        existing: FormattedEntry | None,
        new: FormattedEntry,
    ) -> FormattedEntry:
        """Merge two ``FormattedEntry`` values for the same source.

        Appends *new* normalised text to *existing*, updates token counts,
        and keeps the newer timestamp.  If *existing* is ``None`` returns
        *new* unchanged.
        """
        if existing is None:
            return new

        merged_text = existing.normalized_text + "\n" + new.normalized_text
        merged_tokens = estimate_tokens(merged_text)
        savings = existing.compression_savings + new.compression_savings

        merged_source = existing.tool_output.source._replace(
            started_at=max(
                existing.tool_output.source.started_at,
                new.tool_output.source.started_at,
            ),
            duration_ms=(
                existing.tool_output.source.duration_ms
                + new.tool_output.source.duration_ms
            ),
        )

        merged_tool_output = existing.tool_output._replace(
            source=merged_source,
        )

        merged_tags = list(
            set(existing.tags + new.tags),
        )

        return FormattedEntry(
            entry_id=str(uuid.uuid4()),
            tool_output=merged_tool_output,
            normalized_text=merged_text,
            normalized_tokens=merged_tokens,
            compression_savings=savings,
            section_title=existing.section_title,
            priority=max(existing.priority, new.priority),
            tags=merged_tags,
        )

    # ------------------------------------------------------------------
    # Tag extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_tags(
        text: str,
        existing_tags: list[str] | None = None,
    ) -> list[str]:
        """Extract tags from *text* and merge with *existing_tags*.

        Recognised patterns:
        - ``@mention``        → ``mention``
        - ``#hashtag``        → ``hashtag``
        - ``[bracket:content]`` → ``bracket:content``
        - ``ERROR/WARN/INFO`` line prefixes → ``error``, ``warn``, ``info``
        """
        tags: set[str] = set()

        for m in _TAG_MENTION.finditer(text):
            tags.add(m.group(1).lower())
        for m in _TAG_HASHTAG.finditer(text):
            tags.add(m.group(1).lower())
        for m in _TAG_BRACKET.finditer(text):
            tags.add(m.group(1).strip().lower())
        for m in _TAG_PREFIX.finditer(text):
            tags.add(m.group(1).lower())

        if existing_tags:
            tags.update(t.lower() for t in existing_tags)

        return sorted(tags)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_status(text: str, source: ToolOutputSource) -> str:
        """Heuristic status detection based on text content."""
        if source.source_type == "agent" and not text.strip():
            return "timeout"
        lower = text.lower()
        if any(
            kw in lower
            for kw in [
                "traceback",
                "exception:",
                "error:",
                "error occurred",
                "failed with",
                "exit code 1",
                "command not found",
            ]
        ):
            return "error"
        return "success"

    @staticmethod
    def _extract_error(text: str) -> str | None:
        """Extract the first meaningful error line."""
        lines = text.splitlines()
        for line in lines:
            stripped = line.strip()
            if any(
                kw in stripped
                for kw in ["Error:", "Exception:", "Traceback", "failed:"]
            ):
                return stripped[:500]
        return None

    @staticmethod
    def _compute_priority(status: str, token_count: int) -> int:
        """Assign 1-100 priority.

        Errors get lower priority (40) so they don't crowd out productive
        results.  Small outputs get slightly boosted (up to 85) because
        they are cheap to cache.
        """
        if status == "error":
            return 40
        if status == "timeout":
            return 20
        if status == "partial":
            return 55
        if token_count < 50:
            return 85
        if token_count < 200:
            return 75
        return 70


# ---------------------------------------------------------------------------
# OutputRouter — cache level routing
# ---------------------------------------------------------------------------


class OutputRouter:
    """Route tool outputs to the correct cache level and bundle.

    Levels:
        L0 — Eager-loaded core (high-priority results)
        L1 — Lazy-loaded per project type (normal results)
        L2 — Debug / diagnostics (errors)
        L3 — Per-task transient (everything else)
    """

    @staticmethod
    def route_to_level(entry: FormattedEntry) -> str:
        """Determine the appropriate cache level for *entry*.

        Priority-based routing (checked from highest to lowest):
        1. ``priority >= 80`` → ``'L0'``
        2. ``priority >= 50`` → ``'L1'``
        3. Status is ``'error'`` → ``'L2'`` (debugging)
        4. Everything else     → ``'L3'``
        """
        if entry.priority >= 80:
            return "L0"
        if entry.priority >= 50:
            return "L1"
        if entry.tool_output.status == "error":
            return "L2"
        return "L3"

    @staticmethod
    def route_to_bundle(
        entry: FormattedEntry,
        bundle_registry: dict[str, set[str]],
    ) -> str | None:
        """Match *entry* tags against *bundle_registry* bundle criteria.

        *bundle_registry* maps bundle names to sets of required tag keywords.
        The first bundle whose all keywords are present in ``entry.tags``
        wins.  Returns the bundle name or ``None``.
        """
        entry_tags = set(entry.tags)
        for bundle_name, required_keywords in bundle_registry.items():
            if required_keywords.issubset(entry_tags):
                return bundle_name
        return None

    @staticmethod
    def should_cache(entry: FormattedEntry) -> bool:
        """Decide whether *entry* should be written to cache.

        Rules:
        - Always cache success results.
        - Cache errors only if they contain actionable information
          (error message is present and non-trivial).
        - Never cache timeout outputs (they add noise without value).
        """
        if entry.tool_output.status == "success":
            return True
        if entry.tool_output.status == "error":
            msg = entry.tool_output.error_message
            return bool(msg and len(msg) > 10)
        return False


__all__ = [
    "ToolOutputSource",
    "ToolOutput",
    "FormattedEntry",
    "TOIN",
    "OutputRouter",
]
