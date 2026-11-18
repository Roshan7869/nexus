# E4b — Passage-level chunk splitting: a NEGATIVE result, fully reverted

**Date:** 2026-11-19 · **Status:** implemented, measured across 4 variants, **all worse than
baseline, fully reverted**. Corpus restored to exactly 4,521 chunks.

## Hypothesis (and why it looked strong)

Measured before the change:

| Observation | Value |
|---|---|
| p50 chunk length | **5,690 chars ≈ 1,400 tokens** |
| Chunks > 4,000 chars | **61.3%** (2,773) |
| Chunks per resource | **exactly 1** — the chunk lane was 1:1 with the resource lane |
| Resources with no chunk | **547** (392 with usable body_text) |

The plan (Dense-X, arXiv:2312.06648, +10.1 R@20; Late Chunking 2409.04701):
split long bodies into ~1,024-char passages, prefix each with `name — description`,
embed the passages. Expected +10–15 Recall@5 at zero added latency.

## Measured outcome — four variants, none helped

Golden set, 105 cases (84 skill-asserting), 3 seeds each, cross-seed spread 0.000:

| Variant | hit@k | top-1 | MRR | vs baseline |
|---|---|---|---|---|
| **Baseline** (4,521 chunks) | **0.286** | 0.119 | 0.1895 | — |
| Split + output dedupe (24,518 children) | 0.250 | 0.119 | 0.1687 | **−12.5%** |
| Split + resource-grain RRF collapse | 0.238 | 0.107 | 0.1607 | **−16.8%** |
| Orphans only (392 backfilled) | 0.250 | 0.119 | 0.1736 | **−12.5%** |
| Reverted (baseline restored) | 0.286 | 0.119 | 0.1895 | ✅ |

Receipts: `evals/results/perf/golden-after-e4b*.json`.

## Why it failed

1. **The corpus is already passage-shaped.** Every NEXUS chunk is a skill/agent file
   that begins with `name` + `description` frontmatter. The description *is* the
   retrieval signal, and the parent chunk already embeds it. Splitting adds dozens of
   body passages that are near-duplicates of a resource already correctly represented
   — so the change mostly adds candidate noise.
2. **Pool saturation is structural, not fixable by collapsing.** Fusing at chunk grain
   then deduping output let multi-chunk resources fill the 100-slot pool. Collapsing to
   resource grain before slicing (the "correct" fix) made it *worse* (0.238), because
   the collapse replaced high-RRF chunk candidates with lower-ranked single-chunk
   resources that were worse matches.
3. **The orphan backfill hurt too** (0.250 vs 0.286). Those 392 resources had no chunk
   because they are thin/stub content (the corpus has 363 resources under 300 chars);
   giving them vectors let weak resources compete with strong ones.
4. **Dense-X does not transfer to a name+description catalogue.** Its gain comes from
   documents whose content is not summarized up front. NEXUS resources are the opposite.

## Decision and what was preserved

- **Children deleted** (24,518), **orphans deleted** (392), `chunk_split_backfill`
  source row removed. Corpus is byte-equivalent to pre-E4b: 4,521 chunks, 4,521
  embeddings, 4,521 vectors, 0 unembedded.
- **Kept:** `scripts/split_chunks.py` (documented, revertible, reusable if a corpus of
  long *non-summarized* documents is ever ingested) and the env-gated resource dedupe in
  `retrieve_chunks` (`NEXUS_CHUNK_DEDUPE`, inert at one chunk per resource).
- **Latent issue found, not fixed:** `chunks_fts` holds ~8.9k rows against 4.5k chunks —
  pre-existing stale FTS entries from an earlier ingestion. Unhydratable rows are
  skipped in output, so this is waste, not corruption. Worth a cleanup script later.

## Lesson

Three consecutive index-side interventions: **two helped** (FTS AND→OR + vector
backfill: 0.155 → 0.286) and **three hurt** (TS priors 0.274, passage splitting
0.250/0.238, orphan chunks 0.250). The pattern is clear — this corpus rewards
**increasing candidate recall** and punishes **anything that re-ranks or dilutes
existing good candidates**. The next index work should target the 363 stub resources
(missing content, an enrichment problem, not a chunking one).
