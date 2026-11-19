"""S6 — telemetry watchdog, SHADOW MODE (DISTRACTION-RESISTANT-CONTEXT.md §3).

Batch auditor over routing_log telemetry. Robust per-intent-group baselines
(median + MAD) of latency_ms, tokens_used, injected_ctx_len; a route is
anomalous when >=2 of 3 features exceed |z| > 3 (robust z).

Detour signature is Google's measured one (Agent Distraction, 913k sessions):
wait x~2.05, tokens x~1.77 — a stale-reference detour is a latency+token
outlier against the route's own group.

HARD CONTRACT (design + plan C10): this module writes ONLY to
watchdog_events. It never touches outcomes, beliefs, rewards, or any table
that feeds learning. Shadow mode until a separate design proves attribution
and false-positive handling.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURES = ("latency_ms", "tokens_used", "injected_ctx_len")
Z_THRESHOLD = 3.0
MIN_GROUP = 8  # groups smaller than this use the global baseline


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS watchdog_events ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    route_id TEXT NOT NULL,"
        "    anomaly TEXT NOT NULL,"
        "    ts TEXT NOT NULL,"
        "    detail TEXT"
        ")"
    )


def _robust_baseline(rows: list[sqlite3.Row]) -> dict:
    """Median + MAD per feature; MAD==0 falls back to IQR, then stddev."""
    import statistics

    base = {}
    for f in FEATURES:
        vals = [float(r[f]) for r in rows if r[f] is not None]
        if len(vals) < 3:
            continue
        med = statistics.median(vals)
        devs = [abs(v - med) for v in vals]
        mad = statistics.median(devs)
        if mad == 0:
            q = sorted(vals)
            iqr = q[min(len(q) * 3 // 4, len(q) - 1)] - q[len(q) // 4]
            mad = iqr / 1.349 if iqr else None
        if not mad or mad <= 0:
            sd = statistics.pstdev(vals)
            mad = sd if sd > 0 else None
        if mad and mad > 0:
            base[f] = (med, mad)
    return base


def _group_key(row: sqlite3.Row) -> str:
    return (row["intent"] or "unknown") if row["intent"] else "unknown"


def audit_routing_log(
    con: sqlite3.Connection,
    *,
    window: int = 3500,
    write_events: bool = True,
) -> dict:
    """Scan the last `window` routing_log rows; return anomaly stats.

    Writes ONLY to watchdog_events (if write_events). Fail-open on any error
    in the row loop (a bad row is skipped, never crashes the audit).
    """
    _ensure_table(con)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"SELECT id, task_summary, intent, latency_ms, tokens_used, "
        f"injected_ctx_len FROM routing_log ORDER BY id DESC LIMIT {int(window)}"
    ).fetchall()
    if not rows:
        return {"scanned": 0}

    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault(_group_key(r), []).append(r)
    global_base = _robust_baseline(rows)
    baselines = {}
    for key, grows in groups.items():
        baselines[key] = _robust_baseline(grows) if len(grows) >= MIN_GROUP \
            else global_base

    def z(base: dict, row: sqlite3.Row, f: str) -> float | None:
        if f not in base:
            return None
        v = row[f]
        if v is None:
            return None
        med, mad = base[f]
        return (float(v) - med) / (mad * 1.4826)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    anomalies = 0
    for r in rows:
        try:
            base = baselines[_group_key(r)]
            zs = {f: z(base, r, f) for f in FEATURES}
            flagged = [f for f, zv in zs.items()
                       if zv is not None and abs(zv) > Z_THRESHOLD]
            if len(flagged) >= 2:
                anomalies += 1
                if write_events:
                    con.execute(
                        "INSERT INTO watchdog_events(route_id, anomaly, ts, detail) "
                        "VALUES(?,?,?,?)",
                        (str(r["id"]), "telemetry_outlier", now,
                         f"features={{{', '.join(f'{f}:{zs[f]:.1f}' for f in flagged)}}} "
                         f"intent={_group_key(r)} task={str(r['task_summary'])[:60]}"),
                    )
        except Exception:
            continue
    if write_events:
        con.commit()
    return {"scanned": len(rows), "anomalies": anomalies,
            "groups": len(groups), "group_min": min(
                (len(g) for g in groups.values()), default=0)}


def construct_detour(row: sqlite3.Row, factor: float = 2.05) -> dict:
    """A synthetic detour copy of a healthy row with Google's signature:
    wait x2.05 (+105%), tokens x1.77 (+77%). ctx left untouched."""
    return {
        "id": f"{row['id']}d",
        "task_summary": row["task_summary"],
        "intent": row["intent"],
        "latency_ms": (row["latency_ms"] or 0) * factor,
        "tokens_used": int((row["tokens_used"] or 0) * 1.77),
        "injected_ctx_len": row["injected_ctx_len"],
    }


def evaluate_detector(
    con: sqlite3.Connection,
    *,
    n_detours: int = 100,
    seed: int = 0,
) -> dict:
    """Acceptance harness: inject n constructed detours among the healthy
    baseline; report detection + FPR with Wilson CI. Report-only (no gate,
    no writes)."""
    import math
    import random

    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, task_summary, intent, latency_ms, tokens_used, "
        "injected_ctx_len FROM routing_log ORDER BY id DESC LIMIT 3500"
    ).fetchall()
    healthy = [r for r in rows if (r["latency_ms"] or 0) > 0]
    rng = random.Random(seed)
    detour_rows = rng.sample(healthy, min(n_detours, len(healthy)))
    detours = [construct_detour(r) for r in detour_rows]

    groups: dict[str, list] = {}
    for r in healthy:
        groups.setdefault(_group_key(r), []).append(r)
    global_base = _robust_baseline(healthy)
    baselines = {k: (_robust_baseline(g) if len(g) >= MIN_GROUP else global_base)
                 for k, g in groups.items()}

    def _is_anomalous(row: dict) -> bool:
        base = baselines.get(_group_key(row), global_base)
        cnt = 0
        for f in FEATURES:
            if f not in base or row.get(f) is None:
                continue
            med, mad = base[f]
            zv = (float(row[f]) - med) / (mad * 1.4826)
            if abs(zv) > Z_THRESHOLD:
                cnt += 1
        return cnt >= 2

    detected = sum(1 for d in detours if _is_anomalous(d))
    fps = sum(1 for r in healthy[-300:] if _is_anomalous({
        "intent": r["intent"], "latency_ms": r["latency_ms"],
        "tokens_used": r["tokens_used"],
        "injected_ctx_len": r["injected_ctx_len"]}))

    def wilson(k: int, n: int) -> tuple[float, float]:
        if n == 0:
            return (0.0, 0.0)
        p = k / n
        z = 1.96
        d = 1 + z * z / n
        c = (p + z * z / (2 * n)) / d
        h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
        return (max(0.0, c - h), min(1.0, c + h))

    n = len(detours)
    d_lo, d_hi = wilson(detected, n)
    f_lo, f_hi = wilson(fps, min(300, len(healthy)))
    return {
        "detours": n, "detected": detected,
        "detection_rate": round(detected / n, 3) if n else 0.0,
        "detection_ci": (round(d_lo, 3), round(d_hi, 3)),
        "healthy_checked": min(300, len(healthy)),
        "false_positives": fps,
        "fpr": round(fps / min(300, len(healthy)), 3) if healthy else 0.0,
        "fpr_ci": (round(f_lo, 3), round(f_hi, 3)),
    }
