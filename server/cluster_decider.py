"""Cluster decision with a confidence margin.

`router.detect_cluster` used to delegate entirely to keyword signals
(`auto_trigger.AutoTrigger`), which collapsed heterogeneous tasks onto one cluster:
measured 5/5 of "write tests", "debug a login session", "review a design doc",
"audit docs vs code" and an e-commerce research task all landing on
``knowledge_wiki`` — a 13-resource pool that then **starved** better candidates
before scoring even began (``analyzer_planner`` holds 43).

Two fixes live here:

1. **Embedding-centroid classification** against the per-cluster centroids already
   stored in ``cluster_definitions.centroid_embedding`` (seeded by
   ``scripts/seed_clusters.py``), instead of keyword-only matching.
2. **A margin rule.** If the top two clusters are within ``MARGIN`` of each other the
   decision is *declined*; the caller falls back to signals or widens the pool.
   Declining is the point: a low-confidence guess must never narrow the candidate
   pool to 13 resources.

A JEV/TypeSafe backend is wired behind the same interface and activates automatically
when ``TYPESAFE_API_KEY`` is present. Without a key the local path is used — no error,
no behaviour change beyond the fix.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)

#: Minimum cosine similarity for any cluster to be accepted at all.
#: Calibrated on this corpus (127 resources, 6 populated clusters, MiniLM-384
#: member centroids): measured top-1 cosine across 10 heterogeneous tasks spans
#: 0.146-0.383, so the original 0.35 floor rejected nearly everything. 0.20 keeps
#: clear wins (devops/backend/research) and still declines pure noise (0.146).
ACCEPT_THRESHOLD = float(os.environ.get("NEXUS_CLUSTER_ACCEPT", "0.20"))
#: Minimum gap between the top two clusters before the decision is trusted.
#: Measured margins on this corpus: clear wins 0.042-0.106, ambiguous 0.004-0.023.
MARGIN = float(os.environ.get("NEXUS_CLUSTER_MARGIN", "0.03"))

GENERAL = "general"


@dataclass
class ClusterDecision:
    cluster: str | None = None
    confidence: float = 0.0
    margin: float = 0.0
    source: str = "none"
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.cluster) and self.cluster != GENERAL

    def to_dict(self) -> dict:
        return {
            "cluster": self.cluster,
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "source": self.source,
            "reason": self.reason,
            "alternatives": [(n, round(s, 4)) for n, s in self.alternatives[:4]],
        }


class ClusterDecider(Protocol):
    def decide(self, task: str) -> ClusterDecision: ...


# ── embedding-centroid backend (default, no key) ─────────────────────────────

class LocalCentroidDecider:
    """Cosine match against `cluster_definitions.centroid_embedding` with a margin rule."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._centroids: dict[str, "object"] | None = None
        self._embedder = None

    # -- lazy resources ---------------------------------------------------
    def _db(self) -> str:
        if self._db_path:
            return str(self._db_path)
        from server.db import get_db_path
        return str(get_db_path())

    def _encoder(self):
        if self._embedder is None:
            from server.iso_embedder import IsoEmbedder
            self._embedder = IsoEmbedder.get(self._db())
            self._embedder.load_model()
        return self._embedder

    def _load_centroids(self):
        if self._centroids is not None:
            return self._centroids
        import numpy as np
        out: dict[str, object] = {}
        con = sqlite3.connect(self._db(), timeout=30)
        try:
            for name, blob in con.execute(
                    "SELECT name, centroid_embedding FROM cluster_definitions "
                    "WHERE centroid_embedding IS NOT NULL AND name != ?", (GENERAL,)):
                vec = np.frombuffer(blob, dtype=np.float32)
                if vec.size:
                    out[name] = vec / max(1e-9, float(np.linalg.norm(vec)))
        except sqlite3.Error as exc:
            logger.warning("cluster centroids unavailable: %s", exc)
        finally:
            con.close()
        self._centroids = out
        return out

    # -- decision ---------------------------------------------------------
    def decide(self, task: str) -> ClusterDecision:
        import numpy as np

        text = (task or "").strip()
        if not text:
            return ClusterDecision(source="centroid", reason="empty_task")

        centroids = self._load_centroids()
        if not centroids:
            return ClusterDecision(source="centroid", reason="no_centroids")

        try:
            vec = np.asarray(self._encoder().encode(text), dtype=np.float32)
        except Exception as exc:
            return ClusterDecision(source="centroid", reason=f"embed_failed:{type(exc).__name__}")
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return ClusterDecision(source="centroid", reason="zero_vector")
        vec = vec / norm

        scored = sorted(((name, float(vec @ c)) for name, c in centroids.items()),
                        key=lambda t: -t[1])
        top_name, top_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        margin = top_score - second_score

        if top_score < ACCEPT_THRESHOLD:
            return ClusterDecision(
                cluster=None, confidence=top_score, margin=margin, source="centroid",
                alternatives=scored, reason=f"below_accept:{top_score:.3f}")

        # The actual fix: an ambiguous top-two is not a decision.
        if margin < MARGIN:
            return ClusterDecision(
                cluster=GENERAL, confidence=top_score, margin=margin, source="centroid",
                alternatives=scored, reason=f"ambiguous_margin:{margin:.3f}")

        return ClusterDecision(cluster=top_name, confidence=top_score, margin=margin,
                               source="centroid", alternatives=scored)


# ── JEV / TypeSafe backend ───────────────────────────────────────────────────

class JevClusterDecider:
    """Typed `cluster` question to System One; declines if the key or schema fails."""

    def __init__(self) -> None:
        from server.decision_classifier import get_classifier
        self._classifier = get_classifier("jev")

    def available(self) -> bool:
        return self._classifier.available()

    def decide(self, task: str) -> ClusterDecision:
        if not self._classifier.available():
            return ClusterDecision(source="jev", reason="no_api_key")
        try:
            from server.cluster_config import CLUSTER_DEFINITIONS
            options: Sequence[str] = [n for n in CLUSTER_DEFINITIONS if n != GENERAL]
        except Exception:
            return ClusterDecision(source="jev", reason="options_unavailable")
        try:
            ans = self._classifier.classify(
                "cluster", task, options,
                instructions=("Pick the single best capability cluster for this task. "
                              f"Allowed: {', '.join(options)}"))
        except Exception as exc:  # never let a hosted call break routing
            return ClusterDecision(source="jev", reason=f"classifier_error:{type(exc).__name__}")
        if not ans.ok:
            return ClusterDecision(source="jev", reason=ans.reason or "declined")
        return ClusterDecision(cluster=ans.value, confidence=ans.confidence or 0.8,
                               margin=1.0, source="jev")


# ── factory ──────────────────────────────────────────────────────────────────

_CACHE: dict[str, object] = {}


def get_decider(prefer: str | None = None) -> ClusterDecider:
    """Resolve a cluster decider. `NEXUS_CLUSTER_DECIDER` = auto|local|jev."""
    want = (prefer or os.environ.get("NEXUS_CLUSTER_DECIDER") or "auto").strip().lower()
    cached = _CACHE.get(want)
    if cached is not None:
        return cached  # type: ignore[return-value]

    chosen: object
    if want == "jev":
        chosen = JevClusterDecider()
    elif want == "local":
        chosen = LocalCentroidDecider()
    else:  # auto: JEV when keyed, else local; never fail for a missing key
        try:
            jev = JevClusterDecider()
            chosen = jev if jev.available() else LocalCentroidDecider()
        except Exception:
            chosen = LocalCentroidDecider()

    _CACHE[want] = chosen
    return chosen  # type: ignore[return-value]
