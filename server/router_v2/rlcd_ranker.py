"""RLCD pairwise ranker — contrastive distillation over routing preferences.

This is the RLCD half of the JEV+RLCD architecture, deliberately reduced to what this
host can actually do. Per `docs/plans/REVISED-PLAN-2026-11-11.md` §5, NEXUS adopted
RLCD as a **preference-pair data shape**, not as preference-model training + PPO
(those need ~40k examples per task and an A100-class GPU; this host has none).

What this module does
---------------------
Trains a **pairwise logistic (Bradley-Terry) linear ranker** over resource embeddings
using pairs accumulated in `reflection_fixes` (dispreferred = `resource_id`,
preferred = `alternative_resource_id`). Pure numpy, CPU-only, seconds to fit.

Why a linear ranker and not an LLM
----------------------------------
The decision it must make is "given this task and these candidate resources, which
one?" — a ranker over 384-dim embeddings is sufficient, inspectable, and trains on
tens of pairs rather than tens of thousands. It also cannot hallucinate.

The gate is the important part
------------------------------
`train()` **raises** below `MIN_PAIRS`. There is no "train anyway with a warning":
shipping a learner on a handful of samples is exactly how the observed
`gsd-doc-writer` bias was introduced. Callers integrate this behind
`NEXUS_V5_W_RLCD` (default 0.0) and may only raise that weight with a passing
`run_ab_suite` receipt.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: Hard gate from the repo's own plan: no pair-based learning below this many pairs.
MIN_PAIRS = int(os.environ.get("NEXUS_RLCD_MIN_PAIRS", "100"))
#: L2 strength for the tiny linear model.
L2 = float(os.environ.get("NEXUS_RLCD_L2", "0.01"))
#: Gradient-descent settings (problem is small and convex enough in practice).
EPOCHS = int(os.environ.get("NEXUS_RLCD_EPOCHS", "400"))
LR = float(os.environ.get("NEXUS_RLCD_LR", "0.5"))


class InsufficientPairs(RuntimeError):
    """Raised when distillation is attempted below the pair gate."""


@dataclass
class Pair:
    task: str
    preferred_id: int
    dispreferred_id: int


@dataclass
class RankerModel:
    weights: np.ndarray
    n_pairs: int
    train_accuracy: float = 0.0
    meta: dict = field(default_factory=dict)

    def score(self, skill_embedding: np.ndarray) -> float:
        return float(np.asarray(skill_embedding, dtype=np.float32) @ self.weights)

    def rerank(self, candidates: Sequence[tuple[str, np.ndarray]]) -> list[tuple[str, float]]:
        """Order candidates by learned preference score."""
        scored = [(name, self.score(vec)) for name, vec in candidates]
        return sorted(scored, key=lambda t: -t[1])


# ── data ─────────────────────────────────────────────────────────────────────

def load_pairs(db_path: str | None = None) -> list[Pair]:
    """Read contrastive pairs from `reflection_fixes` (no new table)."""
    from server.db import get_db_path
    path = str(db_path or get_db_path())
    con = sqlite3.connect(path, timeout=30)
    try:
        rows = con.execute(
            """SELECT f.resource_id AS d, f.alternative_resource_id AS p,
                      coalesce(f.failure_summary, '') AS q
                 FROM reflection_fixes f
                WHERE f.alternative_resource_id IS NOT NULL
                  AND f.resource_id IS NOT NULL
                  AND f.resource_id != f.alternative_resource_id""").fetchall()
    finally:
        con.close()
    return [Pair(task=str(q or ""), preferred_id=int(p), dispreferred_id=int(d))
            for d, p, q in rows]


def count_pairs(db_path: str | None = None) -> int:
    return len(load_pairs(db_path))


# ── training ─────────────────────────────────────────────────────────────────

def _skill_vectors(db_path: str | None = None, ids: set[int] | None = None) \
        -> dict[int, np.ndarray]:
    """Load `resources.embedding` for the given resource ids (all when ids is None)."""
    from server.db import get_db_path
    path = str(db_path or get_db_path())
    con = sqlite3.connect(path, timeout=30)
    try:
        out: dict[int, np.ndarray] = {}
        for rid, blob in con.execute(
                "SELECT id, embedding FROM resources WHERE embedding IS NOT NULL"):
            if ids is not None and rid not in ids:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.size:
                out[int(rid)] = vec
        return out
    finally:
        con.close()


def train(db_path: str | None = None, pairs: list[Pair] | None = None) -> RankerModel:
    """Fit a pairwise logistic ranker. Raises below the pair gate."""
    all_pairs = pairs if pairs is not None else load_pairs(db_path)
    if len(all_pairs) < MIN_PAIRS:
        raise InsufficientPairs(
            f"RLCD distillation refused: {len(all_pairs)} pairs < MIN_PAIRS={MIN_PAIRS}. "
            "Accumulate credit disagreements in reflection_fixes first.")

    needed = {p.preferred_id for p in all_pairs} | {p.dispreferred_id for p in all_pairs}
    vectors = _skill_vectors(db_path, needed)
    usable = [p for p in all_pairs if p.preferred_id in vectors and p.dispreferred_id in vectors]
    if len(usable) < MIN_PAIRS:
        raise InsufficientPairs(
            f"RLCD distillation refused: only {len(usable)} pairs have both skill "
            f"embeddings (need {MIN_PAIRS}). Backfill embeddings for all resources.")

    dim = len(next(iter(vectors.values())))
    w = np.zeros(dim, dtype=np.float32)
    n = len(usable)

    for _ in range(EPOCHS):
        grad = np.zeros(dim, dtype=np.float32)
        for p in usable:
            # Bradley-Terry on the embedding difference: prefer w·(x_p - x_d) > 0.
            delta = vectors[p.preferred_id] - vectors[p.dispreferred_id]
            z = float(w @ delta)
            # logistic loss gradient: -delta * sigmoid(-z)
            s = 1.0 / (1.0 + np.exp(min(60.0, z)))  # sigmoid(-z)
            grad += -delta * s
        grad = grad / n + L2 * w
        w -= LR * grad

    # in-sample preference accuracy
    correct = sum(1 for p in usable
                  if float(w @ (vectors[p.preferred_id] - vectors[p.dispreferred_id])) > 0)
    model = RankerModel(weights=w, n_pairs=len(usable),
                        train_accuracy=correct / len(usable),
                        meta={"dim": dim, "epochs": EPOCHS, "l2": L2,
                              "min_pairs": MIN_PAIRS})
    logger.info("RLCD ranker trained: %d pairs, accuracy %.3f", model.n_pairs,
                model.train_accuracy)
    return model


# ── integration guard ────────────────────────────────────────────────────────

def active_weight() -> float:
    """Weight for the 6th L3 fusion term. Defaults to 0.0 (inert).

    MUST stay 0.0 until `train()` succeeds AND `run_ab_suite` produces a passing
    receipt (AGENTS.md: no default weight change without an A/B receipt).
    """
    try:
        return max(0.0, float(os.environ.get("NEXUS_V5_W_RLCD", "0.0")))
    except (TypeError, ValueError):
        return 0.0
