#!/usr/bin/env python3
"""
NEXUS Self-Learning System — Unified Learning Pipeline
========================================================

Activates all 5 learning layers:
  1. Outcome Tracking  — auto-close feedback loop after every route
  2. Thompson Sampling  — Bayesian model/resource selection that improves with use
  3. Self-Reflection    — on failure, diagnose, find alternatives, retry
  4. Auto-Invoke Retry  — automatic retry with alternatives on failure
  5. Cluster Affinity   — learn from activation patterns, adjust cluster weights

All data flows through nexus.db. No external services needed.
"""

import json, sqlite3, time, hashlib, logging, os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

# P-v2faithful (2026-06-27): faithful sample-time TS mechanisms, isolated in a
# sibling module so this file stays focused on DB I/O and the legacy path is
# untouched. See server/learning_faithful.py for the research grounding
# (Russo & Van Roy 2014 IDS; Kveton et al. 2021 Meta-TS). Lazy import inside
# _thompson_sample_resource_v2 to avoid any import cost when NEXUS_TS_V2 is off.

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / 'db' / 'nexus.db'

# ── Config — imported from centralized config ───────────────────────────
from core.config import get_learning_config
_LEARN_CFG = get_learning_config()
THOMPSON_ENABLED = _LEARN_CFG.is_thompson_routing()
CLUSTER_LEARNING = _LEARN_CFG.is_cluster_routing()
REFLECTION_ENABLED = _LEARN_CFG.is_reflection_enabled()
AUTO_INVOKE_ENABLED = _LEARN_CFG.is_auto_invoke_enabled()
MIN_SAMPLES_THOMPSON = _LEARN_CFG.min_samples_thompson
DEGRADED_THRESHOLD = _LEARN_CFG.degraded_threshold

# ── Phase 5: Coach score sync throttle ────────────────────────────────
COACH_SYNC_INTERVAL = 10  # sync every N thompson updates
_coach_sync_counter = 0

EVENT_REWARDS = {
    "code_accepted": +0.8, "code_rejected": -0.3,
    "edit_accepted": +0.7, "edit_rejected": -0.3,
    "test_passed":   +1.0, "test_failed":   -0.5,
    "build_passed":  +0.9, "build_failed":   -0.6,
    "lint_passed":   +0.5, "lint_failed":   -0.2,
    "timeout":       -0.4, "error":         -0.8,
    "chat_accepted": +0.4, "chat_rejected": -0.2,
    "task_completed": +1.0, "task_abandoned": -0.5,
}


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def check_cooldown_restorations(conn):
    """
    Find resources with status='degraded' that have been updated more than 7 days ago.
    Restore them to status='active', reset counters, and update priors to defensive config.
    """
    try:
        # Check for degraded resources with no recent usage (last_used_at > 7 days ago)
        # Uses last_used_at instead of updated_at because degradation resets updated_at to now,
        # creating a deadlock where degraded skills never qualify for time-based decay.
        degraded_rows = conn.execute("""
            SELECT id, name FROM resources 
            WHERE status = 'degraded' 
              AND (last_used_at IS NULL OR last_used_at < datetime('now', '-7 days'))
        """).fetchall()
        
        for row in degraded_rows:
            rid = row["id"]
            name = row["name"]
            logger.info(f"Restoring resource '{name}' (ID: {rid}) from cooldown.")
            # Restore status to active
            conn.execute("""
                UPDATE resources 
                SET status = 'active', times_triggered = 0, times_wrong = 0, times_correct = 0, updated_at = datetime('now')
                WHERE id = ?
            """, (rid,))
            
            # Note: model_beliefs table uses (provider, model, task_type) as composite key,
            # not resource_id. Individual resource Thompson beliefs are tracked through
            # the resources table directly (times_correct, times_wrong).
        conn.commit()
    except Exception as e:
        logger.warning(f"Cooldown restoration check failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# LAYER 1: OUTCOME TRACKING — Auto-close feedback loop
# ══════════════════════════════════════════════════════════════════════════

# Outcomes worth mirroring to outcome_log with computed EFC (arXiv:2605.29682).
# 'routed'/'pending' are intermediate routing states, not feedback — skip them.
_BRIDGED_OUTCOMES = {"correct", "wrong", "partial", "skip"}


def _bridge_outcome_log(conn, skill_name: str, outcome: str,
                        task_summary: str) -> dict | None:
    """Mirror an outcome into outcome_log with computed EFC factors.

    Closes the structural gap (P3/P8): record_outcome is the PRIMARY feedback
    path, but it previously only updated routing_log + resource counters +
    Thompson — it never wrote outcome_log, so the EFC columns stayed NULL
    for the bulk of rows (coverage was 34.8%, 1758/5057). Bridging here lifts
    coverage toward >80% without requiring the HTTP /report_outcome endpoint
    to be the only entry point.

    Mirrors scripts/auto_close_routes.py: direct INSERT with EFC factors;
    cluster name is pulled from resources.cluster and stored in the
    INTEGER-affinity cluster_id column as a string (see server/rl/outcome_log.py).

    Only bridges terminal outcomes {correct,wrong,partial,skip}. Returns the
    inserted resource_id + efc score, or None if the skill has no resource row
    or EFC computation fails (never raises — feedback must not be lost).
    """
    if outcome not in _BRIDGED_OUTCOMES:
        return None
    try:
        from server.efc import (
            compute_efc, classify_action_type,
            compute_repeat_count_1h, is_first_use_on_task,
        )
    except Exception as e:
        logger.warning(f"EFC bridge import failed: {e}")
        return None

    row = conn.execute(
        "SELECT id, cluster FROM resources WHERE name = ?", (skill_name,)
    ).fetchone()
    if not row:
        return None
    resource_id, cluster = row["id"], row["cluster"]

    task = task_summary or ""
    try:
        repeat_count = compute_repeat_count_1h(conn, task, skill_name)
        first_use = is_first_use_on_task(conn, task, skill_name)
        action_type = classify_action_type(task, [])
        factors = compute_efc(
            outcome=outcome, action_type=action_type,
            repeat_count_1h=repeat_count, is_first_use_on_task=first_use,
        )
    except Exception as e:
        logger.warning(f"EFC compute failed for {skill_name}: {e}")
        return None

    conn.execute(
        """
        INSERT INTO outcome_log
          (resource_id, cluster_id, task_intent, outcome, duration_ms,
           alternative_resource_ids, notes, session_id, created_at,
           action_type, repeat_count_1h,
           efc_informativeness, efc_validity, efc_redundancy, efc_memory, efc_score)
        VALUES (?, ?, ?, ?, 0, '[]', 'bridged:record_outcome', 'learning',
                ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (resource_id, cluster, task[:500], outcome, time.time(),
         action_type, repeat_count,
         factors["I"], factors["V"], factors["R"], factors["M"], factors["efc"]),
    )
    return {"resource_id": resource_id, "efc": round(factors["efc"], 4)}


def record_outcome(skill_name: str, outcome: str, task_summary: str = "",
                   project: str = "default", ide: str = "unknown",
                   tokens_used: int = 0, routed_id: int = None,
                   quality: float = 1.0) -> dict:
    """
    Record whether a routing outcome was correct or wrong.

    This is the PRIMARY feedback loop. Every route result should eventually
    call this to close the loop. It:
    - Updates times_triggered / times_correct / times_wrong on the resource
    - Degraded resources (>40% error rate, ≥5 uses) get status='degraded'
    - Bridges to Thompson Sampling beliefs (Layer 2) — quality-weighted
    - Triggers Self-Reflection on failure (Layer 3)
    - Updates cluster affinity (Layer 5)

    `quality` in [0,1] weights the Thompson belief update: a low-confidence
    derived verdict (e.g. perplexity gap near 0, from server/implicit_outcomes.py)
    should move the Beta posterior less than a confident one. Resource
    counters still +1 per route (they count events, not confidence). 'routed'
    outcomes must NOT call this — they carry no signal (IRC arXiv:2604.02869);
    the caller filters them before calling. Default quality=1.0 preserves
    legacy behavior for explicit human verdicts.
    """
    conn = _get_conn()
    check_cooldown_restorations(conn)
    _auto_close_routed(conn)   # P2-2: close stale routed/NULL outcomes before recording new one
    try:
        # ── 1a. Update routing_log ─────────────────────────────────────
        if routed_id:
            conn.execute("""
                UPDATE routing_log SET outcome=? WHERE id=?
            """, (outcome, routed_id))
        else:
            conn.execute("""
                INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                       matched_skills, outcome, skill_name, tokens_used, ide)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (task_summary or "outcome_reported", project, 0, "unknown", "[]",
                  outcome, skill_name, tokens_used, ide))
        
        # ── 1b. Update resource counters ───────────────────────────────
        if outcome == "correct":
            conn.execute("""
                UPDATE resources SET 
                    times_correct = times_correct + 1.0,
                    times_triggered = times_triggered + 1,
                    last_used_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE name = ?
            """, (skill_name,))
            
            # If this is a correct outcome, check if this skill resolved a recent failure on the same task
            try:
                recent_fail = conn.execute("""
                    SELECT id, resource_id, root_cause FROM reflection_failures 
                    WHERE (task = ? OR task LIKE ? OR resource_name = ?) AND fix_successful IS NULL 
                    ORDER BY id DESC LIMIT 1
                """, (task_summary or "", f"%{task_summary[:30]}%" if task_summary else "", skill_name)).fetchone()
                
                if recent_fail:
                    conn.execute("""
                        UPDATE reflection_failures 
                        SET fix_attempted = ?, fix_successful = 1 
                        WHERE id = ?
                    """, (f"Used alternative: {skill_name}", recent_fail["id"]))
                    
                    conn.execute("""
                        UPDATE reflection_fixes 
                        SET times_successful = times_successful + 1, last_applied_at = datetime('now')
                        WHERE resource_id = ? AND failure_summary = ? AND fix_description = ?
                    """, (recent_fail["resource_id"], f"Failed with {recent_fail['root_cause']}", f"Use {skill_name} instead"))
            except Exception as e:
                logger.warning(f"Failed to update reflection fix success: {e}")
        elif outcome == "partial":
            conn.execute("""
                UPDATE resources SET 
                    times_correct = times_correct + 0.5,
                    times_wrong = times_wrong + 0.5,
                    times_triggered = times_triggered + 1,
                    last_used_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE name = ?
            """, (skill_name,))
        else:
            conn.execute("""
                UPDATE resources SET 
                    times_wrong = times_wrong + 1.0,
                    times_triggered = times_triggered + 1,
                    last_used_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE name = ?
            """, (skill_name,))
        
        conn.commit()
        
        # ── 1c. Check degradation ────────────────────────────────────
        stats = conn.execute("""
            SELECT times_triggered, times_wrong, times_correct, cluster
            FROM resources WHERE name = ?
        """, (skill_name,)).fetchone()
        
        warning = None
        new_status = "active"
        if stats and stats["times_triggered"] >= 5:
            error_rate = stats["times_wrong"] / stats["times_triggered"]
            if error_rate > DEGRADED_THRESHOLD:
                new_status = "degraded"
                conn.execute("""
                    UPDATE resources SET status = 'degraded', updated_at = datetime('now')
                    WHERE name = ?
                """, (skill_name,))
                conn.commit()
                warning = f"{skill_name} degraded: {error_rate:.0%} error rate"
                logger.warning(warning)
        
        # ── 2. Bridge to Thompson Sampling ─────────────────────────────
        if THOMPSON_ENABLED:
            try:
                _update_thompson_belief(conn, skill_name, outcome, task_summary,
                                        quality=quality)
            except Exception as e:
                logger.warning(f"Thompson update failed: {e}")
        
        # ── 3. Trigger Self-Reflection on failure / high-error partial ────
        reflection_result = None
        auto_invoke_result = None
        error_rate = stats["times_wrong"] / max(stats["times_triggered"], 1) if stats else 0
        should_reflect = (
            outcome == "wrong"
            or (outcome == "partial" and error_rate > 0.30 and REFLECTION_ENABLED)
        )
        if should_reflect and REFLECTION_ENABLED:
            try:
                reflection_result = _trigger_reflection(conn, skill_name, task_summary, stats)
            except Exception as e:
                logger.warning(f"Reflection trigger failed: {e}")
        
        # ── 4. Auto-Invoke Retry on failure ──────────────────────
        if outcome == "wrong":
            try:
                auto_invoke_result = auto_invoke_retry(
                    conn, task_summary, skill_name, project
                )
                logger.info(f"Auto-invoke: {auto_invoke_result.get('status')} "
                           f"→ {auto_invoke_result.get('recommended', {}).get('name', 'none')}")
            except Exception as e:
                logger.warning(f"Auto-invoke retry failed: {e}")
        
        # ── 5. Update cluster affinity ────────────────────────────────
        if CLUSTER_LEARNING and stats:
            try:
                _update_cluster_affinity(conn, stats["cluster"] or "general",
                                         skill_name, outcome)
            except Exception as e:
                logger.warning(f"Cluster affinity update failed: {e}")

        # ── 6. Multiplicative edge weight learning ────────────────────
        # Adjust weights on graph edges connected to this resource based on outcome.
        # Multiplicative updates preserve baseline relative ordering and avoid inflation.
        if outcome in ("correct", "wrong"):
            try:
                resource_row = conn.execute(
                    "SELECT id FROM resources WHERE name = ?", (skill_name,)
                ).fetchone()
                if resource_row:
                    rid = resource_row["id"]
                    if outcome == "correct":
                        # Increase weight by 5%, capped at 1.0
                        conn.execute("""
                            UPDATE resource_edges
                            SET weight = MIN(1.0, weight * 1.05),
                                updated_at = datetime('now')
                            WHERE source_id = ? OR target_id = ?
                        """, (rid, rid))
                    else:
                        # Decrease weight by 15%, floored at 0.05
                        conn.execute("""
                            UPDATE resource_edges
                            SET weight = MAX(0.05, weight * 0.85),
                                updated_at = datetime('now')
                            WHERE source_id = ? OR target_id = ?
                        """, (rid, rid))
                    conn.commit()
            except Exception as e:
                logger.warning(f"Edge weight update failed: {e}")

        # ── 7. Bridge to outcome_log with EFC (P3/P8 structural fix) ───────
        # Primary feedback path must mirror into outcome_log with computed
        # EFC factors, else EFC coverage stays at ~34.8% (HTTP bridge only).
        outcome_log_bridge = None
        try:
            outcome_log_bridge = _bridge_outcome_log(
                conn, skill_name, outcome, task_summary
            )
            if outcome_log_bridge:
                conn.commit()
        except Exception as e:
            logger.warning(f"outcome_log bridge failed: {e}")

        conn.close()

        return {
            "status": "logged",
            "skill": skill_name,
            "outcome": outcome,
            "triggered": int(stats["times_triggered"]) if stats else 0,
            "error_rate": round(stats["times_wrong"] / max(stats["times_triggered"], 1) * 100, 1) if stats else 0,
            "resource_status": new_status,
            "warning": warning,
            "reflection": reflection_result,
            "auto_invoke": auto_invoke_result,
            "outcome_log_bridge": outcome_log_bridge,
            "layers_active": {
                "outcome_tracking": True,
                "thompson_sampling": THOMPSON_ENABLED,
                "self_reflection": reflection_result is not None,
                "auto_invoke_retry": auto_invoke_result is not None,
                "cluster_affinity": CLUSTER_LEARNING,
                "efc_outcome_log_bridge": outcome_log_bridge is not None,
            }
        }
    except Exception as e:
        conn.close()
        logger.error(f"Outcome recording failed: {e}")
        return {"status": "error", "message": str(e)}



# ══════════════════════════════════════════════════════════════════════════
# AUTO-CLOSE ROUTED — Closes stale NULL/routed outcomes
# ══════════════════════════════════════════════════════════════════════════

def _auto_close_routed(conn, max_age_hours: int = 24) -> int:
    """Auto-close stale 'routed' or NULL routing_log entries.

    DISABLED: This heuristic inflated Thompson beliefs by marking unverified
    routes as 'correct' without real outcome reporting (alpha=473, beta=15).
    Real outcomes must be reported via record_outcome() instead.
    
    Returns 0 (no-op).
    """
    return 0


# ══════════════════════════════════════════════════════════════════════════
# TS-v2 (NEXUS_TS_V2, default OFF): informative priors (RC2 / HierTS MLR 2022
# / Meta-TS arXiv:2102.06129) + SW-TS time-decay (RC3 / Trovò JAIR 2020) +
# EFC reward shaping (RC3+RC4 convergence) + Information-Directed Sampling
# (RC4 / Russo & Van Roy NeurIPS 2014, arXiv:1403.5556) + abstention gate
# (RC3 / Yang ICML 2024 arXiv:2402.15127). Decay + prior + IDS + abstention
# are READ-ONLY at sample time (use existing last_updated / times_* / alpha /
# beta); only the update path writes, with EFC-shaped increments. Default
# OFF = exact legacy behavior. No schema change, no caller change.
# ══════════════════════════════════════════════════════════════════════════

def _ts_v2_enabled() -> bool:
    return os.environ.get("NEXUS_TS_V2", "").lower() in ("1", "true", "yes")


def _ts_opt(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _cluster_prior(cluster: Optional[str], affinities: Dict[str, float],
                   lam: float) -> tuple:
    """Empirical-Bayes informative prior INSPIRED BY HierTS (Hong et al. ICML
    2022) / Meta-TS (Kveton et al. ICML 2021) — a λ-pseudo-count point-estimate
    seeding, NOT a hierarchical/meta posterior. cluster affinity → λ pseudo-
    observations. Returns (α_p, β_p) = (1+λ·aff, 1+λ·(1−aff)). Washed out as
    data accrues (Bayesian shrinkage — a property of any Beta prior). Neutral
    0.5 when cluster unknown / no data."""
    aff = affinities.get(cluster, 0.5) if cluster else 0.5
    aff = max(0.0, min(1.0, float(aff)))
    return (1.0 + lam * aff, 1.0 + lam * (1.0 - aff))


def _meta_cluster_posterior(conn, cluster: Optional[str], aff: float,
                            kappa: float) -> tuple:
    """Read the per-cluster Meta-TS meta-posterior Q_s = Beta(α_m, β_m) used to
    SAMPLE the instance prior (Kveton et al. ICML 2021). Stored in model_beliefs
    under provider='nexus', model='meta_cluster:<cluster>', task_type='__meta__'.

    READ-ONLY at sample time: if no row exists yet (no cluster outcomes
    accrued), warm-start from the affinity via meta_warm_start — the informative
    meta-prior Q_0 (Meta-TS permits any meta-prior). The row is created lazily
    by the UPDATE path in _update_thompson_belief (gated on NEXUS_TS_V2), so the
    sample path never writes and flag-OFF stays byte-identical."""
    from server.learning_faithful import meta_warm_start
    if not cluster:
        return meta_warm_start(0.5, kappa)
    row = conn.execute(
        "SELECT alpha, beta FROM model_beliefs "
        "WHERE provider='nexus' AND model=? AND task_type='__meta__'",
        (f"meta_cluster:{cluster}",)
    ).fetchone()
    if row:
        return (float(row["alpha"]), float(row["beta"]))
    return meta_warm_start(aff, kappa)


def _sw_decay(alpha: float, beta: float, last_updated, gamma: float) -> tuple:
    """Exponential time-decay (DISCOUNTED TS, NOT Sliding-Window TS / Trovò
    et al. JAIR 2020 — SW-TS uses a hard fixed window τ, this fades
    continuously): multiply stored (already-accumulated) counts by
    gamma^hours_since_last_update so stale beliefs decay toward the prior.
    Read-only on storage. No-op if last_updated unparseable."""
    try:
        if last_updated and gamma > 0.0:
            lu = last_updated
            if isinstance(lu, str):
                lu = datetime.strptime(lu[:19], "%Y-%m-%d %H:%M:%S")
            hrs = max(0.0, (datetime.utcnow() - lu).total_seconds() / 3600.0)
            d = gamma ** hrs
            if d < 1.0:
                return (alpha * d, beta * d)
    except Exception:
        pass
    return (alpha, beta)


# NOTE: _update_thompson_belief_v2 (EFC R-shaped update) was REMOVED in the
# P2 audit (2026-06-27). An offline replay benchmark proved R-shaping is
# anti-Bayesian for a bandit — it correlates increment magnitude with outcome
# direction, inflating the posterior mean (calibration 0.0549 vs 0.0243 flat;
# symmetric R worse at 0.0871 via regression-to-prior). The honest flat Beta
# update in _update_thompson_belief is the single calibration-preserving path;
# all TS-v2 intelligence lives at sample time in _thompson_sample_resource_v2.


# ══════════════════════════════════════════════════════════════════════════
# LAYER 2: THOMPSON SAMPLING — Bayesian model/resource beliefs
# ══════════════════════════════════════════════════════════════════════════

def _update_thompson_belief(conn, skill_name: str, outcome: str, task_summary: str,
                             quality: float = 1.0):
    """
    Update Beta(alpha, beta) beliefs for resource + task type combination.

    Positive outcome → increase alpha
    Negative outcome → increase beta

    Also updates model-level beliefs for the provider/model that was used.

    `quality` in [0,1] scales the belief increment: a low-confidence derived
    verdict (from server/implicit_outcomes.py, e.g. perplexity gap near 0)
    moves the Beta posterior less than a confident one. This prevents
    low-signal implicit outcomes from inflating beliefs the way the old
    _auto_close_routed heuristic did. reward is also quality-scaled so
    avg_reward stays calibrated.

    NOTE (P2 audit 2026-06-27): the belief UPDATE is intentionally the honest
    flat Beta increment (correct→α+q, partial→0.5q/0.5q, wrong→β+q per Riou
    & Honda ALT 2020 randomized binarization). An earlier TS-v2 variant shaped
    the increment by the EFC R-factor (non-redundancy), but an offline replay
    benchmark proved that R-shaping is ANTI-BAYESIAN for a bandit: it
    correlates the increment magnitude with the outcome direction (R<1 only on
    wrong/partial), inflating the posterior mean ~2.3× (calibration error
    0.0549 vs 0.0243 flat). Symmetric R was worse still (0.0871, regression-to-
    prior). ALL TS-v2 value therefore lives at SAMPLE time (cluster priors,
    SW-decay, IDS re-rank, abstention gate) in thompson_sample_resource_v2;
    the update is one calibration-preserving path. Non-redundancy's correct home
    is the outcome-recording layer (dedupe loop-repeats), not the belief update.
    """
    global _coach_sync_counter
    q = max(0.0, min(1.0, float(quality)))  # clamp

    # ── Resource-level belief ──────────────────────────────────────
    resource = conn.execute(
        "SELECT id, cluster, resource_type FROM resources WHERE name = ?",
        (skill_name,)
    ).fetchone()

    if not resource:
        return

    # Classify task type from the summary
    task_type = _classify_task_simple(task_summary or "")

    # Resource belief: (resource_name, task_type) — quality-weighted
    if outcome == "correct":
        reward = 1.0 * q
        alpha_inc, beta_inc = 1.0 * q, 0.0
    elif outcome == "partial":
        reward = 0.5 * q
        alpha_inc, beta_inc = 0.5 * q, 0.5 * q
    else:
        reward = 0.0
        alpha_inc, beta_inc = 0.0, 1.0 * q

    resource_key = f"resource:{skill_name}"
    _ensure_belief(conn, "nexus", resource_key, task_type)
    
    conn.execute("""
        UPDATE model_beliefs SET 
            alpha = alpha + ?, 
            beta = beta + ?, 
            total_requests = total_requests + 1,
            total_reward = total_reward + ?, 
            avg_reward = (total_reward + ?) / (total_requests + 1),
            last_updated = datetime('now')
        WHERE provider = 'nexus' AND model = ? AND task_type = ?
    """, (alpha_inc, beta_inc, reward, reward, resource_key, task_type))

    conn.commit()

    # ── Meta-cluster posterior (P-v2faithful; Kveton et al. ICML 2021) ──────
    # GATED on NEXUS_TS_V2 so flag-OFF performs ZERO meta writes — the live
    # legacy update path stays byte-identical (no new rows, no new commits).
    # Updates the per-cluster meta-posterior Q_s = Beta(α^meta, β^meta) from the
    # outcome so the instance prior is LEARNED online; the sample path in
    # _thompson_sample_resource_v2 only READS it. Same quality-weighted,
    # calibration-preserving flat increment as the resource belief. The row is
    # warm-started from the cluster affinity (meta_warm_start) on first outcome
    # — matching what the sample path uses before any outcomes accrue.
    if os.environ.get("NEXUS_TS_V2", "false").lower() == "true":
        cluster = resource["cluster"] if resource else None
        if cluster:
            from server.learning_faithful import meta_warm_start
            meta_key = f"meta_cluster:{cluster}"
            existing = conn.execute(
                "SELECT id FROM model_beliefs "
                "WHERE provider='nexus' AND model=? AND task_type='__meta__'",
                (meta_key,)
            ).fetchone()
            if not existing:
                aff = get_cluster_affinity_scores(conn).get(cluster, 0.5)
                mk = _ts_opt("NEXUS_TS_META_PRIOR", "10")
                am, bm = meta_warm_start(aff, mk)
                conn.execute("""
                    INSERT OR IGNORE INTO model_beliefs
                        (provider, model, task_type, alpha, beta,
                         total_requests, total_reward, avg_reward)
                    VALUES (?, ?, '__meta__', ?, ?, 0, 0.0, 0.0)
                """, ("nexus", meta_key, am, bm))
                conn.commit()
            conn.execute("""
                UPDATE model_beliefs SET
                    alpha = alpha + ?,
                    beta = beta + ?,
                    total_requests = total_requests + 1,
                    total_reward = total_reward + ?,
                    avg_reward = (total_reward + ?) / (total_requests + 1),
                    last_updated = datetime('now')
                WHERE provider = 'nexus' AND model = ? AND task_type = '__meta__'
            """, (alpha_inc, beta_inc, reward, reward, meta_key))
            conn.commit()

    # ── Phase 5: Periodically sync Coach practice scores → beliefs ──
    _coach_sync_counter += 1
    if _coach_sync_counter >= COACH_SYNC_INTERVAL:
        _coach_sync_counter = 0
        try:
            from server.coach.thompson_bridge import sync_scores_to_beliefs
            sync_scores_to_beliefs()
        except Exception:
            pass


_BELIEF_SCHEMA_CHECKED = False

def _ensure_belief(conn, provider: str, model: str, task_type: str):
    """Make sure a belief row exists."""
    global _BELIEF_SCHEMA_CHECKED
    existing = conn.execute(
        "SELECT id FROM model_beliefs WHERE provider=? AND model=? AND task_type=?",
        (provider, model, task_type)
    ).fetchone()
    if not existing:
        conn.execute("""
            INSERT OR IGNORE INTO model_beliefs (provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward)
            VALUES (?, ?, ?, 1.0, 1.0, 0, 0.0, 0.0)
        """, (provider, model, task_type))
        conn.commit()

    if not _BELIEF_SCHEMA_CHECKED:
        try:
            conn.execute("ALTER TABLE model_beliefs ADD COLUMN avg_reward REAL DEFAULT 0.0")
            # Backfill existing rows — ALTER TABLE DEFAULT does NOT apply to existing rows
            conn.execute("""
                UPDATE model_beliefs 
                SET avg_reward = CAST(total_reward AS REAL) / MAX(total_requests, 1)
                WHERE total_requests > 0
            """)
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE model_beliefs ADD COLUMN last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except sqlite3.OperationalError:
            pass
        _BELIEF_SCHEMA_CHECKED = True


def _thompson_sample_resource_v2(conn, task: str, candidates: List[Dict],
                                  task_type: str = None) -> List[Dict]:
    """TS-v2 sampling — RC2 informative priors + RC3 time-decay / abstention
    + RC4 IDS-style re-rank. Read-only on storage (the UPDATE path is the
    flat calibration-preserving Beta update in _update_thompson_belief).

    HONEST RESEARCH LABELS (updated P-v2faithful 2026-06-27):
      • Cluster priors: by default Meta-TS FAITHFUL (Kveton et al. ICML 2021,
        arXiv:2102.06129) — instance prior P_s SAMPLED from a per-cluster
        meta-posterior, updated online from outcomes. NEXUS_TS_META='empirical'
        falls back to the prior empirical-Bayes λ-pseudo-count point-estimate
        (inspired by HierTS, NOT a hierarchical posterior) for A/B.
      • Time-decay: EXPONENTIAL discounting of stored counts by γ^hours — NOT
        Sliding-Window TS (Trovò et al. JAIR 2020), which uses a hard fixed
        window τ. Relabel: 'discounted TS'. Kept as-is (schema gap: skills have
        no per-outcome timestamp log for a hard window).
      • IDS re-rank: by default FAITHFUL Russo & Van Roy NeurIPS 2014
        (arXiv:1403.5556) — Ψ=Δ²/g with canonical expected-regret Δ and mutual-
        information g, computed by Monte-Carlo in learning_faithful.ids_rank_score;
        INHERITS the IDS regret bound. NEXUS_TS_IDS_MODE='surrogate' falls back
        to the prior gap-variance proxy (NO bound; leader=0 is a patch) for A/B.
        NEXUS adaptation: canonical IDS selects one action; NEXUS returns a
        ranked list, so we rank by Ψ ascending (argmin Ψ = top = the canonical
        deterministic-IDS pick) and blend with TS+keyword.
      • Abstention: a confidence GATE/flag — NOT Yang et al. ICML 2024's
        abstention action with fixed regret c and LCB criterion. Kept as-is
        (faithful abstention needs caller-side abstain-action handling = scope creep).
    """
    import random
    if not task_type:
        task_type = _classify_task_simple(task)

    lam = _ts_opt("NEXUS_TS_PRIOR_STRENGTH", "10")
    gamma = _ts_opt("NEXUS_TS_DECAY", "0.97")
    w_ts = _ts_opt("NEXUS_TS_W_TS", "0.5")
    w_kw = _ts_opt("NEXUS_TS_W_KW", "0.3")
    w_ids = _ts_opt("NEXUS_TS_W_IDS", "0.2")
    tau = _ts_opt("NEXUS_TS_ABSTAIN_TAU", "0.35")
    abstain_info = _ts_opt("NEXUS_TS_ABSTAIN_INFO", "20")
    affinities = get_cluster_affinity_scores(conn)
    # Meta-TS prior source (P-v2faithful, 2026-06-27; Kveton et al. ICML 2021):
    #   • 'faithful' (default) — SAMPLE the instance prior P_s ~ Beta(α^meta,β^meta)
    #     drawn from the per-cluster meta-posterior, then seed the arm prior as
    #     (1+λ·P_s, 1+λ·(1−P_s)). Sampling P_s is the Meta-TS exploration
    #     mechanism; the meta-posterior is learned online in _update_thompson_belief.
    #   • 'empirical' — the prior empirical-Bayes point-estimate P_s=aff via
    #     _cluster_prior (NO Meta-TS sampling); A/B baseline for P5-bench.
    meta_mode = os.environ.get("NEXUS_TS_META", "faithful").lower()
    kappa = _ts_opt("NEXUS_TS_META_PRIOR", "10")

    # Pass 1: effective (alpha, beta) per candidate (prior + counts + decay)
    arms = []
    for cand in candidates:
        name = cand.get("name", "unknown")
        res_row = conn.execute(
            "SELECT times_correct, times_wrong, times_triggered, resource_type, cluster "
            "FROM resources WHERE name = ?", (name,)
        ).fetchone()
        cluster = res_row["cluster"] if res_row else None
        aff = affinities.get(cluster, 0.5) if cluster else 0.5
        if meta_mode == "empirical":
            a_p, b_p = _cluster_prior(cluster, affinities, lam)
        else:
            # faithful Meta-TS: sample P_s from the cluster meta-posterior and
            # seed the arm prior (read-only; _meta_cluster_posterior never writes).
            from server.learning_faithful import meta_sample_prior
            am, bm = _meta_cluster_posterior(conn, cluster, aff, kappa)
            a_p, b_p = meta_sample_prior(am, bm, lam, rng=random)
        if res_row and res_row["resource_type"] == "skill":
            alpha = a_p + (res_row["times_correct"] or 0)
            beta = b_p + (res_row["times_wrong"] or 0)
        else:
            resource_key = f"resource:{name}"
            _ensure_belief(conn, "nexus", resource_key, task_type)
            row = conn.execute(
                "SELECT alpha, beta, last_updated FROM model_beliefs "
                "WHERE provider='nexus' AND model=? AND task_type=?",
                (resource_key, task_type)
            ).fetchone()
            sa = (row["alpha"] if row else 1.0)
            sb = (row["beta"] if row else 1.0)
            lu = row["last_updated"] if row else None
            # subtract the uniform seed (1.0), decay the rest, add the cluster prior
            sa, sb = _sw_decay(max(sa - 1.0, 0.0), max(sb - 1.0, 0.0), lu, gamma)
            alpha = a_p + sa
            beta = b_p + sb
        arms.append((cand, alpha, beta))

    # IDS re-rank (P-v2faithful, 2026-06-27). Two modes via NEXUS_TS_IDS_MODE:
    #   • 'faithful' (default) — canonical Russo & Van Roy NeurIPS 2014
    #     (arXiv:1403.5556): Ψ(a)=Δ(a)²/g(a), Δ=expected regret to the unknown
    #     optimal arm A* (averaged over the posterior P(A*)), g=mutual
    #     information I(A*;Y_a). Ranked by Ψ ascending (argmin Ψ = top). The
    #     Δ/g/Ψ quantities are computed faithfully (Monte-Carlo over the Beta
    #     posteriors) in learning_faithful.ids_rank_score; this INHERITS the
    #     IDS regret bound. Feasible because K is small (~5-10) → O(K·N).
    #   • 'surrogate' — the prior gap-variance/info proxy (NO regret bound; the
    #     leader=0 rule is a divide-by-zero patch, not IDS theory). Kept as an
    #     A/B baseline for P5-bench.
    # Both produce a [0,1] ids_norm array (higher = more worth sampling) fed
    # into the same w_ts/w_kw/w_ids blend below.
    ids_mode = os.environ.get("NEXUS_TS_IDS_MODE", "faithful").lower()
    if ids_mode == "surrogate":
        means = []
        variances = []
        for _, a, b in arms:
            s = a + b
            means.append(a / s if s > 0 else 0.5)
            variances.append((a * b) / ((s * s) * (s + 1)) if s > 0 else 0.0)
        best_mean = max(means) if means else 0.5
        leader_var = 0.0
        for m, v in zip(means, variances):
            if abs(m - best_mean) < 1e-9:
                leader_var = max(leader_var, v)
        raw_ids = []
        for m, v in zip(means, variances):
            gap = max(0.0, best_mean - m)
            if gap <= 1e-9:
                info = 0.0  # the leader: sampling it teaches nothing about leadership
            else:
                info = v + leader_var  # Var(mean_i - mean_leader) for independent Betas
            raw_ids.append(info / (gap + 1e-6))
        mx = max(raw_ids) if raw_ids else 0.0
        ids_norm = [(x / mx if mx > 0 else 0.0) for x in raw_ids]
    else:
        # faithful IDS — canonical Δ/g/Ψ via Monte-Carlo over the Beta arms.
        from server.learning_faithful import ids_rank_score as _ids_rank
        alphas = [max(a, 0.01) for _, a, _ in arms]
        betas_ = [max(b, 0.01) for _, _, b in arms]
        ids_norm = _ids_rank(alphas, betas_)

    scored = []
    for (cand, a, b), idn in zip(arms, ids_norm):
        sampled = random.betavariate(max(a, 0.01), max(b, 0.01))
        keyword_score = cand.get("score", 0.5)
        combined = w_ts * sampled + w_kw * keyword_score + w_ids * idn
        cc = dict(cand)
        cc["thompson_score"] = round(sampled, 4)
        cc["combined_score"] = round(combined, 4)
        cc["belief_alpha"] = round(a, 2)
        cc["belief_beta"] = round(b, 2)
        cc["ids_score"] = round(idn, 4)
        cc["ts_v2"] = True
        scored.append(cc)

    # Abstention GATE (NOT Yang et al. ICML 2024's abstention action — see
    # docstring): flag the top result when globally low-confidence.
    # Threshold = prior floor (1+λ) + abstain_info → "low total evidence".
    if scored:
        scored.sort(key=lambda x: (
            0 if x.get("_source") == "intent" else 1,
            -x["combined_score"],
        ))
        top = scored[0]
        if top["thompson_score"] < tau and \
                (top["belief_alpha"] + top["belief_beta"]) < (1.0 + lam + abstain_info):
            top["abstain"] = True
    return scored


def thompson_sample_resource(conn, task: str, candidates: List[Dict], task_type: str = None) -> List[Dict]:
    """
    Thompson Sampling: sample from Beta(alpha, beta) for each candidate,
    sort by sampled score. This balances exploration (uncertain resources)
    with exploitation (known-good resources).

    Phase 5: Reads times_correct/times_wrong directly from resources table
    for skill-level beliefs. Falls back to model_beliefs for non-skill resources.
    """
    if _ts_v2_enabled():
        return _thompson_sample_resource_v2(conn, task, candidates, task_type)
    import random

    if not task_type:
        task_type = _classify_task_simple(task)
    
    scored = []
    for cand in candidates:
        name = cand.get("name", "unknown")
        resource_key = f"resource:{name}"
        
        # Phase 5: Read times_correct/times_wrong directly from resources
        res_row = conn.execute(
            "SELECT times_correct, times_wrong, times_triggered, resource_type FROM resources WHERE name = ?",
            (name,)
        ).fetchone()
        
        if res_row and res_row["resource_type"] == "skill":
            # Skill-level belief: use actual outcome data from resources table
            tc = res_row["times_correct"] or 0
            tw = res_row["times_wrong"] or 0
            alpha = 1.0 + tc
            beta = 1.0 + tw
        else:
            # Fallback to model_beliefs for non-skill resources
            _ensure_belief(conn, "nexus", resource_key, task_type)
            row = conn.execute(
                "SELECT alpha, beta FROM model_beliefs WHERE provider='nexus' AND model=? AND task_type=?",
                (resource_key, task_type)
            ).fetchone()
            if row:
                alpha, beta = row["alpha"], row["beta"]
            else:
                alpha, beta = 1.0, 1.0
        
        # Sample from Beta distribution
        sampled = random.betavariate(max(alpha, 0.01), max(beta, 0.01))
        
        # Blend with keyword match score (70% Thompson, 30% keyword)
        keyword_score = cand.get("score", 0.5)
        combined = sampled * 0.7 + keyword_score * 0.3
        
        cand_copy = dict(cand)
        cand_copy["thompson_score"] = round(sampled, 4)
        cand_copy["combined_score"] = round(combined, 4)
        cand_copy["belief_alpha"] = round(alpha, 2)
        cand_copy["belief_beta"] = round(beta, 2)
        scored.append(cand_copy)
    
    # Preserve intent _source priority through re-rank (Phase C: task-type routing)
    scored.sort(key=lambda x: (
        0 if x.get("_source") == "intent" else 1,
        -x["combined_score"],
    ))
    return scored


# ══════════════════════════════════════════════════════════════════════════
# LAYER 3: SELF-REFLECTION — Diagnose failures, find alternatives
# ══════════════════════════════════════════════════════════════════════════

def _trigger_reflection(conn, failed_skill: str, task_summary: str, 
                        stats: Optional[sqlite3.Row] = None) -> Optional[dict]:
    """
    When a resource is marked wrong, analyze the failure and find alternatives.
    
    Based on AnyTool self-reflection:
    1. Log the failure to reflection_failures
    2. Classify the error type using execution logs or trace notes if available
    3. Find alternative resources in the same cluster
    4. Populate reflection_fixes table with proposed alternative fixes
    5. Return alternatives with reasoning
    """
    # ── Log failure ────────────────────────────────────────────────
    resource_id = None
    cluster = "general"
    if stats:
        resource_id = stats["id"] if "id" in stats.keys() else None
        cluster = stats["cluster"] or "general"
    
    # Get resource ID from name if not in stats
    if not resource_id:
        row = conn.execute("SELECT id, cluster FROM resources WHERE name=?", (failed_skill,)).fetchone()
        if row:
            resource_id = row["id"]
            cluster = row["cluster"] or "general"
    
    # Classify the error (retrieve latest execution error trace for this resource)
    error_log = task_summary or ""
    try:
        # Check tool_traces table for any failure notes/errors
        trace_row = conn.execute("""
            SELECT notes FROM tool_traces 
            WHERE resource_name = ? 
              AND (status = 'failed' OR outcome = 'failed' OR outcome = 'wrong' OR notes LIKE '%error%' OR notes LIKE '%fail%')
            ORDER BY id DESC LIMIT 1
        """, (failed_skill,)).fetchone()
        if trace_row and trace_row["notes"]:
            error_log += " | Log: " + trace_row["notes"]
    except Exception as e:
        logger.warning(f"Failed to query tool_traces: {e}")

    error_type = _classify_error(error_log)
    
    conn.execute("""
        INSERT INTO reflection_failures (task, resource_name, resource_id, expected, actual, root_cause)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (task_summary or "unknown", failed_skill, resource_id or 0,
          "correct routing", "wrong routing", error_type))
    conn.commit()
    
    # ── Find alternatives ──────────────────────────────────────────
    alternatives = _find_alternatives(conn, failed_skill, cluster, task_summary)
    
    # Populate reflection_fixes table with proposed alternative fixes
    if resource_id and alternatives:
        for alt in alternatives:
            alt_name = alt["name"]
            # Check if this fix already exists in reflection_fixes
            row_fix = conn.execute("""
                SELECT id, times_applied FROM reflection_fixes 
                WHERE resource_id = ? AND failure_summary = ? AND fix_description = ?
            """, (resource_id, f"Failed with {error_type}", f"Use {alt_name} instead")).fetchone()
            
            if row_fix:
                conn.execute("""
                    UPDATE reflection_fixes 
                    SET times_applied = times_applied + 1, last_applied_at = datetime('now')
                    WHERE id = ?
                """, (row_fix["id"],))
            else:
                conn.execute("""
                    INSERT INTO reflection_fixes (resource_id, failure_summary, fix_description, times_applied, times_successful)
                    VALUES (?, ?, ?, 1, 0)
                """, (resource_id, f"Failed with {error_type}", f"Use {alt_name} instead"))
        conn.commit()

    return {
        "failed_skill": failed_skill,
        "error_type": error_type,
        "cluster": cluster,
        "alternatives": alternatives[:5],
        "action": "retry_with_alternatives" if alternatives else "no_alternatives_found"
    }


def _classify_error(log: str) -> str:
    """Classify error from execution logs and error messages."""
    log_lower = log.lower()
    if any(kw in log_lower for kw in ["null", "none", "nil", "nonetype"]):
        return "null_reference"
    elif any(kw in log_lower for kw in ["import", "module", "not found", "no module named"]):
        return "missing_dependency"
    elif any(kw in log_lower for kw in ["permission", "forbidden", "auth", "access denied", "not permission"]):
        return "permission_denied"
    elif any(kw in log_lower for kw in ["timeout", "timed out", "hang", "deadline"]):
        return "timeout"
    elif any(kw in log_lower for kw in ["syntax", "parse", "format", "indentationerror"]):
        return "syntax_error"
    elif any(kw in log_lower for kw in ["type", "cast", "conversion", "typeerror"]):
        return "type_mismatch"
    elif any(kw in log_lower for kw in ["connection", "host", "port", "network", "socket"]):
        return "network_error"
    else:
        return "general_failure"


def _find_alternatives(conn, failed_skill: str, cluster: str, task: str) -> List[dict]:
    """
    Find alternative resources in the same cluster with better success rates.
    """
    # Find resources in the same cluster, excluding the failed one,
    # sorted by success rate (times_correct / times_triggered)
    rows = conn.execute("""
        SELECT name, resource_type, description, 
               times_triggered, times_correct, times_wrong,
               ROUND(COALESCE(times_correct * 1.0 / NULLIF(times_triggered, 0), 0), 3) as success_rate
        FROM resources
        WHERE cluster = ? 
          AND name != ?
          AND status = 'active'
          AND times_triggered > 0
        ORDER BY success_rate DESC, times_correct DESC
        LIMIT 10
    """, (cluster, failed_skill)).fetchall()
    
    # If no used alternatives in cluster, try broader search
    if not rows:
        rows = conn.execute("""
            SELECT name, resource_type, description,
                   times_triggered, times_correct, times_wrong,
                   ROUND(COALESCE(times_correct * 1.0 / NULLIF(times_triggered, 0), 0), 3) as success_rate
            FROM resources
            WHERE name != ?
              AND status = 'active'
            ORDER BY times_triggered DESC, times_correct DESC
            LIMIT 10
        """, (failed_skill,)).fetchall()
    
    return [{
        "name": r["name"],
        "type": r["resource_type"],
        "description": r["description"],
        "success_rate": r["success_rate"],
        "uses": r["times_triggered"],
    } for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# LAYER 4: AUTO-INVOKE — Retry with alternatives on failure
# ══════════════════════════════════════════════════════════════════════════

def auto_invoke_retry(conn, original_task: str, failed_skill: str, 
                      project: str = "default", max_retries: int = 3) -> dict:
    """
    Auto-invoke: when a skill fails, find an alternative and retry.
    
    Based on Reflexion + AnyTool: max 3 retries with different resources.
    Each failed attempt triggers reflection (Layer 3) to find better alternatives.
    """
    from router import find_matching_skills
    
    attempts = []
    tried = [failed_skill]
    
    for attempt in range(max_retries):
        # Find alternatives excluding tried skills
        alternatives = find_matching_skills(original_task, project, filtered_ids=None)
        
        # Filter out already-tried skills
        fresh = [a for a in alternatives if a["name"] not in tried]
        
        if not fresh:
            # No more alternatives — log final failure
            _log_auto_invoke(conn, original_task, tried, "exhausted", None)
            return {
                "status": "exhausted",
                "attempts": len(attempts),
                "tried": tried,
                "message": f"No more alternatives after {attempt} retries"
            }
        
        # Thompson Sampling to pick best alternative (Layer 2)
        if THOMPSON_ENABLED:
            fresh = thompson_sample_resource(conn, original_task, fresh)
        
        best = fresh[0]
        tried.append(best["name"])
        attempts.append({
            "attempt": attempt + 1,
            "skill": best["name"],
            "score": best.get("score", 0),
            "thompson_score": best.get("thompson_score"),
            "combined_score": best.get("combined_score"),
        })
        
        # In production, we'd actually invoke the skill here.
        # For now, return the recommendation and let the caller invoke.
        if best.get("score", 0) >= 0.3:
            _log_auto_invoke(conn, original_task, tried, "alternative_found", best["name"])
            return {
                "status": "alternative_found",
                "attempts": attempts,
                "recommended": best,
                "tried": tried,
                "message": f"After {attempt + 1} retries, found alternative: {best['name']}"
            }
    
    return {
        "status": "exhausted",
        "attempts": attempts,
        "tried": tried,
        "message": "Max retries exhausted"
    }


def _log_auto_invoke(conn, task, tried, status, recommended):
    """Log auto-invoke attempt to routing_log."""
    conn.execute("""
        INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                               matched_skills, outcome, skill_name, ide)
        VALUES(?,?,?,?,?,?,?,?)
    """, (f"auto_invoke_{status}: {task[:180]}", "auto_invoke", 0, "unknown",
          json.dumps(tried), "partial", recommended or "none",
          "auto_invoke"))
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════
# LAYER 5: CLUSTER AFFINITY — Learn from activation patterns
# ══════════════════════════════════════════════════════════════════════════

def _update_cluster_affinity(conn, cluster_name: str, skill_name: str, outcome: str):
    """
    After each outcome, adjust the cluster's affinity score.
    
    Positive outcome → increase affinity for this cluster on matching queries
    Negative outcome → decrease affinity slightly
    
    This makes NEXUS learn which clusters are most useful and rank them higher.
    """
    # ── 5a. Log the affinity change ────────────────────────────────
    # Get current cluster stats — graded: include `partial` (credit 0.5) so
    # the 1611 partial rows seed priors instead of being dropped (P2 dep).
    cluster_stats = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN outcome='correct' THEN 1.0
                        WHEN outcome='partial'  THEN 0.5
                        ELSE 0.0 END) as correct
        FROM routing_log
        WHERE skill_name = ? AND outcome IN ('correct', 'partial', 'wrong')
    """, (skill_name,)).fetchone()
    
    # Calculate new affinity
    total = cluster_stats["total"] if cluster_stats else 0
    correct = cluster_stats["correct"] if cluster_stats else 0
    
    if total > 0:
        new_affinity = correct / total  # 0.0 to 1.0
    else:
        new_affinity = 0.5  # neutral default
    
    # ── 5b. Update cluster_definition boost ────────────────────────
    # Find the cluster's current boost level and adjust
    cluster_row = conn.execute("""
        SELECT id, trigger_keywords FROM cluster_definitions WHERE name = ?
    """, (cluster_name,)).fetchone()
    
    if cluster_row and outcome == "correct":
        # Get current resource count in cluster
        resource_count = conn.execute("""
            SELECT COUNT(*) FROM resources WHERE cluster = ? AND status = 'active'
        """, (cluster_name,)).fetchone()[0]
        
        # Log the affinity history for auditing
        conn.execute("""
            INSERT INTO cluster_affinity_history(cluster_name, trigger_query, old_affinity, new_affinity, reason)
            VALUES(?, ?, ?, ?, ?)
        """, (cluster_name, skill_name, 0.5, new_affinity,
              f"outcome={outcome}, total={total}, correct={correct}"))
        conn.commit()


def get_cluster_affinity_scores(conn) -> Dict[str, float]:
    """
    Compute learned affinity scores for each cluster based on outcome history.
    Returns dict: cluster_name → affinity (0.0 to 1.0)
    """
    clusters = conn.execute("SELECT name FROM cluster_definitions").fetchall()
    affinities = {}
    
    for (cluster_name,) in clusters:
        # Calculate from routing outcomes — graded: include `partial` (credit
        # 0.5) so the 1611 partial rows seed priors instead of being dropped.
        # This is the hard dependency for P2 informative priors (α/β seeding).
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN rl.outcome = 'correct' THEN 1.0
                         WHEN rl.outcome = 'partial'  THEN 0.5
                         ELSE 0.0 END) as correct
            FROM routing_log rl
            JOIN resources r ON rl.skill_name = r.name
            WHERE r.cluster = ? AND rl.outcome IN ('correct', 'partial', 'wrong')
        """, (cluster_name,)).fetchone()
        
        total = row["total"] if row else 0
        correct = row["correct"] if row else 0
        
        if total >= 3:
            affinities[cluster_name] = round(correct / total, 3)
        else:
            affinities[cluster_name] = 0.5  # neutral — not enough data
    
    return affinities


# ══════════════════════════════════════════════════════════════════════════
# UTILITY: Task Classification
# ══════════════════════════════════════════════════════════════════════════

_embedder = None

def _classify_task_simple(task: str) -> str:
    """Classify task type using centroid semantic similarity with keyword fallback."""
    global _embedder
    task_lower = task.lower()
    
    # Try semantic classification
    try:
        from server.iso_embedder import IsoEmbedder
        import numpy as np
        
        if _embedder is None:
            _embedder = IsoEmbedder.get()
        
        qvec = _embedder.encode(task)
        qnorm = qvec / (np.linalg.norm(qvec) + 1e-10)
        
        # Connect to DB to fetch centroids
        conn = _get_conn()
        centroids = conn.execute(
            "SELECT name, centroid_embedding FROM cluster_definitions WHERE centroid_embedding IS NOT NULL"
        ).fetchall()
        conn.close()
        
        best_cluster = "general"
        best_score = -1.0
        for row in centroids:
            cname = row["name"]
            cblob = row["centroid_embedding"]
            c_arr = np.frombuffer(cblob, dtype=np.float32)
            if len(c_arr) == 384:
                c_norm = c_arr / (np.linalg.norm(c_arr) + 1e-10)
                score = float(np.dot(qnorm, c_norm))
                if score > best_score:
                    best_score = score
                    best_cluster = cname
                    
        CLUSTER_TO_TASK = {
            "analyzer_planner": "reasoning",
            "architect": "architecture",
            "frontend_ui": "frontend",
            "backend_api": "coding",
            "devops_infra": "devops",
            "quality_security": "research",
            "knowledge_wiki": "general",
            "general": "general"
        }
        task_type = CLUSTER_TO_TASK.get(best_cluster, "general")
        
        # Agentic boost
        agentic_kw = ["agent", "swarm", "autonomous", "autopilot", "loop", "runs", "executes", "orchestrate", "pipeline"]
        if task_type in ["reasoning", "coding", "general"] and any(kw in task_lower for kw in agentic_kw):
            task_type = "agentic"
            
        return task_type
    except Exception as e:
        # Fallback to keyword heuristics
        coding_kw = ["code", "implement", "fix", "bug", "function", "class", "method", "debug", "refactor", "test", "compile"]
        reasoning_kw = ["analyze", "explain", "why", "compare", "evaluate", "reason", "logic"]
        devops_kw = ["deploy", "ship", "build", "ci", "docker", "release", "infra"]
        frontend_kw = ["ui", "component", "css", "html", "dashboard", "design"]
        
        if any(kw in task_lower for kw in coding_kw):
            return "coding"
        elif any(kw in task_lower for kw in reasoning_kw):
            return "reasoning"
        elif any(kw in task_lower for kw in devops_kw):
            return "devops"
        elif any(kw in task_lower for kw in frontend_kw):
            return "frontend"
        return "general"


# ══════════════════════════════════════════════════════════════════════════
# HEALTH / STATS — Monitor learning pipeline
# ══════════════════════════════════════════════════════════════════════════

def get_learning_stats() -> dict:
    """Get comprehensive learning pipeline statistics."""
    conn = _get_conn()
    try:
        stats = {
            "layers": {
                "outcome_tracking": {"enabled": True, "description": "Auto-close feedback loop after routing"},
                "thompson_sampling": {"enabled": THOMPSON_ENABLED, "description": "Bayesian resource selection (Beta α,β beliefs)"},
                "self_reflection": {"enabled": REFLECTION_ENABLED, "description": "On failure: diagnose, find alternatives, retry"},
                "auto_invoke": {"enabled": AUTO_INVOKE_ENABLED, "description": "Automatic retry with alternatives on failure"},
                "cluster_affinity": {"enabled": CLUSTER_LEARNING, "description": "Learn from activation patterns, adjust cluster weights"},
            },
            "outcomes": {},
            "thompson": {},
            "reflection": {},
            "cluster_affinity": {},
            "resources": {},
        }
        
        # ── Outcome stats ──────────────────────────────────────────
        # Graded-relevance reporting (Järvelin & Kekäläinen 2002; Deliu 2024):
        # the legacy binary `accuracy = correct/(correct+wrong)` silently dropped
        # all `partial` outcomes (graded between correct and wrong), inflating
        # the headline number. We keep it as `binary_accuracy_excluding_partial`
        # for back-compat and add `weighted_accuracy` which credits partial at
        # half weight: (correct + 0.5*partial) / (correct+partial+wrong).
        total = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
        correct = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='correct'").fetchone()[0]
        partial = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='partial'").fetchone()[0]
        wrong = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome='wrong'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome NOT IN ('correct','partial','wrong')").fetchone()[0]

        terminal = correct + partial + wrong
        weighted_acc = (correct + 0.5 * partial) / max(terminal, 1) * 100

        stats["outcomes"] = {
            "total_routes": total,
            "correct": correct,
            "partial": partial,
            "wrong": wrong,
            "pending_or_routed": pending,
            "close_rate": round(correct / max(total, 1) * 100, 1),
            "binary_accuracy_excluding_partial": round(correct / max(correct + wrong, 1) * 100, 1),
            "weighted_accuracy": round(weighted_acc, 1),
        }
        
        # ── Thompson stats ─────────────────────────────────────────
        belief_count = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
        high_confidence = conn.execute("SELECT COUNT(*) FROM model_beliefs WHERE total_requests >= 10").fetchone()[0]
        
        stats["thompson"] = {
            "enabled": THOMPSON_ENABLED,
            "beliefs_tracked": belief_count,
            "high_confidence_beliefs": high_confidence,
            "min_samples_for_routing": MIN_SAMPLES_THOMPSON,
            "top_beliefs": [],
        }
        
        if belief_count > 0:
            rows = conn.execute("""
                SELECT provider, model, task_type, 
                       ROUND(alpha, 2) as alpha, ROUND(beta, 2) as beta,
                       total_requests, ROUND(avg_reward, 3) as avg_reward
                FROM model_beliefs ORDER BY total_requests DESC LIMIT 10
            """).fetchall()
            stats["thompson"]["top_beliefs"] = [dict(r) for r in rows]
        
        # ── Reflection stats ────────────────────────────────────────
        failures = conn.execute("SELECT COUNT(*) FROM reflection_failures").fetchone()[0]
        # Count distinct failure events (resource_id + failure_summary) instead of all
        # fix-suggestion rows. This prevents inflated fix_rate caused by counting every
        # alternative suggestion as a separate fix (e.g. 70 fixes from 15 failures = 467%).
        fix_events = conn.execute("""
            SELECT COUNT(DISTINCT resource_id || '::' || COALESCE(failure_summary, ''))
            FROM reflection_fixes
        """).fetchone()[0]
        
        stats["reflection"] = {
            "enabled": REFLECTION_ENABLED,
            "total_failures": failures,
            "total_fixes": fix_events,
            "fix_rate": round(fix_events / max(failures, 1) * 100, 1),
            "recent_failures": [],
        }
        
        if failures > 0:
            recent = conn.execute("""
                SELECT task, resource_name, root_cause, created_at 
                FROM reflection_failures ORDER BY created_at DESC LIMIT 5
            """).fetchall()
            stats["reflection"]["recent_failures"] = [dict(r) for r in recent]
        
        # ── Cluster affinity stats ──────────────────────────────────
        affinities = get_cluster_affinity_scores(conn)
        stats["cluster_affinity"] = {
            "enabled": CLUSTER_LEARNING,
            "learned_scores": affinities,
            "history_entries": conn.execute("SELECT COUNT(*) FROM cluster_affinity_history").fetchone()[0],
        }
        
        # ── Resource health ──────────────────────────────────────────
        total_resources = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
        degraded = conn.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
        with_usage = conn.execute("SELECT COUNT(*) FROM resources WHERE times_triggered > 0 AND status='active'").fetchone()[0]
        
        stats["resources"] = {
            "total_active": total_resources,
            "degraded": degraded,
            "with_usage_data": with_usage,
            "coverage": round(with_usage / max(total_resources, 1) * 100, 1),
        }
        
        conn.close()
        return stats
    except Exception as e:
        conn.close()
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# ROUTING INTEGRATION — Wire into the routing pipeline
# ══════════════════════════════════════════════════════════════════════════

def enhance_routing_result(route_result: dict, task: str, project: str) -> dict:
    """
    Post-process a routing result to add learning enhancements.
    Called from route_task() after the initial routing completes.
    
    1. Thompson Samples the skill candidates to blend exploration/exploitation
    2. Applies learned cluster affinities to boost relevant clusters
    3. Returns enriched result with learning metadata
    """
    conn = _get_conn()
    try:
        skills = route_result.get("skills", [])
        
        # ── Layer 2: Thompson Sampling re-rank ──────────────────────
        if THOMPSON_ENABLED and skills:
            skills = thompson_sample_resource(conn, task, skills,
                                              route_result.get("task_type", "general"))
            route_result["skills"] = skills[:5]  # top 5 after Thompson
        
        # ── Layer 5: Cluster affinity boost ─────────────────────────
        if CLUSTER_LEARNING:
            affinities = get_cluster_affinity_scores(conn)
            cluster = route_result.get("cluster")
            if cluster and cluster in affinities:
                route_result["cluster_affinity"] = affinities[cluster]
                # Boost skills from high-affinity clusters
                for skill in route_result.get("skills", []):
                    # Check if skill belongs to the matched cluster
                    skill_row = conn.execute(
                        "SELECT cluster FROM resources WHERE name=?", (skill.get("name"),)
                    ).fetchone()
                    if skill_row and skill_row["cluster"] == cluster:
                        affinity_boost = affinities.get(cluster, 0.5) * 0.1
                        skill["combined_score"] = skill.get("combined_score", skill.get("score", 0)) + affinity_boost
        
        # ── Phase P3: Orchestrator tools integration ────────────────
        try:
            from server.orchestrator_tools import resolve_orchestrators
            orch_tools = resolve_orchestrators(task, max_results=3)
            if orch_tools:
                route_result["orchestrator_tools"] = orch_tools
                # Inject orchestrator tools as fallback skills if routing found very few
                existing_names = {s.get("name") for s in route_result.get("skills", [])}
                if len(existing_names) < 2:
                    for ot in orch_tools:
                        if ot["name"] not in existing_names:
                            route_result.setdefault("skills", []).append({
                                "name": ot["name"],
                                "score": 0.4,
                                "category": "REVIEW",
                                "success_rate": 0,
                                "type": ot["type"],
                                "_source": "orchestrator",
                            })
                            existing_names.add(ot["name"])
        except Exception:
            pass
        
        # ── Tag which learning layers are active ────────────────────
        route_result["learning"] = {
            "thompson_sampling": THOMPSON_ENABLED,
            "self_reflection": REFLECTION_ENABLED,
            "auto_invoke": AUTO_INVOKE_ENABLED,
            "cluster_affinity": CLUSTER_LEARNING,
            "outcome_tracking": True,
            "orchestrator_integration": bool(route_result.get("orchestrator_tools")),
        }
        
        conn.close()
        return route_result
    except Exception as e:
        conn.close()
        logger.warning(f"Learning enhancement failed: {e}")
        route_result["learning"] = {"error": str(e)}
        return route_result