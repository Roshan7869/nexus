"""Weighted TF-IDF Resource Scorer for NEXUS Context Harness Engine.
P0-S2: Multi-type keyword matching with IDF-weighted scoring.
"""

import re
import sqlite3
import time
from collections import OrderedDict
from typing import Callable, NamedTuple

from server.context_harness.project_types import ProjectType, get_signature, get_idf

_SIG_VERSION = 1

_BOOST_NAME = 3.0
_BOOST_DESC = 2.0
_BOOST_BODY = 1.0
_BOOST_SECTION_HEADING = 1.5
_BOOST_SECTION_CONTENT = 1.0

_matcher_cache: dict[str, Callable[[str], bool]] = {}


def _get_matcher(keyword: str) -> Callable[[str], bool]:
    matcher = _matcher_cache.get(keyword)
    if matcher is not None:
        return matcher
    if len(keyword) <= 2 or " " in keyword.strip():
        kw_lower = keyword.lower()
        def _substr(text: str) -> bool:
            return kw_lower in text.lower()
        _matcher_cache[keyword] = _substr
    else:
        pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
        def _regex(text: str) -> bool:
            return bool(pattern.search(text))
        _matcher_cache[keyword] = _regex
    return _matcher_cache[keyword]


class ResourceSignals(NamedTuple):
    resource_id: int
    name: str
    description: str
    body_text_snippet: str
    resource_type: str
    cluster: str
    section_headings: list[str]
    section_contents: list[str]


class ResourceScoreVector(NamedTuple):
    resource_id: int
    scores: dict[str, float]
    dominant_type: str | None
    confidence: float
    match_count: int


class ScoreCache:
    """LRU cache mapping (resource_id, sig_version) -> ResourceScoreVector."""

    def __init__(self, maxsize: int = 500, ttl: float = 300.0):
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict[tuple[int, int], tuple[float, ResourceScoreVector]] = OrderedDict()

    def get(self, resource_id: int, sig_version: int = _SIG_VERSION) -> ResourceScoreVector | None:
        key = (resource_id, sig_version)
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, vector = entry
        if time.monotonic() - ts > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return vector

    def set(self, resource_id: int, vector: ResourceScoreVector, sig_version: int = _SIG_VERSION) -> None:
        key = (resource_id, sig_version)
        self._cache[key] = (time.monotonic(), vector)
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def invalidate(self, resource_id: int | None = None) -> None:
        if resource_id is None:
            self._cache.clear()
        else:
            stale = [k for k in self._cache if k[0] == resource_id]
            for k in stale:
                del self._cache[k]

    @property
    def size(self) -> int:
        return len(self._cache)


def compute_single_score(
    resource_signals: ResourceSignals,
    signatures: dict[ProjectType, list[dict]],
    idf_cache: dict[str, float],
) -> ResourceScoreVector:
    scores: dict[str, float] = {}
    total_matches = 0

    for pt, pt_signatures in signatures.items():
        raw_score = 0.0
        type_matches = 0

        for sig in pt_signatures:
            keyword = sig["keyword"]
            weight = sig["weight"]
            idf_score = idf_cache.get(keyword, 1.0)
            source_focus = sig.get("source_focus", "body")
            matcher = _get_matcher(keyword)

            matched = False

            for field in source_focus.split("|"):
                if field == "name" and resource_signals.name and matcher(resource_signals.name):
                    raw_score += weight * idf_score * _BOOST_NAME
                    matched = True
                elif field == "desc" and resource_signals.description and matcher(resource_signals.description):
                    raw_score += weight * idf_score * _BOOST_DESC
                    matched = True
                elif field == "body" and resource_signals.body_text_snippet and matcher(resource_signals.body_text_snippet):
                    raw_score += weight * idf_score * _BOOST_BODY
                    matched = True

            if resource_signals.section_headings:
                for heading in resource_signals.section_headings:
                    if heading and matcher(heading):
                        raw_score += weight * idf_score * _BOOST_SECTION_HEADING
                        matched = True
                        break

            if resource_signals.section_contents:
                for content in resource_signals.section_contents:
                    if content and matcher(content):
                        raw_score += weight * idf_score * _BOOST_SECTION_CONTENT
                        matched = True
                        break

            if matched:
                type_matches += 1

        normalized = raw_score / max(raw_score, 1.0)
        normalized = max(0.0, min(1.0, normalized))
        scores[pt.value] = normalized
        total_matches += type_matches

    dominant_type: str | None = None
    max_score = -1.0
    for pt in ProjectType:
        s = scores.get(pt.value, 0.0)
        if s > max_score:
            max_score = s
            dominant_type = pt.value

    return ResourceScoreVector(
        resource_id=resource_signals.resource_id,
        scores=scores,
        dominant_type=dominant_type,
        confidence=max_score,
        match_count=total_matches,
    )


class ResourceRow(NamedTuple):
    id: int
    name: str
    description: str
    body_text: str
    resource_type: str
    cluster: str


def _load_all_resources(conn: sqlite3.Connection) -> list[ResourceRow]:
    cursor = conn.execute("""
        SELECT r.id, r.name, r.description, r.body_text, r.resource_type, r.cluster
        FROM resources r
        WHERE r.status = 'active'
    """)
    return [ResourceRow(*row) for row in cursor.fetchall()]


def _load_all_section_keywords(conn: sqlite3.Connection) -> dict[int, tuple[list[str], list[str]]]:
    cursor = conn.execute("""
        SELECT rs.resource_id, rs.heading, rs.content
        FROM resource_sections rs
        JOIN resources r ON rs.resource_id = r.id
        WHERE r.status = 'active'
    """)
    result: dict[int, tuple[list[str], list[str]]] = {}
    for row in cursor.fetchall():
        resource_id, heading, content = row
        if resource_id not in result:
            result[resource_id] = ([], [])
        result[resource_id][0].append(heading or "")
        result[resource_id][1].append(content or "")
    return result


def batch_compute_all(
    db_path: str,
    signatures: dict[ProjectType, list[dict]],
    idf_cache: dict[str, float],
) -> dict[int, ResourceScoreVector]:
    conn = sqlite3.connect(db_path)
    try:
        resources = _load_all_resources(conn)
        sections = _load_all_section_keywords(conn)
        results: dict[int, ResourceScoreVector] = {}
        for row in resources:
            headings, contents = sections.get(row.id, ([], []))
            body_snippet = (row.body_text or "")[:500]
            signals = ResourceSignals(
                resource_id=row.id,
                name=row.name or "",
                description=row.description or "",
                body_text_snippet=body_snippet,
                resource_type=row.resource_type or "",
                cluster=row.cluster or "",
                section_headings=headings,
                section_contents=contents,
            )
            results[row.id] = compute_single_score(signals, signatures, idf_cache)
        return results
    finally:
        conn.close()
