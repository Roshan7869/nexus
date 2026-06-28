"""Faithful sample-time TS mechanisms (paper2code extraction, P-v2faithful, 2026-06-27).

Two mechanisms promoted from heuristic-surrogate to faithful implementations,
isolated here so learning.py stays focused on DB I/O and the legacy path is
untouched. All functions are PURE (no DB, no globals): they take per-arm Beta
beliefs and return selections / priors. The caller (learning.py) owns storage.

1. Information-Directed Sampling (IDS) — Russo & Van Roy, NeurIPS 2014
   (arXiv:1403.5556). The canonical bandit selector minimizing the information-
   regret ratio  Ψ(a) = Δ(a)² / g(a):
     • Δ(a) = E[μ_A* − μ_a | H_t]  — expected regret of playing a, with A*
       the unknown optimal arm averaged over the posterior P(A* | H_t).
     • g(a) = I(A* ; Y_a | H_t)     — mutual information between the optimal-arm
       identity and the reward of playing a, computed as the expected KL shift:
       Σ_y P(Y_a=y) · KL( P(A*|Y_a=y) ‖ P(A*|H_t) ).
   The posterior over A* and the per-outcome posterior shifts are estimated by
   Monte Carlo (N = IDS_MC_SAMPLES). Feasible because K is small (~5-10
   candidates) → O(K · N) per arm. The regret bound is inherited from the
   CANONICAL algorithm (NOT from the gap-variance surrogate this replaces —
   the surrogate had no such bound; see the P2 audit).

   NEXUS adaptation (honest): canonical IDS SELECTS a single action (or a
   2-action mixture). NEXUS returns a RANKED LIST, so we rank arms by Ψ
   ascending — the IDS preference order — and the top arm is argmin Ψ, the
   canonical deterministic-IDS pick. The 2-action mixture is the single-pull
   form; for the ranked-list interface the preference order is the faithful
   projection. This is documented in learning.py's v2 docstring.

2. Meta-Thompson Sampling priors — Kveton et al., ICML 2021 (arXiv:2102.06129).
   Each cluster is a meta-instance with an unknown average success rate P*_s.
   A meta-posterior Q_s = Beta(α_s^meta, β_s^meta) is maintained per cluster
   (warm-started from the cluster affinity — an informative meta-prior Q_0,
   which Meta-TS permits). At sample time the instance prior is SAMPLED:
   P_s ~ Beta(α_s^meta, β_s^meta), then seeds each arm's prior as
   (1 + λ·P_s, 1 + λ·(1−P_s)). Sampling P_s (vs the prior empirical-Bayes
   point-estimate P_s = aff) is the core Meta-TS exploration mechanism; the
   meta-posterior is updated from outcomes so the cluster rate is LEARNED
   online. The meta-posterior STORAGE/UPDATE lives in learning.py (DB I/O);
   this module only does the sampling math.

NOT promoted (kept honest labels in learning.py per the P2 audit):
  • SW-TS (Trovò et al. JAIR 2020) — needs per-skill outcome timestamps; skills
    only have aggregate times_correct/wrong (schema gap). Exponential decay stays.
  • Abstention arm (Yang et al. ICML 2024) — needs caller-side abstain-action
    handling + fixed regret c (scope creep). Confidence gate stays.
"""
from __future__ import annotations

import math
import random
from typing import List, Sequence, Tuple

# Monte-Carlo sample count for the posterior-over-A* and the per-outcome
# posterior shifts. K≤~10, so N=200 gives ~±7% standard error on a 0.5
# probability — enough for a selection signal; raise via env for tighter edges.
IDS_MC_SAMPLES = int(__import__("os").environ.get("NEXUS_TS_IDS_MC", "200"))
_EPS = 1e-12


def _beta_mean(a: float, b: float) -> float:
    s = a + b
    return (a / s) if s > 0 else 0.5


def _sample_thetas(alphas: Sequence[float], betas: Sequence[float],
                   n: int, rng: random.Random) -> List[List[float]]:
    """Draw n joint samples of the per-arm Beta posteriors (one θ per arm per draw)."""
    return [[rng.betavariate(max(a, 0.01), max(b, 0.01)) for a, b in zip(alphas, betas)]
            for _ in range(n)]


def posterior_a_star(alphas: Sequence[float], betas: Sequence[float],
                     n_samples: int = IDS_MC_SAMPLES,
                     rng: random.Random | None = None) -> Tuple[List[float], List[float]]:
    """P(A* = b | H_t) — the posterior probability each arm is optimal — by
    Monte Carlo: sample θ_b ~ Beta(α_b, β_b) for all arms, count argmax wins.
    Returns (P_A_star, mu) where mu_b is the posterior mean of arm b."""
    rng = rng or random.Random()
    K = len(alphas)
    if K == 0:
        return [], []
    counts = [0] * K
    for thetas in _sample_thetas(alphas, betas, n_samples, rng):
        m = max(range(K), key=lambda k: thetas[k])
        counts[m] += 1
    P = [c / n_samples for c in counts]
    mu = [_beta_mean(a, b) for a, b in zip(alphas, betas)]
    return P, mu


def _kl_categorical(p: Sequence[float], q: Sequence[float]) -> float:
    """KL(p ‖ q) for two categorical distributions over the same support."""
    kl = 0.0
    for pi, qi in zip(p, q):
        if pi <= 0.0:
            continue
        kl += pi * math.log((pi + _EPS) / (qi + _EPS))
    return kl


def mutual_information(alphas: List[float], betas: List[float],
                       P_A: Sequence[float], mu: Sequence[float],
                       n_samples: int = IDS_MC_SAMPLES,
                       rng: random.Random | None = None) -> List[float]:
    """g(a) = I(A* ; Y_a | H_t) = Σ_y P(Y_a=y) · KL( P(A*|Y_a=y) ‖ P(A*) ).

    For each arm a and outcome y∈{0,1}: the predictive P(Y_a=y) is μ_a (y=1) /
    (1−μ_a) (y=0); the posterior-after-observation P(A*|Y_a=y) recomputes the
    argmax-win frequencies with arm a's Beta incremented by the outcome. The KL
    of that shifted posterior from the prior P(A*) is the information gain.
    """
    rng = rng or random.Random()
    K = len(alphas)
    g = [0.0] * K
    for a_idx in range(K):
        for y in (1, 0):
            p_y = mu[a_idx] if y == 1 else (1.0 - mu[a_idx])
            if p_y <= 0.0:
                continue
            a2 = list(alphas)
            b2 = list(betas)
            if y == 1:
                a2[a_idx] += 1.0
            else:
                b2[a_idx] += 1.0
            P_post, _ = posterior_a_star(a2, b2, n_samples, rng)
            g[a_idx] += p_y * _kl_categorical(P_post, P_A)
    return g


def expected_regret(mu: Sequence[float], P_A: Sequence[float]) -> List[float]:
    """Δ(a) = E[μ_A*] − μ_a = (Σ_b P(A*=b) · μ_b) − μ_a. Non-negative; 0 for the
    posterior-optimal arm."""
    e_best = sum(pb * mb for pb, mb in zip(P_A, mu)) if P_A else 0.5
    return [e_best - m for m in mu]


def ids_psi(alphas: List[float], betas: List[float],
            n_samples: int = IDS_MC_SAMPLES,
            rng: random.Random | None = None) -> Tuple[List[float], List[float], List[float]]:
    """Canonical IDS ratios. Returns (Psi, Delta, g):
        Psi[a] = Delta[a]² / g[a]   (minimize — least regret per bit of info)
    with the corner cases: Delta=0 → Psi=0 (already optimal, no info needed);
    g=0 & Delta>0 → Psi=+inf (no info from this arm but regret — avoid)."""
    P_A, mu = posterior_a_star(alphas, betas, n_samples, rng)
    Delta = expected_regret(mu, P_A)
    g = mutual_information(alphas, betas, P_A, mu, n_samples, rng)
    Psi = []
    for d, gi in zip(Delta, g):
        if d <= 0.0:
            Psi.append(0.0)            # no regret → nothing to learn
        elif gi <= 0.0:
            Psi.append(float("inf"))   # regret but no info → worst
        else:
            Psi.append((d * d) / gi)
    return Psi, Delta, g


def ids_rank_score(alphas: List[float], betas: List[float],
                   n_samples: int = IDS_MC_SAMPLES,
                   rng: random.Random | None = None) -> List[float]:
    """Rank-normalized IDS preference score for the NEXUS ranked-list interface:
    argmin Ψ → 1.0 (top), ascending Ψ → descending score. Arms with Ψ=∞ sink to
    the bottom. This is the faithful IDS preference order (top-1 = canonical
    deterministic-IDS pick); the linear blend with TS+keyword in learning.py is
    an NEXUS extension on top of the canonical quantities."""
    Psi, _, _ = ids_psi(alphas, betas, n_samples, rng)
    K = len(Psi)
    if K == 0:
        return []
    # rank by Ψ ascending; ∞ sorts last. Convert to a [0,1] score = 1 at best.
    finite = [p for p in Psi if p != float("inf")]
    order = sorted(range(K), key=lambda i: Psi[i])
    score = [0.0] * K
    for rank, i in enumerate(order):
        score[i] = 1.0 - (rank / max(K - 1, 1))
    return score


def meta_sample_prior(alpha_meta: float, beta_meta: float, lam: float,
                      rng: random.Random | None = None) -> Tuple[float, float]:
    """Meta-TS instance-prior sampling (Kveton et al. ICML 2021). Sample the
    cluster success rate  P_s ~ Beta(α^meta, β^meta)  from the meta-posterior,
    then return the arm-prior  (1 + λ·P_s, 1 + λ·(1−P_s)). Sampling P_s (vs a
    point-estimate) is the Meta-TS exploration mechanism over the instance
    prior. lam is the pseudo-count strength (NEXUS_TS_PRIOR_STRENGTH)."""
    rng = rng or random.Random()
    p_s = rng.betavariate(max(alpha_meta, 0.01), max(beta_meta, 0.01))
    p_s = max(0.0, min(1.0, p_s))
    return (1.0 + lam * p_s, 1.0 + lam * (1.0 - p_s))


def meta_warm_start(aff: float, kappa: float) -> Tuple[float, float]:
    """Warm-start the meta-posterior from the cluster affinity (informative
    meta-prior Q_0):  (1 + κ·aff, 1 + κ·(1−aff)). κ = NEXUS_TS_META_PRIOR.
    Used before any cluster outcomes have accrued; thereafter the meta-posterior
    is learned online from outcomes (learning.py owns the update)."""
    aff = max(0.0, min(1.0, float(aff)))
    return (1.0 + kappa * aff, 1.0 + kappa * (1.0 - aff))


__all__ = [
    "IDS_MC_SAMPLES", "posterior_a_star", "mutual_information",
    "expected_regret", "ids_psi", "ids_rank_score",
    "meta_sample_prior", "meta_warm_start",
]