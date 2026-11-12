# F7b — Identity Resolution · Entity Resolution / Record Linkage

**Severity:** HIGH (design gap) · **Effort:** HIGH · **Blast radius:** KG node quality / dedup · **Status:** OPEN

---

## 1. Symptom (observed)

The graph has no principled entity-resolution layer. The same real-world entity (a skill referenced
under slightly different names, a resource re-ingested under a variant slug, a project alias) can
create duplicate nodes with no merge/canonicalization. The audit marked "identity resolution: OPEN."

## 2. Root cause (mechanism)

Node identity is keyed on exact string/id match at ingest. There is no candidate-generation
(blocking) step, no pairwise similarity scoring, and no merge decision with a canonical-id mapping.
So near-duplicates accumulate and fragment the graph (edges split across duplicate nodes, beliefs
attributed to the wrong copy).

## 3. Scientific grounding

- **Fellegi, I.; Sunter, A. "A Theory for Record Linkage." *Journal of the American Statistical
  Association* 64(328), 1969.** The foundational probabilistic model: compute a match weight from
  per-field agreement/disagreement likelihoods and threshold into match / possible-match / non-match.
  This is the principled replacement for exact-string keying.
- **Christen, P. *Data Matching* (Springer, 2012).** The modern end-to-end pipeline: **blocking →
  comparison → classification → evaluation**. Prescribes blocking to avoid O(n²) comparisons and
  standard similarity functions (Jaro-Winkler, token-set) per field.
- **Papadakis, G. et al. "Blocking and Filtering Techniques for Entity Resolution: A Survey." *ACM
  Computing Surveys* 53(2), 2020.** Scalable candidate generation — essential for a 3,254-resource /
  10K-node graph so ER doesn't compare every pair.
- **Embedding-based ER (Mudgal, S. et al. "Deep Learning for Entity Matching." *SIGMOD* 2018).**
  Since NEXUS already has all-MiniLM embeddings, a cheap high-recall blocking step is cosine-similarity
  over existing vectors, then Fellegi-Sunter-style scoring on the candidate pairs.

## 4. Engineering context

Reuse the existing embedding infrastructure: (1) **block** by embedding cosine ≥ τ_block to get
candidate pairs cheaply; (2) **score** candidates with field similarities (name Jaro-Winkler, type
equality, cluster agreement) into a match weight; (3) **classify** with two thresholds (auto-merge /
review / distinct); (4) record a `canonical_id` mapping so all references resolve to the survivor.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `db/schema.sql` | No ER tables | Add `entity_resolution(entity_id, canonical_id, score, decision, method)` | `RCA-F7b` marker |
| New `server/entity_resolution.py` | — | Blocking + scoring + classification pipeline | file header |
| KG ingest writers | Exact-key insert | Consult ER before creating a node; attach to canonical | `RCA-F7b` marker |
| Node readers | — | Resolve through `canonical_id` | `RCA-F7b` marker |

## 6. Execution steps

1. ADR referencing Fellegi-Sunter + Christen's pipeline; pick τ_block and the two decision thresholds.
2. Create the `entity_resolution` table + idempotent migration.
3. Implement `server/entity_resolution.py`: embedding blocking → field-similarity scoring →
   3-way classification → canonical mapping.
4. Hook ingest to consult ER; add a batch backfill over existing nodes.
5. Route reads through the canonical map.

## 7. Verification (pass/fail)

- **PASS** iff a seeded near-duplicate (same skill, variant name) is auto-merged to one canonical id
  and its edges/beliefs consolidate onto the survivor.
- **PASS** iff a genuinely distinct pair below threshold is *not* merged (no false merges on a
  labeled mini-benchmark of ~50 pairs; report precision/recall).

## 8. Risk & rollback

- **Merges are destructive to graph structure** — implement as reversible (keep original ids +
  mapping; never hard-delete on auto-merge). Start in *review* mode (no auto-merge) and only enable
  auto-merge after the benchmark shows high precision. Rollback = drop the ER table + mapping.

## 9. References

1. Fellegi, I. P.; Sunter, A. B. "A Theory for Record Linkage." *Journal of the American Statistical Association* 64(328):1183–1210, 1969.
2. Christen, P. *Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection.* Springer, 2012.
3. Papadakis, G.; Skoutas, D.; Thanos, E.; Palpanas, T. "Blocking and Filtering Techniques for Entity Resolution: A Survey." *ACM Computing Surveys* 53(2), 2020.
4. Mudgal, S. et al. "Deep Learning for Entity Matching: A Design Space Exploration." *SIGMOD*, 2018.
