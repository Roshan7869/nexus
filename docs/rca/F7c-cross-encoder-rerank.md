# F7c — Cross-Encoder Rerank Lane · Information-Retrieval Engineering

**Severity:** MEDIUM (design gap) · **Effort:** MEDIUM · **Blast radius:** Routing/retrieval precision · **Status:** OPEN

---

## 1. Symptom (observed)

Retrieval fuses ~3 of 5 intended lanes (BM25 keyword + bi-encoder vector + RRF). There is **no
cross-encoder reranking stage** and no temporal lane. The audit's "hybrid retrieval: 3/5 lanes"
verdict is accurate. Ranking relies on a bi-encoder (independent query/doc embeddings), which is
fast but less precise than a cross-encoder that jointly attends to query+candidate.

## 2. Root cause (mechanism)

The fusion stage (`server/router_v2/fusion.py`) combines lexical + dense retrieval via Reciprocal
Rank Fusion and stops. Bi-encoder scores are computed once, independently, so subtle query-candidate
interactions (negation, term specificity, intent nuance) are never modeled. This caps top-1 precision
on hard/ambiguous queries.

## 3. Scientific grounding

- **Nogueira, R.; Cho, K. "Passage Re-ranking with BERT." arXiv, 2019.** The canonical two-stage
  design: a cheap first-stage retriever produces top-k, then a **cross-encoder** reranks by jointly
  encoding (query, candidate). Large precision gains at the top of the list — exactly the regime that
  matters for routing (we act on top-1/top-3).
- **Khattab, O.; Zaharia, M. "ColBERT: Efficient and Effective Passage Search via Contextualized
  Late Interaction over BERT." *SIGIR* 2020.** If full cross-encoder latency is a concern, late
  interaction gives most of the precision at a fraction of the cost — a middle option for NEXUS.
- **Cormack, G.; Clarke, C.; Büttcher, S. "Reciprocal Rank Fusion Outperforms Condorcet and
  Individual Rank Learning Methods." *SIGIR* 2009.** Validates the RRF NEXUS already uses as the
  *first-stage* fuser; the point is RRF is a fusion step, **not** a reranker — a cross-encoder is
  the complementary second stage.
- **Lewis, P. et al. "Retrieval-Augmented Generation." *NeurIPS* 2020.** Establishes retrieval
  quality as the dominant lever on downstream decision quality — motivating the added rerank lane.

## 4. Engineering context

Add a stage-2 cross-encoder rerank over the top-k RRF output (k≈20). Use a small pretrained
cross-encoder (e.g. `ms-marco-MiniLM-L-6-v2`) loaded via the same singleton pattern as F6 to avoid
per-call reload. Keep it **behind a flag** and only rerank the top-k so latency stays bounded
(reranking 20 candidates is cheap and off the cold path once warmed).

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `server/router_v2/fusion.py` | BM25 + dense + RRF | Add optional stage-2 cross-encoder rerank of top-k | `RCA-F7c` marker |
| New `server/cross_encoder.py` (or extend `iso_embedder.py`) | — | Singleton-loaded cross-encoder; `rerank(query, candidates)` | file header |
| Config/flags | — | `ENABLE_CROSS_ENCODER_RERANK` (default off until benchmarked) | inline |

## 6. Execution steps

1. ADR: two-stage retrieval (Nogueira & Cho); choose cross-encoder model + k.
2. Implement singleton-loaded reranker (reuse F6 warm-up so first rerank is hot).
3. Insert rerank after RRF in `fusion.py`, gated by flag, limited to top-k.
4. Build a small labeled query→correct-resource set; measure top-1/top-3 before vs after.

## 7. Verification (pass/fail)

- **PASS** iff on the labeled benchmark, enabling rerank improves top-1 accuracy without regressing
  top-3 recall, at acceptable warm latency (< ~150 ms added for k=20).
- **PASS** iff with the flag off, routing output is byte-identical to today (safe rollout).

## 8. Risk & rollback

- Adds a model load (mitigated by F6 warm-up) and per-query latency (bounded by top-k). Ship behind
  a default-off flag; rollback = flip the flag.

## 9. References

1. Nogueira, R.; Cho, K. "Passage Re-ranking with BERT." *arXiv:1901.04085*, 2019.
2. Khattab, O.; Zaharia, M. "ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT." *SIGIR*, 2020.
3. Cormack, G. V.; Clarke, C. L. A.; Büttcher, S. "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." *SIGIR*, 2009.
4. Lewis, P. et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." *NeurIPS*, 2020.
