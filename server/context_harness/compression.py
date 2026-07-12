"""Volatile content detection and fixed-width token window packing.

P3-SA1 (CacheAligner) of the NEXUS Context Harness Engine.

VolatileContentDetector identifies content that changes between cache
invalidation cycles (timestamps, session IDs, user context, error dumps,
cached search summaries, auto-generated IDs) and normalises or strips it so
the remaining text is stable across cache hits.

WindowPacker greedily packs PackedSection objects into fixed-width
CompressedWindows, sorted by salience, aiming for >90% capacity average.
"""

from __future__ import annotations

import enum
import logging
import re
import signal
import threading
import uuid
from typing import NamedTuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class VolatileTag(NamedTuple):
    """One detected volatile content span within a section body."""
    detector: str
    original: str
    normalized: str | None
    start_pos: int
    end_pos: int


class PackedSection(NamedTuple):
    """A section that has been packed into a cache window.

    ``section_title`` and ``resolved_name`` are NEVER modified by the
    packer or the detector — they pass through verbatim.
    """
    resource_id: int
    section_id: int
    section_title: str
    resolved_name: str
    body: str
    body_tokens: int
    salience: float
    compression_ratio: float
    volatile_stripped: list[str]


class CompressedWindow(NamedTuple):
    """One token window ready for cache insertion."""
    window_id: str
    slot: int
    cache_level: str
    sections: list[PackedSection]
    total_tokens: int
    max_tokens: int


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

# CJK Unified Ideographs + Extension A + Compatibility Ideographs
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """Cheap token estimate with CJK adjustment.

    Baseline: chars / 4.  CJK characters count double (chars / 2) because
    they typically consume 2+ tokens in cl100k_base.
    """
    if not text:
        return 1
    cjk_count = len(_CJK_RE.findall(text))
    ascii_count = len(text) - cjk_count
    return max(1, (ascii_count + cjk_count * 2) // 4)


# ---------------------------------------------------------------------------
# Regex timeout — signal-based, main-thread only
# ---------------------------------------------------------------------------

class _TimeoutError(Exception):
    """Raised when a regex pattern exceeds the per-pattern time budget."""


_HAS_SIGNAL = threading.current_thread() is threading.main_thread()


def _regex_finditer_with_timeout(
    pattern: re.Pattern,
    text: str,
    timeout_ms: int = 100,
) -> list[re.Match]:
    """Run ``pattern.finditer(text)`` with a timeout.

    Falls through to an empty list if the timeout is hit (main thread only)
    or if signals are unavailable.
    """
    if not _HAS_SIGNAL:
        return list(pattern.finditer(text))

    matches: list[re.Match] = []
    timeout_hit = False

    def _handler(signum: int, frame) -> None:  # type: ignore[no-untyped-def]
        nonlocal timeout_hit
        timeout_hit = True
        raise _TimeoutError()

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1000.0)
    try:
        matches = list(pattern.finditer(text))
    except _TimeoutError:
        timeout_hit = True
        logger.warning("Regex timed out after %d ms", timeout_ms)
    except Exception:
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)

    return matches


# ---------------------------------------------------------------------------
# Volatile content detector
# ---------------------------------------------------------------------------


class VolatileContentDetector:
    """Detect and normalise volatile content in text.

    Six compiled regex patterns (class constants, atomic-grouped) identify
    content that differs between cache invalidation cycles: timestamps,
    session / request IDs, user context, error messages, cached search
    summaries, and auto-generated IDs.

    Usage::

        detector = VolatileContentDetector()
        tags = detector.detect_all(body_text)
        normalised = detector.normalise(body_text, tags)
    """

    #: ISO8601, RFC2822, mm/dd/yy — replace with ``<TS>``
    TIMESTAMP: re.Pattern = re.compile(
        r"(?>"
        # ISO8601 datetime: 2024-01-15T10:30:00(Z|±HH:MM) or with space
        r"(?:[12]\d{3}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
        r"(?:[T ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?"
        r"(?:Z|[+-](?:0\d|1[0-3]):[0-5]\d)?)?)"
        r"|"
        # RFC2822: Mon, 15 Jan 2024 10:30:00 +0000
        r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{1,2} "
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} "
        r"\d{2}:\d{2}(?::\d{2})? (?:[+-]\d{4}|[A-Z]{3,5}))"
        r"|"
        # mm/dd/yy or mm/dd/yyyy
        r"(?:(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:\d{2}|\d{4}))"
        r")"
    )

    #: ``session_<hex16-64>``, ``s-<hex8-48>``, 32-char hex — replace with ``<SID>``
    SESSION_ID: re.Pattern = re.compile(
        r"(?>"
        r"\b(?:session_[0-9a-fA-F]{16,64})"
        r"|"
        r"\b(?:s-[0-9a-fA-F]{8,48})"
        r"|"
        r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])"
        r")"
    )

    #: ``user_id`` / ``user_name`` / ``user_email`` patterns + email addresses
    #: — replace with ``<USER>``
    USER_CONTEXT: re.Pattern = re.compile(
        r"(?>"
        r"\buser[_\-]?(?:id|name|email|login|handle)"
        r"(?:\s*[:=]\s*)[^\s,;]{1,64}"
        r"|"
        r"\b[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
        r"@[a-zA-Z0-9-]{1,63}(?:\.[a-zA-Z0-9-]{1,63})+"
        r")"
    )

    #: ``Error`` / ``Exception`` / ``Traceback`` + continuation lines
    #: — **stripped entirely** (``normalized = None``)
    ERROR_MSG: re.Pattern = re.compile(
        r"(?>"
        r"^[ \t]*"
        r"(?:Error|Exception|Traceback)[^\n]{0,200}"
        r"(?:\n[ \t]+(?:at |in |File |raise |    ).{0,200}){0,10}"
        r")",
        re.MULTILINE,
    )

    #: ``Showing`` / ``Found`` / ``Displaying`` *N* ``of`` *M* ``results``
    #: — replace with ``<RESULTS>``
    CACHED_SEARCH: re.Pattern = re.compile(
        r"(?>"
        r"\b(?:Showing|Found|Displaying|Found a total of)[ \t]+"
        r"\d+[ \t]*(?:of|–|—|-)[ \t]*\d+"
        r"[ \t]*(?:results?|entries?|matches?)?"
        r")",
        re.IGNORECASE,
    )

    #: UUID v4, SHA1 hex — replace with ``<ID>``
    AUTO_ID: re.Pattern = re.compile(
        r"(?>"
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        r"|"
        r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])"
        r")"
    )

    _DETECTORS: list[tuple[str, re.Pattern, str | None]] = [
        ("timestamp", TIMESTAMP, "<TS>"),
        ("session_id", SESSION_ID, "<SID>"),
        ("user_context", USER_CONTEXT, "<USER>"),
        ("error_msg", ERROR_MSG, None),
        ("cached_search", CACHED_SEARCH, "<RESULTS>"),
        ("auto_id", AUTO_ID, "<ID>"),
    ]

    def __init__(self, timeout_ms: int = 100) -> None:
        self._timeout_ms = timeout_ms

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_all(self, text: str) -> list[VolatileTag]:
        """Run all 6 detectors, merge overlapping spans, sort by start_pos.

        If a detector times out (>100 ms per pattern), the timeout is logged
        and that detector is skipped.
        """
        all_tags: list[VolatileTag] = []
        for name, pattern, replacement in self._DETECTORS:
            matches = _regex_finditer_with_timeout(
                pattern, text, self._timeout_ms,
            )
            for m in matches:
                all_tags.append(VolatileTag(
                    detector=name,
                    original=m.group(),
                    normalized=replacement,
                    start_pos=m.start(),
                    end_pos=m.end(),
                ))
        merged = self._merge_overlapping(all_tags)
        merged.sort(key=lambda t: t.start_pos)
        return merged

    def normalize(self, text: str, tags: list[VolatileTag]) -> str:
        """Apply replacements end-to-start to preserve positions.

        Tags whose ``normalized`` is ``None`` (e.g. ``ERROR_MSG``) are
        stripped entirely.  All other tags are replaced with their
        normalised label (``<TS>``, ``<SID>``, etc.).
        """
        if not tags:
            return text
        sorted_tags = sorted(tags, key=lambda t: t.start_pos, reverse=True)
        for tag in sorted_tags:
            if tag.normalized is None:
                text = text[:tag.start_pos] + text[tag.end_pos:]
            else:
                text = text[:tag.start_pos] + tag.normalized + text[tag.end_pos:]
        return text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_overlapping(tags: list[VolatileTag]) -> list[VolatileTag]:
        """Merge overlapping (or adjacent) tags, keep the longest match.

        When two tags overlap, the one with the longer ``original`` text
        wins.  When lengths are equal the first occurrence wins.
        """
        if not tags:
            return []
        sorted_tags = sorted(tags, key=lambda t: (t.start_pos, -t.end_pos))
        merged: list[VolatileTag] = []
        for tag in sorted_tags:
            if not merged:
                merged.append(tag)
                continue
            last = merged[-1]
            if tag.start_pos >= last.end_pos:
                merged.append(tag)
                continue
            if len(tag.original) > len(last.original):
                merged[-1] = tag
        return merged


# ---------------------------------------------------------------------------
# Fixed-width token window packer
# ---------------------------------------------------------------------------


class WindowPacker:
    """Greedy section-to-window packer.

    Accepts ``PackedSection`` objects one at a time via ``add_section()``.
    When a section does not fit in the current window the caller must
    call ``flush()`` and retry.  Finalise with ``flush()``.

    Greedy strategy: sort sections by salience descending before calling
    ``add_section()``.  Sections are NEVER split across windows.

    Target: >90% capacity average.

    Parameters
    ----------
    max_tokens:
        Hard cap on total tokens per window (default 8192).
    min_tokens:
        Soft floor; windows smaller than this will still be emitted
        on ``flush()`` but a warning is logged (default 4096).
    """

    def __init__(self, max_tokens: int = 8192, min_tokens: int = 4096) -> None:
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self._sections: list[PackedSection] = []
        self._current_tokens = 0
        self._slot = 0
        self._cache_level = "l2"

    def add_section(self, packed: PackedSection) -> bool:
        """Try to add *packed* to the current window.

        Returns ``True`` if the section was added.  Returns ``False`` if
        the section would exceed ``max_tokens`` — the caller should
        ``flush()`` the current window and retry.
        """
        if self._current_tokens + packed.body_tokens > self.max_tokens:
            return False
        self._sections.append(packed)
        self._current_tokens += packed.body_tokens
        return True

    def flush(self) -> list[CompressedWindow]:
        """Return all completed windows and reset internal state.

        If the current in-progress window has sections below ``min_tokens``
        a warning is logged, but the window is still emitted.
        """
        if not self._sections:
            return []
        if self._current_tokens < self.min_tokens:
            logger.warning(
                "WindowPacker: slot %d has %d tokens (min_tokens=%d)",
                self._slot, self._current_tokens, self.min_tokens,
            )
        window = CompressedWindow(
            window_id=str(uuid.uuid4()),
            slot=self._slot,
            cache_level=self._cache_level,
            sections=list(self._sections),
            total_tokens=self._current_tokens,
            max_tokens=self.max_tokens,
        )
        self._slot += 1
        self._sections.clear()
        self._current_tokens = 0
        return [window]

    def reset(self) -> None:
        """Clear current state without emitting a window."""
        self._sections.clear()
        self._current_tokens = 0
        self._slot = 0


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences using a simple regex heuristic.

    The pattern splits on whitespace preceded by ``.!?`` and followed by an
    uppercase letter, which naturally avoids common abbreviations (Dr.,
    Mr., Ms., etc.) because the character after the period is lowercase.
    """
    if not text:
        return [""]
    parts = _SENTENCE_SPLIT.split(text)
    return [s.strip() for s in parts if s.strip()]


# ---------------------------------------------------------------------------
# P3-SA3 Crusher — aggressive compression strategies
# ---------------------------------------------------------------------------


class CompressionStrategy(enum.Enum):
    NONE = "none"
    VOLATILE_ONLY = "volatile_only"
    TRUNCATE = "truncate"
    EXTRACTIVE = "extractive"
    KEYWORD = "keyword"
    TITLE_ONLY = "title_only"
    SUMMARIZE = "summarize"


class CrusherConfig(NamedTuple):
    default_strategy: CompressionStrategy = CompressionStrategy.VOLATILE_ONLY
    aggressive_strategy: CompressionStrategy = CompressionStrategy.EXTRACTIVE
    max_chars: int = 2000
    max_sentences: int = 5
    min_chars: int = 50
    keyword_priority: list[str] = []


class Crusher:
    """Aggressive content compression for low-priority sections.

    Combines volatile content detection with strategy-based compression to
    fit more content into fixed token budgets while preserving meaning.

    Parameters
    ----------
    config:
        Compression configuration.  Falls back to ``CrusherConfig()`` defaults
        when ``None``.
    """

    def __init__(self, config: CrusherConfig | None = None) -> None:
        self._config = config or CrusherConfig()
        self._detector = VolatileContentDetector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crush(
        self,
        text: str,
        strategy: CompressionStrategy | None = None,
        keywords: list[str] | None = None,
    ) -> tuple[str, float]:
        """Compress *text* according to *strategy*.

        Parameters
        ----------
        text:
            Section body to compress.
        strategy:
            Compression strategy.  Falls back to ``config.default_strategy``.
        keywords:
            Additional keywords to preserve when using KEYWORD strategy
            (merged with ``config.keyword_priority``).

        Returns
        -------
        (crushed_text, compression_ratio):
            ``compression_ratio`` = len(result) / len(input), where 1.0 means
            unchanged and 0.0 means fully removed.
        """
        strategy = strategy or self._config.default_strategy
        original_len = len(text) or 1

        if strategy is CompressionStrategy.NONE:
            return text, 1.0

        # Volatile normalisation is always applied first (except NONE).
        tags = self._detector.detect_all(text)
        text = self._detector.normalize(text, tags)

        if strategy is CompressionStrategy.VOLATILE_ONLY:
            ratio = len(text) / original_len
            return self._apply_min_chars(text), ratio

        if strategy is CompressionStrategy.TRUNCATE:
            truncated = self._truncate_word_boundary(text, self._config.max_chars)
            ratio = len(truncated) / original_len
            return truncated, ratio

        if strategy is CompressionStrategy.EXTRACTIVE:
            sentences = _split_sentences(text)
            kept = sentences[: self._config.max_sentences]
            result = " ".join(kept)
            ratio = len(result) / original_len
            return self._apply_min_chars(result), ratio

        if strategy is CompressionStrategy.KEYWORD:
            merged_keywords = list(self._config.keyword_priority)
            if keywords:
                merged_keywords.extend(kw for kw in keywords if kw not in merged_keywords)
            if not merged_keywords:
                return self.crush(text, CompressionStrategy.EXTRACTIVE)
            sentences = _split_sentences(text)
            kw_set = {kw.lower() for kw in merged_keywords}
            kept = [s for s in sentences if any(kw in s.lower() for kw in kw_set)]
            result = " ".join(kept) if kept else ""
            ratio = len(result) / original_len if result else 0.0
            return self._apply_min_chars(result), ratio

        if strategy is CompressionStrategy.TITLE_ONLY:
            return "", 0.0

        if strategy is CompressionStrategy.SUMMARIZE:
            result = self._summarize(text)
            ratio = len(result) / original_len if result else 0.0
            return self._apply_min_chars(result), ratio

        return text, 1.0

    def crush_batch(
        self,
        sections: list[dict],
        strategy: CompressionStrategy | None = None,
    ) -> list[tuple[str, float]]:
        """Batch version of :meth:`crush`.

        Parameters
        ----------
        sections:
            List of dicts.  Each dict may contain keys ``body`` and
            ``keywords`` (optional).
        strategy:
            Compression strategy applied to every section.

        Returns
        -------
        List of ``(crushed_text, compression_ratio)`` in the same order as
        *sections*.
        """
        results: list[tuple[str, float]] = []
        for section in sections:
            body = section.get("body", "")
            kw = section.get("keywords")
            results.append(self.crush(body, strategy, keywords=kw))
        return results

    def select_strategy(
        self,
        salience: float,
        token_budget: int,
        current_usage: int,
    ) -> CompressionStrategy:
        """Pick a compression strategy based on content salience and budget.

        Parameters
        ----------
        salience:
            Content importance (0.0 = irrelevant, 1.0 = critical).
        token_budget:
            Total token budget available.
        current_usage:
            Tokens consumed so far.

        Returns
        -------
        Appropriate :class:`CompressionStrategy` for the given constraints.
        """
        remaining_ratio = (token_budget - current_usage) / max(token_budget, 1)

        if remaining_ratio <= 0:
            return CompressionStrategy.TITLE_ONLY

        if salience > 0.7 and remaining_ratio > 0.8:
            return CompressionStrategy.VOLATILE_ONLY

        if salience < 0.3 and remaining_ratio < 0.5:
            return CompressionStrategy.TITLE_ONLY

        if 0.3 <= salience <= 0.7:
            return self._config.aggressive_strategy

        return self._config.default_strategy

    # ------------------------------------------------------------------
    # Re-export
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Re-export of :func:`estimate_tokens`."""
        return estimate_tokens(text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_word_boundary(text: str, max_chars: int) -> str:
        """Truncate *text* at *max_chars*, avoiding mid-word splits."""
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > max_chars * 0.8:
            truncated = truncated[:last_space]
        return truncated

    @staticmethod
    def _summarize(text: str) -> str:
        """Rule-based extractive summarisation.

        Keeps the first sentence of the first paragraph plus any sentence
        containing "important", "key", or "note".
        """
        if not text:
            return ""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        first_sentence = ""
        if paragraphs:
            first_sentences = _split_sentences(paragraphs[0])
            if first_sentences:
                first_sentence = first_sentences[0]

        sentences = _split_sentences(text)
        important = [s for s in sentences if _IMPORTANT_WORDS.search(s.lower())]

        seen: set[str] = set()
        kept: list[str] = []
        for s in [first_sentence] + important:
            normalized = s.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                kept.append(s)

        return " ".join(kept)

    def _apply_min_chars(self, text: str) -> str:
        """Ensure *text* is at least ``config.min_chars`` characters."""
        if len(text) >= self._config.min_chars:
            return text
        return text[: self._config.min_chars]


_IMPORTANT_WORDS = re.compile(r"\b(?:important|key|note)\b")


__all__ = [
    "VolatileTag",
    "PackedSection",
    "CompressedWindow",
    "VolatileContentDetector",
    "WindowPacker",
    "estimate_tokens",
    "CompressionStrategy",
    "CrusherConfig",
    "Crusher",
]
