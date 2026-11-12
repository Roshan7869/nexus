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

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Config — imported from centralized config ───────────────────────────
from core.config import get_learning_config
from server.db import get_db_path as _resolve_db_path

# Single source of truth for the DB location (server/db.py:39). A hardcoded
# path here ignored NEXUS_DB_PATH, so the test suite wrote to the live DB.
DB_PATH = _resolve_db_path()
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
    from server.db import get_connection
    return get_connection(timeout=60)


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
              AND (
                (last_used_at IS NULL OR last_used_at < datetime('now', '-7 days'))
                OR (updated_at < datetime('now', '-7 days'))
              )
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
            
            # Reset belief priors defensively for restored resource
            try:
                conn.execute("""
                    UPDATE model_beliefs SET alpha = 1.0, beta = 3.0
                    WHERE model = ?
                """, (f"resource:{name}",))
            except Exception:
                pass
            
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


# Terminal outcomes may update counters / Thompson / edges / affinity.
# Non-terminal labels are telemetry only — never invent learning signal.
TERMINAL_OUTCOMES = frozenset({"correct", "wrong", "partial", "skip"})
NON_TERMINAL_OUTCOMES = frozenset({"routed", "pending", "null", ""})


_SCHEMA_OUTCOMES = {"correct", "wrong", "partial", "pending", "routed", "skip"}
# Non-terminal states written by swarm fixes (unverified, dispatched, executed,
# blocked) are not in the routing_log CHECK constraint, so they are telemetry
# recorded as "routed". They never touch counters (see TERMINAL_OUTCOMES).
_UNOBSERVED_LABELS = {"unverified", "dispatched", "executed", "blocked"}


def _normalize_outcome_label(outcome: str | None) -> str:
    if outcome is None:
        return "routed"
    label = str(outcome).strip().lower()
    if label in ("none", "null", "nil"):
        return "routed"
    if label in _UNOBSERVED_LABELS:
        return "routed"
    return label


def _parse_skill_list(raw: Any) -> List[str]:
    """Normalize matched_skills / tools_json into a unique name list."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        names = [str(x).strip() for x in raw if str(x).strip()]
    else:
        s = str(raw).strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                names = [str(x).strip() for x in parsed if str(x).strip()]
            else:
                names = [p.strip() for p in s.split(",") if p.strip()]
        except Exception:
            names = [p.strip() for p in s.split(",") if p.strip()]
    # preserve order, unique
    seen: set[str] = set()
    out: List[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def learn_co_triggers(
    conn: sqlite3.Connection,
    skill_names: List[str],
    outcome: str,
    *,
    min_pair: int = 2,
) -> dict:
    """Gap 3: upsert co_triggered edges for multi-skill terminal outcomes.

    On correct/partial: increment co_trigger_count and raise weight.
    On wrong: mild weight decay (still record co-occurrence so the pair is known).
    Edges are undirected in practice via ordered (min_id, max_id) storage.
    """
    names = _parse_skill_list(skill_names)
    if len(names) < min_pair:
        return {"pairs": 0, "reason": "need_at_least_two_skills"}
    if outcome not in TERMINAL_OUTCOMES or outcome == "skip":
        return {"pairs": 0, "reason": "non_terminal"}

    rows = conn.execute(
        f"SELECT id, name FROM resources WHERE name IN ({','.join('?' * len(names))}) "
        f"AND status IN ('active','degraded')",
        names,
    ).fetchall()
    id_by_name = {r["name"]: r["id"] for r in rows}
    ids = [id_by_name[n] for n in names if n in id_by_name]
    if len(ids) < 2:
        return {"pairs": 0, "reason": "skills_not_in_catalog", "resolved": len(ids)}

    # ordered unique pairs
    pairs = 0
    upserted = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if a == b:
                continue
            src, tgt = (a, b) if a < b else (b, a)
            pairs += 1
            existing = conn.execute(
                "SELECT id, weight, co_trigger_count FROM resource_edges "
                "WHERE source_id=? AND target_id=? AND edge_type='co_triggered'",
                (src, tgt),
            ).fetchone()
            if outcome == "correct":
                w_mul, count_delta = 1.08, 1
            elif outcome == "partial":
                w_mul, count_delta = 1.03, 1
            else:  # wrong
                w_mul, count_delta = 0.92, 1

            if existing:
                new_w = max(0.05, min(1.0, float(existing["weight"]) * w_mul))
                new_c = int(existing["co_trigger_count"] or 0) + count_delta
                conn.execute(
                    "UPDATE resource_edges SET weight=?, co_trigger_count=?, "
                    "updated_at=datetime('now') WHERE id=?",
                    (new_w, new_c, existing["id"]),
                )
            else:
                base = 0.55 if outcome == "correct" else (0.45 if outcome == "partial" else 0.35)
                conn.execute(
                    "INSERT INTO resource_edges"
                    "(source_id, target_id, edge_type, weight, co_trigger_count) "
                    "VALUES (?,?, 'co_triggered', ?, ?)",
                    (src, tgt, base, count_delta),
                )
            upserted += 1
    return {"pairs": pairs, "upserted": upserted, "skills": len(ids), "outcome": outcome}


def edge_learning_stats(conn: sqlite3.Connection | None = None) -> dict:
    """Gap 3 ops metrics for co-trigger graph learning."""
    owns = conn is None
    if owns:
        conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM resource_edges").fetchone()["n"]
        co = conn.execute(
            "SELECT COUNT(*) AS n FROM resource_edges WHERE edge_type='co_triggered'"
        ).fetchone()["n"]
        co_live = conn.execute(
            "SELECT COUNT(*) AS n FROM resource_edges "
            "WHERE edge_type='co_triggered' AND COALESCE(co_trigger_count,0) > 0"
        ).fetchone()["n"]
        similar = conn.execute(
            "SELECT COUNT(*) AS n FROM resource_edges WHERE edge_type='similar_to'"
        ).fetchone()["n"]
        avg_w = conn.execute(
            "SELECT AVG(weight) AS a FROM resource_edges WHERE edge_type='co_triggered'"
        ).fetchone()["a"]
        return {
            "edges_total": total,
            "co_triggered": co,
            "co_triggered_with_count": co_live,
            "similar_to": similar,
            "co_triggered_avg_weight": round(float(avg_w or 0), 4),
            # Gate: graph learning path has live co-occurrence signal
            "edge_learning_gate_pass": co_live >= 10 or co >= 50,
        }
    finally:
        if owns:
            try:
                conn.close()
            except Exception:
                pass


def record_outcome(skill_name: str, outcome: str, task_summary: str = "",
                   project: str = "default", ide: str = "unknown",
                   tokens_used: int = 0, routed_id: int = None,
                   quality: float = 1.0,
                   co_skills: List[str] | None = None,
                   outcome_source: str = "explicit") -> dict:
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
    counters still +1 per route (they count events, not confidence).

    Non-terminal outcomes {'routed','pending',NULL/empty} carry no learning
    signal (IRC arXiv:2604.02869 / P0 design plan). They may update
    routing_log telemetry only — never counters, Thompson, edges, or affinity.
    Default quality=1.0 preserves legacy behavior for explicit human verdicts.

    `outcome_source` ∈ {'explicit','derived','timeout'} (G12 audit marker) is
    persisted on the routing_log row so a derived/auto-closed verdict is
    distinguishable from an explicit human one. Default 'explicit' keeps every
    legacy caller correctly tagged. It is metadata only — it does not alter any
    counter/Thompson/degradation behavior.
    """
    outcome = _normalize_outcome_label(outcome)

    # Sanitize ide for routing_log CHECK constraint
    _IDE_OK = {
        "opencode", "claude-code", "unknown", "hermes", "codex",
        "mcp", "auto_invoke", "http", "eval", "pytest",
    }
    ide_raw = (ide or "unknown").strip().lower()
    ide = ide_raw if ide_raw in _IDE_OK else "unknown"

    # ── P0: non-terminal early return (no invented learning) ──────────
    # 'partial' and 'skip' (timeouts) are accepted by close_session (TERMINAL_OUTCOMES) but
    # must not move counters, beliefs or edges from record_outcome: telemetry only.
    # Only observed 'correct' and 'wrong' outcomes learn.
    if outcome not in TERMINAL_OUTCOMES or outcome in ("partial", "skip"):
        conn = _get_conn()
        try:
            if routed_id:
                conn.execute(
                    "UPDATE routing_log SET outcome=?, outcome_source=?, quality=? WHERE id=?",
                    (outcome if outcome else "routed", outcome_source, quality, routed_id),
                )
            else:
                conn.execute("""
                    INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                           matched_skills, outcome, skill_name, tokens_used, ide,
                                           outcome_source, quality)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (task_summary or "outcome_reported", project, 0, "unknown", "[]",
                      outcome or "routed", skill_name, tokens_used, ide, outcome_source, quality))
            conn.commit()
            return {
                "status": "logged_non_terminal",
                "skill": skill_name,
                "outcome": outcome or "routed",
                "learning_applied": False,
                "reason": "non_terminal_outcome_no_belief_update",
            }
        except Exception as e:
            logger.warning(f"Non-terminal routing_log write failed: {e}")
            return {
                "status": "logged_non_terminal",
                "skill": skill_name,
                "outcome": outcome or "routed",
                "learning_applied": False,
                "error": str(e),
            }
        finally:
            try:
                conn.close()
            except Exception:
                pass

    conn = _get_conn()
    check_cooldown_restorations(conn)
    try:
        # ── 1a. Update routing_log ─────────────────────────────────────
        if routed_id:
            conn.execute("""
                UPDATE routing_log SET outcome=?, outcome_source=?, quality=? WHERE id=?
            """, (outcome, outcome_source, quality, routed_id))
        else:
            conn.execute("""
                INSERT INTO routing_log(task_summary, project, matched_tier, matched_model,
                                       matched_skills, outcome, skill_name, tokens_used, ide,
                                       outcome_source, quality)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (task_summary or "outcome_reported", project, 0, "unknown", "[]",
                  outcome, skill_name, tokens_used, ide, outcome_source, quality))
        
        # ── 1b. Update resource counters (Discrete integer rewards) ─────
        is_correct = outcome == "correct" or (outcome != "wrong" and quality is not None and quality >= 0.5)
        is_wrong = outcome == "wrong" or (outcome != "correct" and quality is not None and quality < 0.5)

        if is_correct:
            conn.execute("""
                UPDATE resources SET 
                    times_correct = times_correct + 1,
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
        elif is_wrong:
            conn.execute("""
                UPDATE resources SET
                    times_wrong = times_wrong + 1,
                    times_triggered = times_triggered + 1,
                    last_used_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE name = ?
            """, (skill_name,))

        # ── 1d. Clamp invariant: times_correct ≤ times_triggered ─────────
        # Defensive guard against success-rate > 100% (sr = corr/trig).
        # Enforce strict integer types and cap times_correct to times_triggered.
        conn.execute("""
            UPDATE resources
            SET times_correct = CAST(ROUND(MIN(times_correct, times_triggered)) AS INTEGER),
                times_wrong = CAST(ROUND(times_wrong) AS INTEGER),
                times_triggered = CAST(ROUND(times_triggered) AS INTEGER)
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
                try:
                    from server.communication.notifier import Notifier
                    Notifier().send_degraded_skill_alert(
                        skill_name, warning, severity="P0"
                    )
                except Exception:
                    pass
        
        # ── 2. Bridge to Thompson Sampling ─────────────────────────────
        # quality None = not measured. An unmeasured outcome moves no belief.
        if THOMPSON_ENABLED and quality is not None:
            try:
                _update_thompson_belief(conn, skill_name, outcome, task_summary,
                                        quality=quality)
            except Exception as e:
                logger.warning(f"Thompson update failed: {e}")
        
        # ── 3. Trigger Self-Reflection on failure / high-error partial ────
        reflection_result = None
        auto_invoke_result = None
        co_trigger_result = None
        error_rate = stats["times_wrong"] / max(stats["times_triggered"], 1) if stats else 0
        should_reflect = (
            is_wrong
            or (outcome == "partial" and error_rate > 0.30 and REFLECTION_ENABLED)
        )
        if should_reflect and REFLECTION_ENABLED:
            try:
                reflection_result = _trigger_reflection(conn, skill_name, task_summary, stats)
            except Exception as e:
                logger.warning(f"Reflection trigger failed: {e}")
        
        # ── 4. Auto-Invoke Retry on failure ──────────────────────
        if is_wrong and AUTO_INVOKE_ENABLED:
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
                # Decay stale cluster affinities periodically
                global _affinity_decay_counter
                _affinity_decay_counter += 1
                if _affinity_decay_counter >= _DECAY_INTERVAL:
                    _apply_affinity_decay(conn)
                    _affinity_decay_counter = 0

                _update_cluster_affinity(conn, stats["cluster"] or "general",
                                          skill_name, "correct" if is_correct else ("wrong" if is_wrong else outcome))
            except Exception as e:
                logger.warning(f"Cluster affinity update failed: {e}")

        # ── 6. Multiplicative edge weight learning ────────────────────
        # Adjust weights on graph edges connected to this resource based on outcome.
        # Multiplicative updates preserve baseline relative ordering and avoid inflation.
        if is_correct or is_wrong:
            try:
                resource_row = conn.execute(
                    "SELECT id FROM resources WHERE name = ?", (skill_name,)
                ).fetchone()
                if resource_row:
                    rid = resource_row["id"]
                    if is_correct:
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

        # ── 6b. Gap 3: co-trigger learning from multi-skill bundles ────
        # Weight-only updates never create co_triggered edges; historical
        # co_trigger_count stayed 0. Build pairs from co_skills, routed_id
        # matched_skills, or explicit skill_name alone (no-op if single).
        co_trigger_result = None
        try:
            bundle: List[str] = []
            if skill_name:
                bundle.append(skill_name)
            if co_skills:
                bundle.extend(_parse_skill_list(co_skills))
            if routed_id:
                row_ms = conn.execute(
                    "SELECT matched_skills FROM routing_log WHERE id=?",
                    (routed_id,),
                ).fetchone()
                if row_ms and row_ms["matched_skills"]:
                    bundle.extend(_parse_skill_list(row_ms["matched_skills"]))
            if len(_parse_skill_list(bundle)) >= 2:
                co_trigger_result = learn_co_triggers(conn, bundle, "correct" if is_correct else ("wrong" if is_wrong else outcome))
                conn.commit()
        except Exception as e:
            logger.warning(f"co-trigger learning failed: {e}")

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
            "co_triggers": co_trigger_result,
            "layers_active": {
                "outcome_tracking": True,
                "thompson_sampling": THOMPSON_ENABLED,
                "self_reflection": reflection_result is not None,
                "auto_invoke_retry": auto_invoke_result is not None,
                "cluster_affinity": CLUSTER_LEARNING,
                "efc_outcome_log_bridge": outcome_log_bridge is not None,
                "co_trigger_edges": co_trigger_result is not None
                and (co_trigger_result or {}).get("upserted", 0) > 0,
            }
        }
    except Exception as e:
        conn.close()
        logger.error(f"Outcome recording failed: {e}")
        return {"status": "error", "message": str(e)}



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
    low-signal implicit outcomes from inflating beliefs. reward is also
    quality-scaled so avg_reward stays calibrated.
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


def thompson_sample_resource(conn, task: str, candidates: List[Dict], task_type: str = None) -> List[Dict]:
    """
    Thompson Sampling: sample from Beta(alpha, beta) for each candidate,
    sort by sampled score. This balances exploration (uncertain resources)
    with exploitation (known-good resources).

    Phase 5: Reads times_correct/times_wrong directly from resources table
    for skill-level beliefs. Falls back to model_beliefs for non-skill resources.
    """
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
            # Resolve the alternative to a resource id so the router can join
            # directly (migration 017) instead of parsing "Use X instead" text.
            alt_id_row = conn.execute(
                "SELECT id FROM resources WHERE name=?", (alt_name,)
            ).fetchone()
            alt_id = alt_id_row["id"] if alt_id_row else None
            # Check if this fix already exists in reflection_fixes
            row_fix = conn.execute("""
                SELECT id, times_applied FROM reflection_fixes
                WHERE resource_id = ? AND failure_summary = ? AND fix_description = ?
            """, (resource_id, f"Failed with {error_type}", f"Use {alt_name} instead")).fetchone()

            if row_fix:
                conn.execute("""
                    UPDATE reflection_fixes
                    SET times_applied = times_applied + 1, last_applied_at = datetime('now'),
                        alternative_resource_id = COALESCE(alternative_resource_id, ?)
                    WHERE id = ?
                """, (alt_id, row_fix["id"],))
            else:
                conn.execute("""
                    INSERT INTO reflection_fixes
                        (resource_id, failure_summary, fix_description, times_applied,
                         times_successful, alternative_resource_id)
                    VALUES (?, ?, ?, 1, 0, ?)
                """, (resource_id, f"Failed with {error_type}", f"Use {alt_name} instead", alt_id))
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
    
    if cluster_row:
        # Get current resource count in cluster
        resource_count = conn.execute("""
            SELECT COUNT(*) FROM resources WHERE cluster = ? AND status = 'active'
        """, (cluster_name,)).fetchone()[0]

        # B1: read actual prior affinity (not hardcoded 0.5)
        prior = conn.execute("""
            SELECT new_affinity FROM cluster_affinity_history
            WHERE cluster_name = ?
            ORDER BY id DESC LIMIT 1
        """, (cluster_name,)).fetchone()
        old_affinity = float(prior["new_affinity"]) if prior else 0.5

        # B2: log an affinity history row for ALL outcomes, not just success — so the system also
        # learns which clusters are BAD. The written value is the Beta(1,1) posterior mean over the
        # cluster's graded outcomes, NOT a saturating additive nudge.
        # RCA-F5(learning.py:1139): FIXED — the old fixed ±0.05 additive delta saturated at the [0,1]
        #   clamp under ~90% correct outcomes → flatline. Replaced with the Laplace-smoothed Beta mean
        #   (correct+1)/(total+2), which asymptotes to the true success ratio and never pins at 1.0
        #   (Chapelle & Li 2011). Aligns this write column with the live read path
        #   (get_cluster_affinity_scores also uses correct/total). See docs/rca/F5-affinity-flatline.md
        beta_affinity = (correct + 1.0) / (total + 2.0)
        delta = beta_affinity - old_affinity  # retained for the audit reason string only

        conn.execute("""
            INSERT INTO cluster_affinity_history(cluster_name, trigger_query, old_affinity, new_affinity, reason)
            VALUES(?, ?, ?, ?, ?)
        """, (cluster_name, skill_name, old_affinity,
              round(beta_affinity, 4),
              f"outcome={outcome}, total={total}, correct={correct}, beta={beta_affinity:.4f}, delta={delta:+.4f}"))
        conn.commit()


# ── Affinity decay counter (module-level, incremented each record_outcome) ──
_affinity_decay_counter = 0
_DECAY_INTERVAL = 50  # apply decay every N outcome recordings
_DECAY_RATE = 0.001   # pull toward neutral by 0.1% per step
_DECAY_STALE_HOURS = 24  # only decay clusters not updated in >24h


def _apply_affinity_decay(conn):
    """
    Apply exponential decay to cluster affinities for clusters not updated
    in over ``_DECAY_STALE_HOURS`` hours. Unused clusters drift toward 0.5
    (neutral), creating differentiation between active vs. stale clusters.
    
    Fixes the audit finding that all 8 clusters had flatlined at 0.75-1.0
    with zero differentiation.
    """
    from datetime import datetime

    clusters = conn.execute("SELECT name FROM cluster_definitions").fetchall()
    for (cluster_name,) in clusters:
        latest = conn.execute("""
            SELECT new_affinity, created_at FROM cluster_affinity_history
            WHERE cluster_name = ?
            ORDER BY id DESC LIMIT 1
        """, (cluster_name,)).fetchone()
        if not latest:
            continue
        try:
            created = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
            hours_since = (datetime.utcnow().replace(tzinfo=created.tzinfo) - created).total_seconds() / 3600
        except (ValueError, TypeError):
            continue
        
        if hours_since > _DECAY_STALE_HOURS:
            # RCA-F5(learning.py:1188): decay (0.1%/step, every 50 outcomes, >24h stale) is far too
            #   weak to counteract a +0.05 ratchet on active clusters. Prefer a γ-discounted sufficient
            #   statistic (Garivier & Moulines 2011). See docs/rca/F5-affinity-flatline.md
            current = latest["new_affinity"]
            # Pull toward neutral (0.5) — the further from 0.5, the harder the pull
            new = current - _DECAY_RATE * (current - 0.5)
            new = max(0.0, min(1.0, new))
            conn.execute("""
                INSERT INTO cluster_affinity_history
                (cluster_name, trigger_query, old_affinity, new_affinity, reason)
                VALUES(?, ?, ?, ?, ?)
            """, (cluster_name, "decay", round(current, 4), round(new, 4),
                  f"decay: {hours_since:.0f}h inactive"))
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
        
        # ── Tag which learning layers are active ────────────────────
        route_result["learning"] = {
            "thompson_sampling": THOMPSON_ENABLED,
            "self_reflection": REFLECTION_ENABLED,
            "auto_invoke": AUTO_INVOKE_ENABLED,
            "cluster_affinity": CLUSTER_LEARNING,
            "outcome_tracking": True,
        }
        
        conn.close()
        return route_result
    except Exception as e:
        conn.close()
        logger.warning(f"Learning enhancement failed: {e}")
        route_result["learning"] = {"error": str(e)}
        return route_result