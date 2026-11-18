# Phase 1 — OKF sections corpus arm (hybrid intact)

Status: IN PROGRESS (implementation complete; section-vec population running — see
"As built" notes; chunks regression + FTS arm already accepted)
Depends on: Phase 0

## Goal
Teach `retrieve_chunks()` to retrieve over OKF `resource_sections` / `sections_fts` behind a
`corpus="chunks"|"sections"` param (default `"chunks"`, zero behavior change until flipped).
Keep the semantic arm (D2 defers embedder removal to Phase 2). Ensure cold resources still
resolve by backfilling `resource_sections` first.

## Files to touch
| file:line | current | change |
|-----------|---------|--------|
| `server/router_v2/chunk_retrieval.py:53` `_fts_search` | queries `chunks_fts` | add sibling `_fts_search_sections` over `sections_fts` |
| `server/router_v2/chunk_retrieval.py:115` `_hydrate` | joins `ingestion_chunks`+`ingestion_sources` | add `_hydrate_sections` joining `resource_sections`→`resources` |
| `server/router_v2/chunk_retrieval.py:155` `retrieve_chunks` | fixed chunks corpus | add `corpus=` param, branch fts+hydrate |
| `server/router_v2/__init__.py:277` | `_retrieve_chunks(..., vec=True)` | plumb `corpus` (default unchanged) |
| `server/okf_sections.py:128` `backfill_cold_sections` | exists | run once to close section coverage gaps |

## Schema reference (migration 006)
```
resource_sections(id, resource_id→resources.id, section_order, depth,
                  heading, content, token_count, created_at)
sections_fts(content, heading, resource_id UNINDEXED)      # external-content, content_rowid=id
# rowid of sections_fts == resource_sections.id  (kept in sync by triggers)
```

## Diff sketch — chunk_retrieval.py

### 1. New sections FTS arm (sibling of `_fts_search`, chunk_retrieval.py:53)
```python
# AFTER _fts_search(...)  — mirrors it, different table + rowid meaning
def _fts_search_sections(con, query: str, limit: int) -> list[dict]:
    match = _prepare_fts(query)            # reuse existing sanitizer (:41)
    if not match:
        return []
    try:
        rows = con.execute(
            "SELECT f.rowid AS section_id, bm25(sections_fts) AS score "
            "FROM sections_fts f WHERE sections_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"section_id": int(r["section_id"]), "fts_rank": i, "fts_raw": float(r["score"])}
            for i, r in enumerate(rows, start=1)]
```

### 2. New sections hydrate (sibling of `_hydrate`, chunk_retrieval.py:115)
```python
def _hydrate_sections(con, section_ids: list[int], snippet_len: int = 240) -> dict[int, dict]:
    if not section_ids:
        return {}
    ph = ",".join("?" for _ in section_ids)
    rows = con.execute(
        f"SELECT s.id, s.heading, s.content, s.resource_id, "
        f"       r.name AS resource_name, r.cluster, r.resource_type, r.repo_name "
        f"FROM resource_sections s "
        f"JOIN resources r ON r.id = s.resource_id "
        f"WHERE s.id IN ({ph})",
        tuple(section_ids),
    ).fetchall()
    out = {}
    for r in rows:
        sid = int(r["id"])
        out[sid] = {
            "chunk_id": sid,                       # keep key name → downstream shape stable
            "heading": r["heading"],
            "snippet": (r["content"] or "")[:snippet_len].replace("\n", " ").strip(),
            "chunk_type": "section",
            "source_id": None,
            "source_path": None,
            "resource_id": r["resource_id"],
            "resource_name": r["resource_name"],
            "cluster": r["cluster"],
            "repo_name": r["repo_name"],
        }
    return out
```

### 3. Branch inside `retrieve_chunks` (chunk_retrieval.py:155)
```python
# BEFORE
def retrieve_chunks(query, top_k=10, clusters=None, db_path=DB_PATH,
                    vec=True, snippet_len=240) -> dict:
    ...
    fts = _fts_search(con, query, limit=pool)
    ... sem via ChunkIndex ...
    fused = _rrf(fts, sem)[:top_k*2]
    meta = _hydrate(con, cids, snippet_len)
    rmeta = _hydrate_resources(con, rids)      # separate resource lookup

# AFTER
def retrieve_chunks(query, top_k=10, clusters=None, db_path=DB_PATH,
                    vec=True, snippet_len=240, corpus="chunks") -> dict:
    ...
    if corpus == "sections":
        fts = _fts_search_sections(con, query, limit=pool)
        id_key = "section_id"
        sem = _sem_search_sections(...) if vec else []      # Phase 2 supplies vec arm
        fused = _rrf_generic(fts, sem, id_key)[:top_k*2]
        meta = _hydrate_sections(con, [f[id_key] for f in fused], snippet_len)
        # resource fields already joined in _hydrate_sections → skip _hydrate_resources
    else:
        fts = _fts_search(con, query, limit=pool)           # unchanged path
        sem = _sem_search(...) if vec else []
        fused = _rrf(fts, sem)[:top_k*2]
        meta = _hydrate(con, [f["chunk_id"] for f in fused], snippet_len)
        rmeta = _hydrate_resources(con, rids)
    # result assembly + cluster filter (:200-225) is corpus-agnostic → unchanged
```
> Note: `_rrf` (chunk_retrieval.py:87) is keyed on `"chunk_id"`. Either generalize it to
> `_rrf_generic(fts, sem, id_key)` or normalize section rows to carry `chunk_id` before
> RRF. Prefer normalizing (smaller diff, keeps `_rrf` untouched → lower impact radius).

## Diff sketch — router_v2/__init__.py:277
```python
# BEFORE
rc = _retrieve_chunks(query, top_k=k, clusters=clusters, db_path=db_path, vec=True)
# AFTER
rc = _retrieve_chunks(query, top_k=k, clusters=clusters, db_path=db_path,
                      vec=True, corpus=RETRIEVAL_CORPUS)   # module const, default "chunks"
```
> `RETRIEVAL_CORPUS` is a module-level constant flipped in Phase 2 after the A/B — NOT a
> new env flag (respects D4).

## Backfill (close section coverage before A/B)
```python
# one-shot, uses existing okf_sections.backfill_cold_sections (okf_sections.py:128)
from server.okf_sections import backfill_cold_sections
from server.db import get_connection
conn = get_connection()
report = backfill_cold_sections(conn, db_path="db/nexus.db")   # file-OKF else synth
conn.commit()
# report: {file_ok, synth_ok, fail, skip, sections}
```

## What must work (acceptance)
- [x] `retrieve_chunks(q, corpus="sections")` returns hits with `resource_name`,
      `cluster`, `heading`, `snippet` populated; `source == "fts"` when `vec=False`.
      (verified + `sections+lexical` eval arm: recall@k=0.119, mrr=0.0556, sem=False)
- [x] `retrieve_chunks(q, corpus="chunks")` output is byte-identical to pre-change
      (regression guard). (re-ran `chunks+hybrid`: 0.1905/0.1373 identical;
      per-case diff 0/105 differing fields — `p1_regress_chunks.json`)
- [x] Cluster filter (`clusters=[...]`) still narrows section hits.
- [x] Section coverage: every resource that had chunks now has ≥1 `resource_sections` row
      (backfill report `fail == 0` or explained). (cold backfill: 0 candidates/0 fail;
      full-coverage pass synthesized 385/385, fail=0 → 0 lacking; `sections_fts`
      triggers keep FTS in sync, 23700=23700)

## As built (implementation notes vs sketch)
- **OR-variant FTS for sections**: `_fts_search_sections` uses a new
  `_prepare_fts_any()` (OR-join + bm25) instead of the shared `_prepare_fts`
  (AND-join). Measured: AND-match returned **0 rows on every multi-token query**
  against short sections (the chunks arm only survives because its semantic arm
  carries recall). The chunks path keeps `_prepare_fts` unchanged (byte-identity).
- **Row normalization (sketch's preferred option)**: section rows carry
  `chunk_id = resource_sections.id`, so `_rrf` and the result-assembly loop are
  untouched — no `_rrf_generic`.
- **`_hydrate_sections` mirrors `_hydrate` exactly** (no resource JOIN): resource
  fields come from the shared `_hydrate_resources`, keeping one assembly path.
- **Section vec arm built in Phase 1 (not deferred to Phase 2)**: sibling class
  `SectionIndex` in `chunk_index.py` (no new file) + `backfill_section_embeddings()`
  in `okf_sections.py`. `vec_sections_content` (plain, empty, from migration 006)
  is self-repaired to a vec0 virtual table on first open; floats in
  `section_embeddings`. Text-hash cache (`chunk_embed_cache`) shared with chunks.
  Without this, Phase 2 arm B (`sections+hybrid`) would silently run FTS-only
  and degenerate into arm C.
- **Coverage pass beyond `backfill_cold_sections`**: the cold backfill only
  targets active+untriggered resources (0 candidates); the remaining 385
  chunk-linked resources (126 active, 259 archived) were covered with
  `synthesize_from_catalog` in a one-off pass (fail=0).

## Verify commands
```
python -m server.router_v2.chunk_retrieval "fix a login bug"      # smoke (chunks)
python -c "from server.router_v2.chunk_retrieval import retrieve_chunks as R; \
           print(R('fix a login bug', corpus='sections', vec=False)['n'])"
python evals/run_eval.py --arm chunks+hybrid   # confirm baseline unchanged
```

## Impact gate (run BEFORE editing)
```
impact({target:"retrieve_chunks", direction:"upstream"})     # expect router_v2 callers
impact({target:"_rrf", direction:"upstream"})                # confirm only retrieve_chunks
```
STOP and report if either returns HIGH/CRITICAL.

## Risk & rollback
- Risk: MEDIUM. `retrieve_chunks` is a hub; the `corpus` default keeps old path intact,
  so risk is contained to the new branch until Phase 2 flips the default.
- Rollback: revert the branch; set `RETRIEVAL_CORPUS="chunks"`.
