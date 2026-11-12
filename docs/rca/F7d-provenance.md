# F7d — Edge-Level Provenance · Data Provenance

**Severity:** MEDIUM (design gap) · **Effort:** MEDIUM · **Blast radius:** Trust / auditability of KG facts · **Status:** OPEN

---

## 1. Symptom (observed)

Graph edges and derived facts record **what** but not **why/whence**: there is no per-edge evidence
(which source document/observation/agent produced the assertion, with what confidence and when).
The audit marked provenance "PARTIAL." Without it, a fact cannot be audited, contested, or
recomputed, and low-quality sources cannot be down-weighted.

## 2. Root cause (mechanism)

Edges (`resource_edges`) and belief updates store the relation and a confidence-ish scalar (`weight`,
plus `co_trigger_count`), but not a **derivation record** linking the assertion to its generating activity and inputs. So the chain
"source chunk → extraction step → edge" is not reconstructable, and two edges from a trusted vs.
speculative source look identical.

## 3. Scientific grounding

- **W3C. "PROV-O: The PROV Ontology." W3C Recommendation, 2013.** The standard provenance model:
  **Entity** (the fact), **Activity** (the process that produced it), **Agent** (who/what ran it),
  linked by `wasGeneratedBy` / `used` / `wasAttributedTo`. NEXUS should record, per edge, the
  activity (extractor/version), the used inputs (source chunk ids), and the agent (model/tool).
- **Moreau, L.; Groth, P. *Provenance: An Introduction to PROV* (2013).** Practical guidance on
  applying PROV: minimum viable provenance is (source → activity → derived fact) with timestamps —
  enough for audit and recomputation.
- **Groth, P. et al. "Nanopublications." (2010).** A compact pattern: every assertion ships with its
  provenance and publication-info as first-class, queryable metadata — a good target shape for
  per-edge evidence rows.
- **Buneman, P.; Khanna, S.; Tan, W. "Why and Where: A Characterization of Data Provenance." *ICDT*
  2001.** Distinguishes *why*-provenance (which inputs justify a fact) from *where*-provenance
  (which source location) — both worth capturing for retrieval-grounding and citation.

## 4. Engineering context

Add a provenance envelope to edges/facts: `source_ref` (chunk/document id), `activity`
(extractor + version), `agent` (model/tool id), `confidence`, `created_at`. Store as columns on the
edge table for the common case, and/or a side `edge_provenance` table for multi-source facts. This
lets retrieval cite sources (pairs with F7c/RAG grounding) and lets F5's learning down-weight
low-provenance edges.

## 5. Files / components to update

| Path | Current | Change | Annotation |
|------|---------|--------|------------|
| `db/migrations/007_graph_topology.sql` (`resource_edges`) + new migration `022_edge_provenance.sql` | `edge_type`, `weight`, `co_trigger_count` — no provenance (**not** in `db/schema.sql`; live drifted — see F8) | Add `source_ref`, `activity`, `agent` via new migration + ALTER; index | `RCA-F7d` marker |
| New `edge_provenance` table (optional) | — | Multi-source derivation records (PROV triples) | schema note |
| KG edge writers / ingest | Insert relation | Populate provenance on write | `RCA-F7d` marker |
| Retrieval/citation surface | No source cite | Surface `source_ref` with results | `RCA-F7d` marker |

## 6. Execution steps

1. ADR mapping NEXUS edges to PROV-O (Entity/Activity/Agent); decide inline columns vs. side table.
2. Idempotent migration adding provenance columns (backfill `activity='legacy'`, `confidence` kept).
3. Update edge writers to record source chunk id + extractor version + agent.
4. Expose provenance in retrieval output (citations).

## 7. Verification (pass/fail)

- **PASS** iff every newly written edge has non-null `source_ref` + `activity` + `agent`, and a query
  can trace a fact back to its originating chunk/document.
- **PASS** iff legacy edges remain readable (backfilled sentinel provenance).

## 8. Risk & rollback

- Additive schema — low risk; DB backup before migration (624 MB). Rollback = drop columns / restore.

## 9. References

1. World Wide Web Consortium. "PROV-O: The PROV Ontology." W3C Recommendation, 30 April 2013.
2. Moreau, L.; Groth, P. *Provenance: An Introduction to PROV.* Morgan & Claypool, 2013.
3. Groth, P.; Gibson, A.; Velterop, J. "The Anatomy of a Nanopublication." *Information Services & Use* 30(1–2), 2010.
4. Buneman, P.; Khanna, S.; Tan, W. C. "Why and Where: A Characterization of Data Provenance." *International Conference on Database Theory (ICDT)*, 2001.
