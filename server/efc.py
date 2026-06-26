"""
efc.py — Effective Feedback Compute (EFC) computation for NEXUS routings.

Reference: arXiv:2605.29682 (Zhang, Wang, Xu, Zhu, Che, May 2026)
"Scaling Laws for Agent Harnesses via Effective Feedback Compute"

Implements a tractable approximation of the paper's EFC and NRS-EFC formulas,
using only data we already collect (outcome_log + routing_log + resources).

EFC event-level (Eq. 4):
    EFC_t = κ · I_t · V_t · R_t · M_t
    κ = 10 (paper)

    I_t = informativeness    — was this feedback signal useful?
    V_t = validity           — was it correct?
    R_t = non-redundancy     — was it novel (not a repeat)?
    M_t = memory-write       — did it update long-term memory?

Run-level (Eq. 5):
    EFC(τ) = κ · Σ_t I_t · V_t · R_t · M_t

NRS-EFC real-trace version (Eqs. 9-10):
    NRS-EFC_t = EFC_t · Q^nr · G^nr · Λ^nr / (1 + α_A · A_t)
    α_A = 0.35 (paper, redundancy penalty)

NEXUS approximations (where paper features aren't available):
  - I: outcome='correct' → 1.0, 'partial' → 0.5, 'wrong' → 0.0, 'pending' → 0.2
  - V: 1 - (outcome='wrong')*0.5  (partial credit for partial outcomes)
  - R: 1 - min(repeat_count_1h / 10, 0.9)  (heavy penalty for repeats)
  - M: 1.0 if first time this (task_summary, skill_name) pair, else 0.5
  - A_t: action_type-specific redundancy (paper H1-H6 mappings)

These are *coarse* proxies. The paper calibrates θ from real closed-loop
traces; NEXUS doesn't have those yet. The point of computing these now is
to (a) populate the schema with EFC factors so future routings get logged,
and (b) seed the model_beliefs / thompson priors with feedback-quality
weighting instead of raw outcome counts.

If/when NEXUS acquires a closed-loop calibration set, swap these
heuristics for the paper's θ·φ(e_t) estimator.
"""

from __future__ import annotations

KAPPA = 10.0  # paper Eq. 4
ALPHA_A = 0.35  # paper Eq. 10, redundancy penalty
REPEAT_SATURATION = 10  # paper-implicit; how many repeats saturate R to 0.1

# Action-type specific redundancy weights (paper H1-H6 mappings, coarse).
# In the paper, Htool gets the largest weight; we mirror that.
ACTION_REDUNDANCY = {
    "tool_call": 1.0,
    "verifier": 0.6,
    "reflection": 0.8,
    "repair": 0.4,  # repairs are usually non-redundant
    "rollout_summary": 0.7,
    "candidate_comparison": 0.5,
    "coordination": 0.9,
    None: 1.0,  # default
}

# Status-quality weights (paper Eq. 28: 1.00 passed ... 0.00 API error).
STATUS_QUALITY = {
    "correct": 1.0,
    "partial": 0.5,
    "wrong": 0.0,
    "pending": 0.2,
    "routed": 0.5,  # routed-but-not-yet-verified
}


def compute_efc(
    outcome: str,
    action_type: str | None = None,
    repeat_count_1h: int = 0,
    is_first_use_on_task: bool = False,
) -> dict:
    """Compute EFC factors for a single routing outcome.

    Returns dict with keys: I, V, R, M, A_t, Q_nr, G_nr, efc, nrs_efc.
    """
    # I: informativeness — was this useful signal?
    i_t = STATUS_QUALITY.get(outcome, 0.0)
    # Special case: correct → 1.0 (always informative when correct)
    if outcome == "correct":
        i_t = 1.0
    elif outcome == "wrong":
        i_t = 0.6  # paper: wrong but informative (negative signal is signal)
    elif outcome == "partial":
        i_t = 0.5

    # V: validity — was the verdict correct? Proxy via outcome + repeat pressure.
    v_t = 1.0 if outcome == "correct" else (0.7 if outcome == "partial" else 0.3)

    # R: non-redundancy — how novel is this signal?
    # Paper Eq. 31: G^nr = 0.16 for repeated failures, 0.10 for regressions.
    # We use a continuous version: clamp(repeat_count / 10, 0.1, 1.0)
    r_t = max(1.0 - repeat_count_1h / REPEAT_SATURATION, 0.1)

    # M: memory-write — first-use on task = strong signal; repeat = weaker
    m_t = 1.0 if is_first_use_on_task else 0.5

    # A_t: action-type redundancy (paper Htool weight is largest)
    a_t = ACTION_REDUNDANCY.get(action_type, 1.0)

    # EFC event-level
    efc = KAPPA * i_t * v_t * r_t * m_t

    # NRS-EFC: paper Eq. 10 with α_A = 0.35
    # Q^nr: status quality = STATUS_QUALITY[outcome]
    q_nr = STATUS_QUALITY.get(outcome, 0.0)
    # G^nr: gate factor — paper uses 0.16 for repeated failures
    # We use a continuous version: 1.0 → 0.1 as repeat_count grows
    g_nr = max(1.0 - repeat_count_1h * 0.09, 0.1)
    # Λ^nr: progress gate — paper 0.45 for repair events; 1.0 otherwise
    lambda_nr = 0.45 if action_type == "repair" else 1.0

    nrs_efc = efc * q_nr * g_nr * lambda_nr / (1.0 + ALPHA_A * a_t)

    return {
        "I": round(i_t, 4),
        "V": round(v_t, 4),
        "R": round(r_t, 4),
        "M": round(m_t, 4),
        "A_t": round(a_t, 4),
        "Q_nr": round(q_nr, 4),
        "G_nr": round(g_nr, 4),
        "efc": round(efc, 4),
        "nrs_efc": round(nrs_efc, 4),
    }


def compute_repeat_count_1h(conn, task_summary: str, skill_name: str) -> int:
    """Count of (task_summary, skill_name) pairs in last 1 hour (excluding current).

    Used as feature ρ_t in the paper (Eq. 6).
    """
    row = conn.execute("""
        SELECT COUNT(*) FROM routing_log
        WHERE task_summary = ?
          AND skill_name = ?
          AND created_at >= datetime('now', '-1 hour')
    """, (task_summary, skill_name)).fetchone()
    return int(row[0]) if row else 0


def is_first_use_on_task(conn, task_summary: str, skill_name: str) -> bool:
    """True if (task_summary, skill_name) pair has not been routed before."""
    row = conn.execute("""
        SELECT COUNT(*) FROM routing_log
        WHERE task_summary = ? AND skill_name = ?
    """, (task_summary, skill_name)).fetchone()
    count = int(row[0]) if row else 0
    return count == 0


def classify_action_type(task_summary: str, matched_skills: list[str]) -> str:
    """Heuristic classification of a routing into one of EFC's action categories.

    Paper H1-H6 distinguishes: tool_call, verifier, reflection, repair,
    rollout_summary, candidate_comparison, coordination.
    """
    if not task_summary:
        return "tool_call"
    t = task_summary.lower()
    if any(p in t for p in ("verify:", "check ", "audit:", "review:")):
        return "verifier"
    if any(p in t for p in ("reflect", "self-improve", "retro:")):
        return "reflection"
    if any(p in t for p in ("repair", "fix", "remediate", "patch:")):
        return "repair"
    if any(p in t for p in ("summary", "rollout", "recap")):
        return "rollout_summary"
    if any(p in t for p in ("compare", "vs", "alternative")):
        return "candidate_comparison"
    if any(p in t for p in ("coordinate", "delegate", "spawn")):
        return "coordination"
    return "tool_call"


def efc_normalized_for_ranking(efc_score: float, max_efc: float = 100.0) -> float:
    """Normalize EFC to [0, 1] for blending into combined_score.

    Theoretical max is κ=10 per event (I=V=R=M=1, A=0); for ~10 events/run
    EFC can reach 100. We cap at 100 for ranking purposes.
    """
    if max_efc <= 0:
        return 0.0
    return min(efc_score / max_efc, 1.0)
