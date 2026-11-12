# F5 — Cluster-Affinity Flatline · ML Systems / Multi-Armed Bandits

**Severity:** MEDIUM (latent, not live-impacting today) · **Effort:** LOW · **Blast radius:** Routing quality (dormant) · **Status:** DONE (write path → Beta mean; §7 gates PASS via `test_f5_affinity.py`; decay γ-discount §6-3 deferred as optional follow-up)

---

## 1. Symptom (observed)

The `cluster_affinity_history.new_affinity` column flatlined near 1.0 across all 8 clusters with
near-zero differentiation. The external audit read this as "the learning loop does nothing."

## 2. Root cause (mechanism)

Two distinct bugs, and the disarming twist that the column is **not on the live routing path**.

**(a) Reward-shaping saturation on the write path.** Each outcome nudges affinity by a *fixed*
additive step, then clamps to `[0,1]`:

```python
# server/learning.py:1139-1156
if   outcome == "correct": delta =  0.05
elif outcome == "wrong":   delta = -0.05
else:                      delta =  0.0
... old_affinity + delta ... max(0.0, min(1.0, ...))   # :1154
```

With correct outcomes dominating (~90%), a fixed `+0.05` ratchet drives every active cluster to the
`1.0` ceiling and pins it there — the clamp destroys all between-cluster signal. The code comment
(`:1135` "scaled via new_affinity") describes scaling that **was never implemented**; the delta is
constant.

**(b) The write column is dead weight — read path recomputes independently.** Routing does *not*
read `cluster_affinity_history.new_affinity`. It uses (i) centroid cosine similarity in the cluster
router and (ii) `get_cluster_affinity_scores()` (`learning.py:1209-1239`), which **recomputes** a
ratio directly from `routing_log` (`correct/total`, partial credited 0.5, `total>=3` gate). So the
saturated column is cosmetic today — but it is a latent trap: any future code that starts trusting
`new_affinity` inherits a saturated, signal-free feature.

**(c) Decay exists but barely fires.** `_apply_affinity_decay` (`:1166-1206`) pulls stale clusters
toward 0.5, but only every 50 outcomes (`_DECAY_INTERVAL`), only for clusters idle >24h
(`_DECAY_STALE_HOURS`), at `_DECAY_RATE=0.001` (0.1%/step) — far too weak to counteract a `+0.05`
ratchet on active clusters.

## 3. Scientific grounding

- **Chapelle, O.; Li, L. "An Empirical Evaluation of Thompson Sampling." *NeurIPS* 2011** — the
  correct mechanism for arm (cluster) preference under uncertainty is posterior sampling over a
  Beta-Bernoulli, *not* a hand-tuned additive nudge. NEXUS already does this properly for
  per-resource beliefs (`core/learner.py` Beta(α,β)); cluster affinity should mirror it (α += reward,
  β += (1−reward)) instead of `±0.05`.
- **Russo, D. et al. "A Tutorial on Thompson Sampling." *Foundations and Trends in ML*, 2018** —
  formalises reward accumulation as conjugate updates; shows why saturating point-estimates lose the
  exploration signal a posterior preserves.
- **Garivier, A.; Moulines, E. "On Upper-Confidence Bound Policies for Non-Stationary Bandit
  Problems." 2011** — under non-stationarity you need **discounted** or **sliding-window** statistics
  so old outcomes decay geometrically. This is the principled version of NEXUS's ad-hoc decay: replace
  the 0.1%/50-step nudge with a discount factor γ on the reward sufficient statistics.
- **Sutton, R.; Barto, A. *Reinforcement Learning: An Introduction* (2018), §2 & §reward scaling** —
  fixed additive rewards with hard clamps cause value saturation and destroy relative ordering; use
  incremental sample-average / exponential-recency-weighted updates.

## 4. Engineering context

The fix aligns cluster affinity with the *already-correct* per-resource Thompson machinery. Two
acceptable designs:

- **Preferred:** delete the `new_affinity` write ratchet; make `get_cluster_affinity_scores` the
  single source, and upgrade it to a discounted Beta posterior (γ-weighted α/β) so it (a) stays the
  live read path and (b) gains non-stationary adaptivity.
- **Minimal:** keep the column but replace the fixed `±0.05` with a Beta update
  (`α += reward; β += 1−reward; affinity = α/(α+β)`) and let the existing `total>=3` gate stand.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `server/learning.py:1139-1156` | Fixed `±0.05` + clamp | Beta update *or* delete write path | `RCA-F5` marker |
| `server/learning.py:1135` | Stale comment ("scaled") | Correct/remove comment | inline |
| `server/learning.py:1188` | Decay gated >24h, rate 0.001 | Adopt γ-discount on sufficient stats *or* strengthen | `RCA-F5` marker |
| `server/learning.py:1209-1239` | Recompute ratio (live read) | Optionally upgrade to discounted Beta; keep as SoT | `RCA-F5` marker |
| `server/cluster_router.py` (~centroid + affinity blend) | Reads recomputed score | Confirm it never reads `new_affinity` | doc note |

## 6. Execution steps

1. Add unit test capturing current saturation (feed 20 correct → assert every cluster == 1.0) to
   lock the bug before changing it.
2. Replace `±0.05` write with Beta update (or delete write path per Preferred design).
3. If keeping decay, convert to γ-discounted sufficient statistics; otherwise remove the weak nudge.
4. Add `RCA-F5` annotations; correct the stale `:1135` comment.
5. Re-run the test: after mixed outcomes, clusters must **differentiate** (spread > 0.1).

## 7. Verification (pass/fail)

- **PASS** iff, after a synthetic stream (cluster A: 18/20 correct; cluster B: 5/20), computed
  affinities differ by > 0.2 and neither is pinned at 1.0.
- **PASS** iff routing decisions are unchanged for the live read path when only the (dead) write
  column changes — proving today's behavior is preserved while the trap is removed.

## 8. Risk & rollback

- Low risk today (column is inert). Main risk is subtly changing `get_cluster_affinity_scores`
  behavior — guard with the before/after routing-equivalence test. Rollback = revert `learning.py`.

## 9. References

1. Chapelle, O.; Li, L. "An Empirical Evaluation of Thompson Sampling." *Advances in Neural Information Processing Systems 24 (NeurIPS)*, 2011.
2. Russo, D.; Van Roy, B.; Kazerouni, A.; Osband, I.; Wen, Z. "A Tutorial on Thompson Sampling." *Foundations and Trends in Machine Learning* 11(1), 2018.
3. Garivier, A.; Moulines, E. "On Upper-Confidence Bound Policies for Non-Stationary Bandit Problems." *Algorithmic Learning Theory (ALT)*, 2011.
4. Sutton, R. S.; Barto, A. G. *Reinforcement Learning: An Introduction*, 2nd ed. MIT Press, 2018.
