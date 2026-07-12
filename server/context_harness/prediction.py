"""Prediction engine and signal choke for context-aware resource prewarm.

P4-S2 (Prediction Engine) — predicts which resources are likely to be needed
next based on workflow graph affinities, past routing patterns, and bundle
membership.

P4-S3 (Signal Choke) — filters low-confidence predictions and prevents noisy
or infrequent signals from triggering cache prewarm or resource loading.
"""

import time
from collections import defaultdict, deque
from typing import NamedTuple, TypedDict

from server.context_harness.workflow_graph import WorkflowGraph


class PredictionConfig(TypedDict):
    min_workflow_weight: float
    max_predictions: int
    signal_choke_threshold: float
    enable_negative_signal: bool
    window_size: int
    cooldown_seconds: int


DEFAULT_PREDICTION_CONFIG: PredictionConfig = {
    "min_workflow_weight": 0.1,
    "max_predictions": 20,
    "signal_choke_threshold": 0.3,
    "enable_negative_signal": False,
    "window_size": 5,
    "cooldown_seconds": 300,
}


class PredictedResource(NamedTuple):
    resource_id: int
    score: float
    source: str
    reason: str
    predicted_at: float


class SignalEvent(NamedTuple):
    resource_id: int
    signal_type: str
    confidence: float
    timestamp: float
    source: str


class WorkflowPredictor:
    """Predict next resources from workflow graph affinities."""

    def __init__(self, graph: WorkflowGraph, config: PredictionConfig | None = None):
        self._graph = graph
        self._config = config or dict(DEFAULT_PREDICTION_CONFIG)
        self._last_predicted: dict[int, float] = {}

    def predict_next(self, active_resource_ids: list[int]) -> list[PredictedResource]:
        """Predict which resources are likely needed next.

        For each active resource, lookup outgoing edges in the workflow graph.
        Score each target: sum(edge.weight) / len(active), normalized to 0-1.
        Filters by min_workflow_weight, deduplicates, applies cooldown.
        """
        if not active_resource_ids:
            return []

        raw_scores: dict[int, float] = defaultdict(float)
        sources_for_target: dict[int, list[int]] = defaultdict(list)
        now = time.time()
        min_weight = self._config.get("min_workflow_weight", 0.1)
        max_predictions = self._config.get("max_predictions", 20)
        cooldown = self._config.get("cooldown_seconds", 300)

        for rid in active_resource_ids:
            for edge in self._graph.outgoing_edges(rid):
                if edge.weight < min_weight:
                    continue
                raw_scores[edge.target_id] += edge.weight
                sources_for_target[edge.target_id].append(rid)

        if not raw_scores:
            return []

        for target_id in raw_scores:
            raw_scores[target_id] /= len(active_resource_ids)

        normalized = self._normalize_scores(raw_scores)

        results: list[PredictedResource] = []
        for target_id, score in sorted(normalized.items(), key=lambda x: -x[1]):
            if (
                target_id in self._last_predicted
                and (now - self._last_predicted[target_id]) < cooldown
            ):
                continue
            n_sources = len(sources_for_target.get(target_id, []))
            results.append(
                PredictedResource(
                    resource_id=target_id,
                    score=score,
                    source="workflow_affinity",
                    reason=f"predicted from {n_sources} active resource(s); score={score:.3f}",
                    predicted_at=now,
                )
            )
            self._last_predicted[target_id] = now
            if len(results) >= max_predictions:
                break

        return results

    def predict_for_bundle(
        self, bundle_name: str, bundle_members: list[int]
    ) -> list[PredictedResource]:
        """Predict next resources scoped to bundle members as source IDs."""
        if not bundle_members:
            return []

        raw_scores: dict[int, float] = defaultdict(float)
        sources_for_target: dict[int, list[int]] = defaultdict(list)
        now = time.time()
        min_weight = self._config.get("min_workflow_weight", 0.1)
        max_predictions = self._config.get("max_predictions", 20)
        cooldown = self._config.get("cooldown_seconds", 300)

        for rid in bundle_members:
            for edge in self._graph.outgoing_edges(rid):
                if edge.weight < min_weight:
                    continue
                raw_scores[edge.target_id] += edge.weight
                sources_for_target[edge.target_id].append(rid)

        if not raw_scores:
            return []

        for target_id in raw_scores:
            raw_scores[target_id] /= len(bundle_members)

        normalized = self._normalize_scores(raw_scores)

        results: list[PredictedResource] = []
        for target_id, score in sorted(normalized.items(), key=lambda x: -x[1]):
            if (
                target_id in self._last_predicted
                and (now - self._last_predicted[target_id]) < cooldown
            ):
                continue
            n_sources = len(sources_for_target.get(target_id, []))
            results.append(
                PredictedResource(
                    resource_id=target_id,
                    score=score,
                    source="bundle_affinity",
                    reason=f"bundle '{bundle_name}' affinity from {n_sources} member(s); score={score:.3f}",
                    predicted_at=now,
                )
            )
            self._last_predicted[target_id] = now
            if len(results) >= max_predictions:
                break

        return results

    @staticmethod
    def _normalize_scores(scores: dict[int, float]) -> dict[int, float]:
        if not scores:
            return {}
        values = list(scores.values())
        min_v = min(values)
        max_v = max(values)
        if max_v - min_v < 1e-12:
            return {k: 1.0 for k in scores}
        return {
            k: (v - min_v) / (max_v - min_v) for k, v in scores.items()
        }


class SignalChoke:
    """Filters low-confidence predictions and prevents noisy signals.

    Maintains a rolling window of recent signal outcomes per resource_id
    and gates emission based on confidence threshold and cooldown.
    """

    def __init__(self, config: PredictionConfig | None = None):
        self._config = config or dict(DEFAULT_PREDICTION_CONFIG)
        self._signals: dict[int, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._config.get("window_size", 5))
        )
        self._last_signal: dict[int, float] = {}

    def evaluate(self, prediction: PredictedResource) -> SignalEvent | None:
        """Gate a prediction and return a SignalEvent if it passes.

        Returns None when:
        - prediction.score < signal_choke_threshold
        - resource is in cooldown
        """
        threshold = self._config.get("signal_choke_threshold", 0.3)
        cooldown = self._config.get("cooldown_seconds", 300)
        now = time.time()

        if prediction.score < threshold:
            return None

        if (
            prediction.resource_id in self._last_signal
            and (now - self._last_signal[prediction.resource_id]) < cooldown
        ):
            return None

        if prediction.score > 0.7:
            signal_type = "prewarm"
        elif prediction.score > 0.5:
            signal_type = "cache_hint"
        else:
            signal_type = "load"

        self._last_signal[prediction.resource_id] = now
        return SignalEvent(
            resource_id=prediction.resource_id,
            signal_type=signal_type,
            confidence=prediction.score,
            timestamp=now,
            source=prediction.source,
        )

    def record_positive(self, resource_id: int) -> None:
        """Record a hit signal — increases rolling confidence."""
        self._signals[resource_id].append(1.0)

    def record_negative(self, resource_id: int) -> None:
        """Record a miss signal — decreases rolling confidence (if enabled)."""
        if self._config.get("enable_negative_signal", False):
            self._signals[resource_id].append(0.0)

    def get_signal_strength(self, resource_id: int) -> float:
        """Rolling average of recent signal outcomes (0.0-1.0)."""
        sigs = self._signals.get(resource_id)
        if not sigs:
            return 0.0
        return sum(sigs) / len(sigs)

    def reset(self) -> None:
        """Clear all signal state."""
        self._signals.clear()
        self._last_signal.clear()


class PredictionEngine:
    """Combines WorkflowPredictor + SignalChoke into a unified pipeline.

    Usage:
        engine = PredictionEngine(db_path)
        events = engine.predict([1, 2, 3])
        for event in events:
            # prewarm or load resource based on event.signal_type
            ...
        engine.report_outcome(resource_id=5, was_used=True)
    """

    def __init__(self, db_path: str, config: PredictionConfig | None = None):
        self._config = config or dict(DEFAULT_PREDICTION_CONFIG)
        self._graph = WorkflowGraph(db_path)
        self._predictor = WorkflowPredictor(self._graph, self._config)
        self._choke = SignalChoke(self._config)
        self._total_predictions = 0
        self._correct_predictions = 0

    def predict(self, active_ids: list[int]) -> list[SignalEvent]:
        """Run full prediction pipeline and return emitted signals."""
        predictions = self._predictor.predict_next(active_ids)
        events: list[SignalEvent] = []
        for p in predictions:
            event = self._choke.evaluate(p)
            if event is not None:
                events.append(event)
        self._total_predictions += len(events)
        return events

    def report_outcome(self, resource_id: int, was_used: bool) -> None:
        """Report whether a predicted resource was actually used."""
        if was_used:
            self._choke.record_positive(resource_id)
            self._correct_predictions += 1
        else:
            self._choke.record_negative(resource_id)

    def get_stats(self) -> dict:
        """Return prediction accuracy and system statistics."""
        accuracy = 0.0
        if self._total_predictions > 0:
            accuracy = self._correct_predictions / self._total_predictions

        return {
            "total_predictions": self._total_predictions,
            "correct_predictions": self._correct_predictions,
            "accuracy": accuracy,
            "graph_edge_count": self._graph.edge_count(),
            "graph_node_count": self._graph.node_count(),
        }


class FrequencyTracker:
    """Tracks how often a resource is requested within a time window.

    Uses a deque of timestamps per resource_id, expiring entries older
    than window_seconds.
    """

    def __init__(self, window_seconds: int = 3600):
        self._window_seconds = window_seconds
        self._entries: dict[int, deque[float]] = defaultdict(deque)

    def record(self, resource_id: int) -> None:
        """Record a request for the given resource."""
        now = time.time()
        dq = self._entries[resource_id]
        dq.append(now)
        self._prune(dq, now)

    def frequency(self, resource_id: int) -> float:
        """Return requests/hour within the current window."""
        dq = self._entries.get(resource_id)
        if not dq:
            return 0.0
        now = time.time()
        self._prune(dq, now)
        count = len(dq)
        hours = self._window_seconds / 3600.0
        return count / hours if hours > 0 else float(count)

    def top_k(self, k: int) -> list[tuple[int, float]]:
        """Return the k most frequently requested resources with their freq."""
        now = time.time()
        freqs: list[tuple[int, float]] = []
        for rid, dq in self._entries.items():
            self._prune(dq, now)
            if dq:
                hours = self._window_seconds / 3600.0
                freq = len(dq) / hours if hours > 0 else float(len(dq))
                freqs.append((rid, freq))
        freqs.sort(key=lambda x: -x[1])
        return freqs[:k]

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
