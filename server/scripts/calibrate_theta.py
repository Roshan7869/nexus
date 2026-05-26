#!/usr/bin/env python3
"""Calibrate theta for Tool Attention v4 using labeled routing_log data.

Sweeps ISO score threshold theta from 0.10 to 0.50 in 0.02 increments,
computes precision/recall/F1 at each step against ground-truth outcomes
('correct' / 'wrong') logged in routing_log.

Reports per-resource-type optimal theta and full sweep table.
Writes results to theta-calibration.json.
"""
from __future__ import annotations
import json, sqlite3, os, sys
from pathlib import Path
from typing import Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

DB_PATH = Path.home() / "nexus/db/nexus.db"
INDEX_PATH = Path.home() / "nexus/db/faiss/resources.index"
OUTPUT_PATH = Path("/home/roshan/nexus/server/scripts/theta-calibration.json")
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
THETA_MIN = 0.10
THETA_MAX = 0.50
THETA_STEP = 0.02


class Calibrator:
    """Load labeled data, compute ISO scores, and sweep theta.

    Reuses the same embedding model and FAISS index as ToolAttentionV4
    (tool_attention_v4.py) so calibration is directly applicable.
    """

    def __init__(self):
        self.model: Optional[SentenceTransformer] = None
        self.index: Optional[faiss.Index] = None
        self.resource_ids: list[int] = []
        self._conn: Optional[sqlite3.Connection] = None
        self._resource_id_map: dict[str, int] = {}

    def load(self):
        if self.model is None:
            os.environ["TOKENIZERS_PARALLELISM"] = "false"
            self.model = SentenceTransformer(MODEL_ID)
        if self.index is None:
            self.index = faiss.read_index(str(INDEX_PATH))
            with open(str(INDEX_PATH) + ".meta.json") as f:
                meta = json.load(f)
            self.resource_ids = meta["resource_ids"]
        if self._conn is None:
            self._conn = sqlite3.connect(str(DB_PATH), timeout=60)
            self._conn.row_factory = sqlite3.Row

    def fetch_labeled_pairs(self) -> list[dict]:
        """Fetch rows from routing_log where outcome is 'correct' or 'wrong'."""
        self.load()

        rows = self._conn.execute(
            "SELECT id, task_summary, skill_name, outcome "
            "FROM routing_log "
            "WHERE outcome IN ('correct', 'wrong') "
            "ORDER BY created_at DESC"
        ).fetchall()

        # Build name -> id + resource_type map
        resources = self._conn.execute(
            "SELECT id, name, resource_type FROM resources WHERE status='active'"
        ).fetchall()
        self._resource_id_map = {r["name"]: r["id"] for r in resources}
        self._resource_type_map = {r["name"]: r["resource_type"] for r in resources}

        pairs = []
        for r in rows:
            summary = (r["task_summary"] or "").strip()
            skill = (r["skill_name"] or "").strip()
            if not summary or not skill:
                continue
            resource_id = self._resource_id_map.get(skill, -1)
            pairs.append({
                "log_id": r["id"],
                "query": summary,
                "skill_name": skill,
                "resource_id": resource_id,
                "resource_type": self._resource_type_map.get(skill, "unknown"),
                "outcome": r["outcome"],
            })
        return pairs

    def compute_iso_for_query(self, query: str, resource_id: int) -> Optional[float]:
        """Compute ISO score of a specific resource against the query via FAISS."""
        if resource_id < 0:
            return None
        self.load()
        qvec = self.model.encode([query]).astype(np.float32)
        faiss.normalize_L2(qvec)
        distances, indices = self.index.search(qvec, len(self.resource_ids))

        for j, i in enumerate(indices[0]):
            if i >= len(self.resource_ids):
                continue
            if self.resource_ids[i] == resource_id:
                return float(distances[0][j])
        return None

    @staticmethod
    def _compute_metrics(
        annotated: list[dict], theta: float
    ) -> dict:
        """Compute TP, FP, FN, precision, recall, F1 at a given theta."""
        tp = sum(
            1 for a in annotated
            if a["iso_score"] is not None and a["iso_score"] >= theta
            and a["outcome"] == "correct"
        )
        fp = sum(
            1 for a in annotated
            if a["iso_score"] is not None and a["iso_score"] >= theta
            and a["outcome"] == "wrong"
        )
        fn = sum(
            1 for a in annotated
            if (
                (a["iso_score"] is not None and a["iso_score"] < theta)
                or a["iso_score"] is None
            )
            and a["outcome"] == "correct"
        )

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)

        return {
            "theta": round(theta, 2),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    def sweep_theta(self, pairs: list[dict]) -> dict:
        """Sweep theta and compute overall + per-type optimal values."""
        # Annotate each pair with its ISO score
        annotated = []
        for p in pairs:
            iso = self.compute_iso_for_query(p["query"], p["resource_id"])
            annotated.append({**p, "iso_score": iso})

        steps = np.arange(THETA_MIN, THETA_MAX + 1e-9, THETA_STEP)

        # -- Overall sweep --
        sweep = []
        best_metrics = None
        best_f1 = -1.0

        for theta in steps:
            theta = round(theta, 2)
            m = self._compute_metrics(annotated, theta)
            sweep.append(m)
            if m["f1"] > best_f1:
                best_f1 = m["f1"]
                best_metrics = m

        # -- Per-resource-type sweep --
        types_seen: dict[str, list[dict]] = {}
        for a in annotated:
            rt = a["resource_type"]
            types_seen.setdefault(rt, []).append(a)

        per_type = {}
        for rt, rt_pairs in sorted(types_seen.items()):
            best_rt_f1 = -1.0
            best_rt = None
            for theta in steps:
                theta = round(theta, 2)
                m = self._compute_metrics(rt_pairs, theta)
                if m["f1"] > best_rt_f1:
                    best_rt_f1 = m["f1"]
                    best_rt = {
                        "theta": m["theta"],
                        "precision": m["precision"],
                        "recall": m["recall"],
                        "f1": m["f1"],
                        "n_pairs": len(rt_pairs),
                    }
            if best_rt:
                per_type[rt] = best_rt

        return {
            "n_labeled_pairs": len(annotated),
            "n_with_iso": sum(1 for a in annotated if a["iso_score"] is not None),
            "n_missing_iso": sum(1 for a in annotated if a["iso_score"] is None),
            "overall_optimal_theta": best_metrics["theta"] if best_metrics else 0.28,
            "overall_optimal_metrics": {
                "precision": best_metrics["precision"] if best_metrics else 0,
                "recall": best_metrics["recall"] if best_metrics else 0,
                "f1": best_metrics["f1"] if best_metrics else 0,
            } if best_metrics else {"precision": 0, "recall": 0, "f1": 0},
            "per_type_optimal": per_type,
            "sweep": sweep,
            "pairs": [
                {
                    "log_id": a["log_id"],
                    "query": a["query"],
                    "skill_name": a["skill_name"],
                    "resource_type": a["resource_type"],
                    "iso_score": a["iso_score"],
                    "outcome": a["outcome"],
                }
                for a in annotated
            ],
        }


def print_report(result: dict):
    """Print a formatted calibration report to stdout."""
    n = result["n_labeled_pairs"]
    n_iso = result["n_with_iso"]
    n_miss = result["n_missing_iso"]

    print("=" * 60)
    print("  Tool Attention v4 — Theta Calibration Report")
    print("=" * 60)
    print(f"  Labeled pairs found:        {n}")
    print(f"  With ISO score:             {n_iso}")
    print(f"  Missing ISO (unseen skill): {n_miss}")
    if n < 20:
        print()
        print(f"  WARNING: Only {n} labeled pairs — statistically insufficient.")
        print("  Collect >= 20 pairs before trusting theta values.")
    print()

    print(f"  Optimal theta (overall):  {result['overall_optimal_theta']:.2f}")
    print(f"  Precision:                {result['overall_optimal_metrics']['precision']:.3f}")
    print(f"  Recall:                   {result['overall_optimal_metrics']['recall']:.3f}")
    print(f"  F1 Score:                 {result['overall_optimal_metrics']['f1']:.3f}")
    print()

    if result["per_type_optimal"]:
        print("  Per-Resource-Type Optimal:")
        for rt, opt in sorted(result["per_type_optimal"].items()):
            print(f"    {rt:20s}  theta={opt['theta']:.2f}  "
                  f"P={opt['precision']:.3f}  R={opt['recall']:.3f}  "
                  f"F1={opt['f1']:.3f}  (n={opt['n_pairs']})")
        print()

    # Sweep table
    print(f"  {'theta':>5}  {'TP':>3}  {'FP':>3}  {'FN':>3}  "
          f"{'Prec':>6}  {'Recall':>6}  {'F1':>6}")
    print(f"  {'-----':>5}  {'---':>3}  {'---':>3}  {'---':>3}  "
          f"{'------':>6}  {'------':>6}  {'------':>6}")
    for s in result["sweep"]:
        marker = " <--" if s["theta"] == result["overall_optimal_theta"] else ""
        print(f"  {s['theta']:5.2f}  {s['tp']:3d}  {s['fp']:3d}  {s['fn']:3d}  "
              f"{s['precision']:6.3f}  {s['recall']:6.3f}  {s['f1']:6.3f}{marker}")
    print()

    # Per-pair detail
    print("  Per-pair ISO scores:")
    for p in result["pairs"]:
        iso_str = f"{p['iso_score']:.4f}" if p["iso_score"] is not None else "  N/A  "
        label = "+" if p["outcome"] == "correct" else "-"
        print(f"    [{label}] ISO={iso_str}  "
              f"{p['skill_name'][:45]:<47} "
              f"\"{p['query'][:55]}\"")
    print()

    if n < 20:
        print("  Recommendation: Collect more labeled data (target >= 20 pairs).")
        print("  Current THETA in tool_attention_v4.py: 0.28 (paper default).")
        print("  No update recommended until sufficient data is available.")
        print()
    else:
        print(f"  Recommended THETA update: {result['overall_optimal_theta']:.2f} "
              f"(current: 0.28)")
        print()


def save_results(result: dict):
    """Write full calibration results as JSON."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[calibrate] Results written to {OUTPUT_PATH}")


def calibrate_theta() -> dict:
    """Main calibration routine. Returns full results dict."""
    cal = Calibrator()
    pairs = cal.fetch_labeled_pairs()

    if not pairs:
        result = {
            "n_labeled_pairs": 0,
            "n_with_iso": 0,
            "n_missing_iso": 0,
            "overall_optimal_theta": 0.28,
            "overall_optimal_metrics": {"precision": 0, "recall": 0, "f1": 0},
            "per_type_optimal": {},
            "sweep": [],
            "pairs": [],
            "warning": "No labeled data found in routing_log. Defaulting to theta=0.28.",
        }
        return result

    result = cal.sweep_theta(pairs)
    return result


if __name__ == "__main__":
    result = calibrate_theta()
    print_report(result)
    save_results(result)
