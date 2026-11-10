"""P2: Hub-and-Spoke Router — query-to-context pipeline orchestrator.

Cache-first: L3 → L2 → L1 → L0. Falls through to full assembly
with classification + workflow affinity boost. Target: <50 ms.
"""

from __future__ import annotations
from server.db import get_db_path as _resolve_db_path

import logging
import statistics
import time
from pathlib import Path
from typing import NamedTuple, TypedDict

from server.context_harness.cache import (
    _L0Entry,
    _L1Entry,
    _L2Entry,
    get_l0_cache,
    get_l1_cache,
    get_l2_cache,
    get_l3_cache,
)
from server.context_harness.project_types import ProjectType, get_idf, get_signature
from server.context_harness.workflow_graph import EdgeCountCache, WorkflowGraph

try:
    from server.context_harness.classifier import Classifier, Classification, ClassifyConfig
    HAS_CLASSIFIER = True
except ImportError:
    HAS_CLASSIFIER = False

logger = logging.getLogger(__name__)
_L1_CACHE = get_l1_cache()

_CLUSTER_GROUP_MAP = {
    ProjectType.WEB_DEV: "web_dev",
    ProjectType.GENERAL_DEV: "general_dev",
    ProjectType.RESEARCH: "research",
    ProjectType.DEVOPS: "devops",
}
_CLUSTER_GROUPS = {
    "web_dev": ["frontend_ui", "backend_api"],
    "devops": ["quality_security", "devops_infra"],
    "general_dev": ["analyzer_planner", "general", "architect"],
    "research": ["knowledge_wiki"],
}


class RouterConfig(TypedDict, total=False):
    min_confidence: float
    enable_workflow_affinity: bool
    enable_cache: bool
    max_results: int
    score_threshold: float


_DEFAULT_CONFIG: RouterConfig = {
    "min_confidence": 0.15,
    "enable_workflow_affinity": True,
    "enable_cache": True,
    "max_results": 50,
    "score_threshold": 0.05,
}


class RouteContext(NamedTuple):
    query_text: str
    query_embedding: list[float] | None = None
    project_type: str | None = None
    cluster_filter: list[str] | None = None
    top_k: int = 10


class RouteResult(NamedTuple):
    resource_ids: list[int]
    scores: list[float]
    cache_level: str
    project_type: str | None
    confidence: float
    latency_ms: float
    hit_count: int


class HubAndSpokeRouter:
    def __init__(
        self, db_path: str | None = None, config: RouterConfig | None = None
    ) -> None:
        self.db_path = db_path or str(_resolve_db_path())
        merged = dict(_DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: RouterConfig = merged

        self.l0 = get_l0_cache()
        self.l1 = _L1_CACHE
        self.l2 = get_l2_cache()
        self.l3 = get_l3_cache()

        self.classifier: Classifier | None = None
        self.graph: WorkflowGraph | None = None
        self.edge_cache = EdgeCountCache()

        if db_path and self.config["enable_cache"]:
            if HAS_CLASSIFIER:
                try:
                    self.classifier = Classifier(db_path, ClassifyConfig())
                except Exception:
                    pass
            try:
                self.graph = WorkflowGraph(self.db_path)
                self.edge_cache.rebuild_from(self.graph)
            except Exception:
                pass

        self._latencies: list[float] = []
        self._hit_counts: dict[str, int] = {}
        self._miss_counts: dict[str, int] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def route(self, ctx: RouteContext) -> RouteResult:
        t0 = time.perf_counter()
        confidence = 0.0
        project_type = ctx.project_type

        # Step 1: L3 — query result cache
        if self.config["enable_cache"]:
            cached = self.l3.get(ctx.query_text)
            if cached is not None:
                elapsed = (time.perf_counter() - t0) * 1000
                ids = [s["resource_id"] for s in cached]
                sc = [s.get("score", 0.5) for s in cached]
                self._record_hit("L3")
                return RouteResult(
                    resource_ids=ids[: ctx.top_k],
                    scores=sc[: ctx.top_k],
                    cache_level="L3",
                    project_type=None,
                    confidence=0.0,
                    latency_ms=elapsed,
                    hit_count=1,
                )

        # Step 2: Classify
        if project_type is None:
            project_type, confidence = self._classify_query(ctx.query_text)

        # Steps 3-5: consult L1 → L2 → L0
        l1_bundle: dict[int, _L1Entry] = {}
        l2_entries: list[_L2Entry] = []
        deepest = "L0"

        if project_type and self.config["enable_cache"]:
            cluster_group = _CLUSTER_GROUP_MAP.get(ProjectType(project_type))
            if cluster_group:
                if cluster_group not in self.l1.loaded_project_types():
                    try:
                        self.l1.fill(cluster_group)
                    except Exception:
                        pass
                l1_bundle = self.l1.get_bundle(cluster_group) or {}
                if l1_bundle:
                    deepest = "L1"

                clusters = _CLUSTER_GROUPS.get(cluster_group, [])
                if ctx.cluster_filter:
                    clusters = [c for c in clusters if c in ctx.cluster_filter]
                if clusters:
                    try:
                        self.l2.activate(clusters)
                    except Exception:
                        pass
                    seen: set[int] = set()
                    for cl in clusters:
                        for entry in self.l2.get_all_for_cluster(cl):
                            if entry.resource_id not in seen:
                                l2_entries.append(entry)
                                seen.add(entry.resource_id)
                    if l2_entries:
                        deepest = "L2"

        # Step 6: Merge
        candidates = self._merge_levels(
            self.l0.get_all(),
            list(l1_bundle.values()),
            l2_entries,
        )

        if not candidates:
            elapsed = (time.perf_counter() - t0) * 1000
            self._record_miss(deepest)
            return RouteResult(
                resource_ids=[],
                scores=[],
                cache_level="L4",
                project_type=project_type,
                confidence=confidence,
                latency_ms=elapsed,
                hit_count=0,
            )

        # Step 7: Workflow affinity boost
        if self.config["enable_workflow_affinity"] and self.graph is not None:
            candidates = self._boost_with_workflow(candidates, project_type)

        threshold = self.config["score_threshold"]
        sorted_pairs = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        filtered_ids: list[int] = []
        filtered_scores: list[float] = []
        for rid, sc in sorted_pairs:
            if sc < threshold:
                break
            filtered_ids.append(rid)
            filtered_scores.append(sc)
            if len(filtered_ids) >= ctx.top_k:
                break

        if self.config["enable_cache"] and filtered_ids:
            iso_scores = [
                {"resource_id": rid, "score": sc, "token_count": 0}
                for rid, sc in zip(filtered_ids, filtered_scores)
            ]
            self.l3.set(ctx.query_text, iso_scores)

        elapsed = (time.perf_counter() - t0) * 1000
        self._record_hit(deepest)
        return RouteResult(
            resource_ids=filtered_ids,
            scores=filtered_scores,
            cache_level="L4",
            project_type=project_type,
            confidence=confidence,
            latency_ms=elapsed,
            hit_count=1,
        )

    def _classify_query(self, text: str) -> tuple[str, float]:
        if self.classifier is not None:
            try:
                result: Classification = self.classifier.classify(text)
                return (result.project_type, result.confidence)
            except Exception:
                pass

        text_lower = text.lower()
        scores: dict[str, float] = {}
        for pt in ProjectType:
            total = 0.0
            for sig in get_signature(pt):
                kw = sig["keyword"].lower()
                if kw in text_lower:
                    idf = get_idf(sig["keyword"])
                    total += sig["weight"] * idf * sig.get("boost_factor", 1.0)
            if total > 0:
                scores[pt.value] = total

        if not scores:
            return (ProjectType.GENERAL_DEV.value, 0.0)

        best = max(scores, key=scores.get)
        max_possible = max(scores.values()) or 1.0
        conf = min(scores[best] / max_possible, 1.0)
        if conf < self.config["min_confidence"]:
            return (ProjectType.GENERAL_DEV.value, conf)
        return (best, conf)

    def _boost_with_workflow(
        self, candidates: dict[int, float], project_type: str | None
    ) -> dict[int, float]:
        if self.graph is None:
            return candidates

        boosted: dict[int, float] = {}
        candidate_set = set(candidates)

        for rid, score in candidates.items():
            boost = 0.0

            incoming = self.graph.incoming_count(rid)
            if incoming > 0:
                boost += min(incoming * 0.02, 0.1)

            outgoing = self.graph.outgoing_edges(rid)
            linked_high_scorers = 0
            for edge in outgoing:
                if edge.target_id in candidate_set and candidates.get(edge.target_id, 0) > 0.3:
                    linked_high_scorers += 1
            if linked_high_scorers > 0:
                boost += min(linked_high_scorers * 0.025, 0.05)

            for source_edge in self.graph.incoming_edges(rid):
                if source_edge.target_id in candidate_set and candidates.get(source_edge.target_id, 0) > 0.3:
                    linked_high_scorers += 1
            if linked_high_scorers > 0:
                boost += min(linked_high_scorers * 0.025, 0.05)

            boosted[rid] = min(score + boost, 1.0)

        return boosted

    # ── Merge Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _merge_levels(
        l0: list[_L0Entry],
        l1: list[_L1Entry],
        l2: list[_L2Entry],
    ) -> dict[int, float]:
        merged: dict[int, float] = {}

        def upsert(rid: int, score: float) -> None:
            if rid in merged:
                merged[rid] = max(merged[rid], score)
            else:
                merged[rid] = score

        for e in l0:
            upsert(e.resource_id, 1.0 - min(e.priority / 100.0, 0.95))

        for e in l1:
            upsert(e.resource_id, 1.0 - min(e.priority / 100.0, 0.95))

        for e in l2:
            upsert(e.resource_id, e.iso_score)

        return merged

    # ── Cache Management ───────────────────────────────────────────────────

    def invalidate_cache(self, pattern: str | None = None) -> None:
        if pattern:
            self.l3.invalidate(pattern)
        else:
            self.l3.clear()

    def prewarm(self, project_types: list[str]) -> None:
        for raw_pt in project_types:
            try:
                pt = ProjectType(raw_pt)
            except ValueError:
                continue
            group = _CLUSTER_GROUP_MAP.get(pt)
            if group and group not in self.l1.loaded_project_types():
                self.l1.fill(group)

    # ── Health / Telemetry ─────────────────────────────────────────────────

    def cache_hit_rate(self) -> dict[str, float]:
        rates: dict[str, float] = {}
        for level in ("L0", "L1", "L2", "L3"):
            hits = self._hit_counts.get(level, 0)
            misses = self._miss_counts.get(level, 0)
            total = hits + misses
            rates[level] = hits / total if total > 0 else 0.0
        return rates

    def latency_p95(self) -> float:
        if len(self._latencies) < 20:
            return 0.0
        return statistics.quantile(self._latencies[-200:], 0.95)

    def __enter__(self) -> HubAndSpokeRouter:
        self._enter_time = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        span = (time.perf_counter() - self._enter_time) * 1000
        self._latencies.append(span)
        if len(self._latencies) > 1000:
            self._latencies = self._latencies[-500:]

    def _record_hit(self, level: str) -> None:
        self._hit_counts[level] = self._hit_counts.get(level, 0) + 1

    def _record_miss(self, level: str) -> None:
        self._miss_counts[level] = self._miss_counts.get(level, 0) + 1

# ── Singleton Accessors ──────────────────────────────────────────────────────

_router: HubAndSpokeRouter | None = None


def get_router(db_path: str | None = None) -> HubAndSpokeRouter:
    global _router
    if _router is None:
        _router = HubAndSpokeRouter(db_path)
    return _router
