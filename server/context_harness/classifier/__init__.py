"""NEXUS Context Harness Engine — P0-S3: Resource Classifier.

Orchestrates TF-IDF scoring from P0-S2 into full resource classifications
with confidence scoring, persistent caching, and batch processing.
"""

import json
import sqlite3
from pathlib import Path
from typing import NamedTuple, TypedDict

from server.context_harness.project_types import ProjectType, get_signature, get_idf
from server.context_harness.classifier.scorer import (
    ResourceScoreVector,
    ResourceSignals,
    ScoreCache,
    compute_single_score,
    batch_compute_all,
    _SIG_VERSION,
    _get_matcher,
    _BOOST_NAME,
    _BOOST_DESC,
    _BOOST_BODY,
    _BOOST_SECTION_HEADING,
    _BOOST_SECTION_CONTENT,
    _load_all_resources,
    _load_all_section_keywords,
)


class Classification(NamedTuple):
    resource_id: int
    project_type: str
    confidence: float
    scores: dict[str, float]
    dominant_keyword: str | None
    match_count: int
    model_version: int = _SIG_VERSION


class ClassifyConfig(TypedDict):
    min_confidence: float
    fallback_type: str
    enable_section_scoring: bool
    max_resources_per_pass: int
    batch_size: int


_DEFAULT_CONFIG: ClassifyConfig = {
    "min_confidence": 0.15,
    "fallback_type": "general-dev",
    "enable_section_scoring": True,
    "max_resources_per_pass": 500,
    "batch_size": 200,
}


class Classifier:
    def __init__(
        self,
        db_path: str | None = None,
        config: ClassifyConfig | None = None,
    ):
        self._db_path = db_path or str(Path.home() / "nexus/db/nexus.db")
        self._config = {**_DEFAULT_CONFIG, **(config or {})}
        self._score_cache = ScoreCache()

        self._signatures: dict[ProjectType, list[dict]] = {}
        for pt in ProjectType:
            self._signatures[pt] = get_signature(pt)

        all_keywords: set[str] = set()
        for sigs in self._signatures.values():
            for sig in sigs:
                all_keywords.add(sig["keyword"])
        self._idf_cache = {kw: get_idf(kw) for kw in all_keywords}

    def _find_dominant_keyword(self, signals: ResourceSignals) -> str | None:
        best_keyword = None
        best_total = 0.0
        enable_sections = self._config["enable_section_scoring"]
        for keyword, idf_val in self._idf_cache.items():
            matcher = _get_matcher(keyword)
            total = 0.0
            if signals.name and matcher(signals.name):
                total += idf_val * _BOOST_NAME
            if signals.description and matcher(signals.description):
                total += idf_val * _BOOST_DESC
            if signals.body_text_snippet and matcher(signals.body_text_snippet):
                total += idf_val * _BOOST_BODY
            if signals.section_headings:
                for h in signals.section_headings:
                    if h and matcher(h):
                        total += idf_val * _BOOST_SECTION_HEADING
                        break
            if enable_sections and signals.section_contents:
                for c in signals.section_contents:
                    if c and matcher(c):
                        total += idf_val * _BOOST_SECTION_CONTENT
                        break
            if total > best_total:
                best_total = total
                best_keyword = keyword
        return best_keyword

    def classify(self, resource_signals: ResourceSignals) -> Classification:
        cached = self._score_cache.get(resource_signals.resource_id)
        if cached is not None:
            vector = cached
        else:
            vector = compute_single_score(
                resource_signals, self._signatures, self._idf_cache,
            )
            self._score_cache.set(resource_signals.resource_id, vector)

        project_type = vector.dominant_type or self._config["fallback_type"]
        if vector.confidence < self._config["min_confidence"]:
            project_type = self._config["fallback_type"]

        dominant_keyword = self._find_dominant_keyword(resource_signals)

        return Classification(
            resource_id=vector.resource_id,
            project_type=project_type,
            confidence=vector.confidence,
            scores=dict(vector.scores),
            dominant_keyword=dominant_keyword,
            match_count=vector.match_count,
            model_version=_SIG_VERSION,
        )

    def classify_batch(self, resources: list[dict]) -> list[Classification]:
        results: list[Classification] = []
        for item in resources:
            body_text = item.get("body_text", "") or ""
            signals = ResourceSignals(
                resource_id=item["id"],
                name=item.get("name", ""),
                description=item.get("description", ""),
                body_text_snippet=body_text[:500],
                resource_type=item.get("resource_type", ""),
                cluster=item.get("cluster", ""),
                section_headings=item.get("section_headings", []),
                section_contents=item.get("section_contents", []),
            )
            results.append(self.classify(signals))
        return results

    def classify_all(self) -> dict[int, Classification]:
        raw_results = batch_compute_all(
            self._db_path, self._signatures, self._idf_cache,
        )

        conn = sqlite3.connect(self._db_path)
        try:
            resource_rows = _load_all_resources(conn)
            section_rows = _load_all_section_keywords(conn)
        finally:
            conn.close()

        resource_map = {r.id: r for r in resource_rows}
        results: dict[int, Classification] = {}

        for rid, vector in raw_results.items():
            project_type = vector.dominant_type or self._config["fallback_type"]
            if vector.confidence < self._config["min_confidence"]:
                project_type = self._config["fallback_type"]

            row = resource_map.get(rid)
            if row is not None:
                headings, contents = section_rows.get(rid, ([], []))
                signals = ResourceSignals(
                    resource_id=rid,
                    name=row.name or "",
                    description=row.description or "",
                    body_text_snippet=(row.body_text or "")[:500],
                    resource_type=row.resource_type or "",
                    cluster=row.cluster or "",
                    section_headings=headings,
                    section_contents=contents,
                )
                dominant_kw = self._find_dominant_keyword(signals)
            else:
                dominant_kw = None

            results[rid] = Classification(
                resource_id=rid,
                project_type=project_type,
                confidence=vector.confidence,
                scores=dict(vector.scores),
                dominant_keyword=dominant_kw,
                match_count=vector.match_count,
                model_version=_SIG_VERSION,
            )

        return results

    def filter_by_type(
        self,
        classifications: dict[int, Classification],
        project_type: str | ProjectType,
        min_confidence: float | None = None,
    ) -> dict[int, Classification]:
        if isinstance(project_type, ProjectType):
            type_val = project_type.value
        else:
            type_val = project_type
        threshold = (
            min_confidence
            if min_confidence is not None
            else self._config["min_confidence"]
        )
        return {
            rid: c
            for rid, c in classifications.items()
            if c.project_type == type_val and c.confidence >= threshold
        }

    def save_to_file(self, path: str) -> None:
        results = self.classify_all()
        serializable = {
            str(rid): {
                "resource_id": c.resource_id,
                "project_type": c.project_type,
                "confidence": c.confidence,
                "scores": dict(c.scores),
                "dominant_keyword": c.dominant_keyword,
                "match_count": c.match_count,
                "model_version": c.model_version,
            }
            for rid, c in results.items()
        }
        with open(path, "w") as f:
            json.dump(serializable, f, indent=2)

    def load_from_file(self, path: str) -> dict[int, Classification]:
        with open(path) as f:
            raw = json.load(f)
        result: dict[int, Classification] = {}
        for rid_str, data in raw.items():
            result[int(rid_str)] = Classification(
                resource_id=data["resource_id"],
                project_type=data["project_type"],
                confidence=data["confidence"],
                scores=data["scores"],
                dominant_keyword=data.get("dominant_keyword"),
                match_count=data["match_count"],
                model_version=data.get("model_version", _SIG_VERSION),
            )
        return result


__all__ = [
    "Classification",
    "ClassifyConfig",
    "Classifier",
]
