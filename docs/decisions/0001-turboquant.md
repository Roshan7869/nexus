# Decision 0001 — TurboQuant on real NEXUS vectors: 8-bit qualifies, 4-bit misses

Date: 2026-08-17. Spike: evals/spikes/turboquant.py (training-free, seeded
random-orthogonal rotation — 384 is not a power of two, so Hadamard is
replaced by QR-orthogonal, same outlier-spreading property — + per-vector
scalar quantization; recall@10 over 200 sampled queries, cosine).

## Measured (2,277 dominant-dim-384 vectors from resources.embedding)

| bits | recall@10 | bytes/vec | % of float32 |
|------|-----------|-----------|--------------|
| 8    | 0.9950    | 386       | 25.1%        |
| 4    | 0.9460    | 194       | 12.6%        |
| 2    | 0.4960    | 98        | 6.4%         |

## Decision (against the pre-registered gates)
- **4-bit: REJECTED** — 0.946 < 0.95 adopt bar (misses by 0.004; no gate-bending).
- **8-bit: QUALIFIES** (0.995 recall, 25.1% memory) but **ADOPT-DEFERRED**:
  decision 0002 moved the hot path to FTS5-first, so the semantic arm is not
  memory-bound today. Adopt 8-bit when/if the semantic arm returns to the hot
  path or the vector count grows ~10x.
- 2-bit: unusable (≈ random).
- If 4-bit ever becomes required, the RaBitQ-style residual-refinement variant
  is the documented next step (one extra sign bit per residual).

## Data note
Embedding blobs are MIXED-dimension in the wild (2,842 rows; only 2,277 are
the dominant 384-dim). Any future quantized index must partition by dim.
