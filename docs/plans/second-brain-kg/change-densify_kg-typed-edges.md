# `scripts/densify_kg.py` — add typed-edge pass 7h (EDIT, not new)

| Field | Value |
|-------|-------|
| Layer | L1 (KG usable at retrieval time) |
| Status | **DONE** (2026-09-25) — `pass_7h_typed_edges` shipped, prod populated + converged |
| New or edit | **EDIT** — extend an existing 538-line script; do **not** create a parallel one |
| Depends on | `resource_embeddings`, `resources.cluster`, `resource_edges` (all exist); obsidian wikilink metadata if present |
| First-principle atoms | **A1_registry**, **A3_rank** |
| Gaps served | **G25** (KG densify → typed edges the retrieval lane can use) |

## Why this is an edit, not a new file
`scripts/densify_kg.py` already implements reversible, non-O(n²) edge derivation:
- `pass_7b_semantic_edges` — vectorized cosine (`mat @ mat.T`), per-node cap
  (`SIMILAR_CAP_PER_NODE=8`), writes `similar_to`.
- `pass_7d_member_edges` — `member_of` from 0-edge orphans to cluster anchors.
- `pass_7e_promote_co_triggered` — promote `co_triggered` count≥5 to weighted edges.
- `pass_7f_prune_similar_to` — gated prune with backup to `resource_edges_backup`.
- Reversibility: every insert stamps `created_at = run_ts`; documented rollback is
  `DELETE FROM resource_edges WHERE created_at >= '<run_ts>' AND edge_type='<type>'`.

`resource_edges` (migration `007_graph_topology.sql`) already has `edge_type TEXT` and its
comment already names `dependency`/`alternative`/`documentation_for`. So the typed-edge
*contract* and the *reversible derivation harness* both exist. Adding `edge_derivation.py` +
`scripts/derive_edges.py` + a new migration would **duplicate** all of this. We add **one
pass** instead.

## Role of the change
Add **`pass_7h_typed_edges`**: derive the two typed edges nothing currently populates, so
`graph_lane.py` has meaningful edges to expand over:
- **`documented_by`** — link a skill/tool → the doc that explains it. Source signal:
  obsidian `[[wikilink]]` metadata already parsed by the ingester (doc → resource references),
  and/or name/path correspondence (doc resource whose title matches a skill name).
- **`alternative_to`** — link resources that are *substitutes*: same `cluster` **and** cosine
  ≥ a higher threshold than generic `similar_to` (e.g. ≥ 0.82), **excluding** pairs already
  `co_triggered` (co-use implies complement, not alternative).

## Research grounding
- **GraphSAGE `arXiv:1706.02216`** — inductive edge prediction. We **defer** the learned/
  inductive version to **L3** (see roadmap); L1 uses the cheap deterministic signals above.
  Rationale (ADR-002 / G25): "prefer restore densified edges; GraphSAGE only if edge_pred needed."
- **KG-TS `SSRN:4807690`** — KG structure aids bandit cold-start. We take: typed edges give
  the graph lane (and later cold-start explore) real structure to lean on.
- **G25 `adopt` (gap-paper-map):** "edge_type column contract" (satisfied — we reuse it) and
  "Avoid O(n²) full pairwise; use FAISS NN" (satisfied — reuse `pass_7b`'s vectorized matrix,
  same normalized `mat`, thresholded per-node cap; no double loop).

## Guided plan (new pass, mirrors existing pass style)
```python
TYPED_ALT_THRESHOLD = 0.82          # stricter than similar_to's 0.65
TYPED_ALT_CAP = 4                    # cap alternative_to per node

def pass_7h_typed_edges(conn, ids, mat, dry_run):
    """7h: documented_by (from wikilinks/name-match) + alternative_to
    (same-cluster, cos>=0.82, not already co_triggered). Reversible via run_ts."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    existing = existing_edge_keys(conn)

    # --- alternative_to: reuse normalized mat; same-cluster high-sim, non-co-triggered
    id_to_cluster = {r[0]: r[1] for r in conn.execute(
        "SELECT id, cluster FROM resources WHERE cluster IS NOT NULL")}
    sim = mat @ mat.T; np.fill_diagonal(sim, -1.0)
    alt_edges = []
    for i, a in enumerate(ids):
        above = np.where(sim[i] >= TYPED_ALT_THRESHOLD)[0]
        order = above[np.argsort(-sim[i][above])]
        added = 0
        for j in order:
            b = ids[j]
            if added >= TYPED_ALT_CAP: break
            if id_to_cluster.get(a) != id_to_cluster.get(b): continue
            if (a, b) in existing: continue
            if _co_triggered(conn, a, b): continue        # substitutes, not companions
            alt_edges.append((a, b, float(sim[i][j]))); existing.add((a,b)); existing.add((b,a)); added += 1

    # --- documented_by: doc-resource -> skill/tool it explains (wikilink or name match)
    doc_edges = _derive_documented_by(conn, existing)     # returns [(doc_id, target_id, w)]

    print(f"7h: alternative_to={len(alt_edges)} documented_by={len(doc_edges)} "
          f"{'(dry-run)' if dry_run else ''}")
    if not dry_run:
        conn.executemany(
          "INSERT INTO resource_edges (source_id,target_id,edge_type,weight,co_trigger_count,created_at,updated_at)"
          " VALUES (?,?,'alternative_to',?,0,?,?)", [(a,b,round(w,4),ts,ts) for a,b,w in alt_edges])
        conn.executemany(
          "INSERT INTO resource_edges (source_id,target_id,edge_type,weight,co_trigger_count,created_at,updated_at)"
          " VALUES (?,?,'documented_by',?,0,?,?)", [(a,b,round(w,4),ts,ts) for a,b,w in doc_edges])
        conn.commit()
    return len(alt_edges), len(doc_edges)
```
Wire `pass_7h_typed_edges(conn, ids, mat, dry_run)` into the existing `main()` pass sequence
(after 7b so `mat`/`ids` are already loaded and normalized), gated by the same `--dry-run`.
Add a `--typed-edges` flag if you want it opt-in initially.

## Acceptance
- [x] Dry-run prints nonzero `alternative_to` / `documented_by` counts on the restored brain.
      (Evidence: pre-write dry-run `alternative_to=675 documented_by=319`; post-write dry-run
      `0/0` = converged/idempotent.)
- [x] `alternative_to` pairs are same-cluster and **not** present as `co_triggered`.
      (Copy + prod verification: 0 bad-cluster, 0 co-triggered across 462 edges.)
- [x] Reversible: `DELETE FROM resource_edges WHERE created_at >= '<run_ts>' AND
      edge_type IN ('alternative_to','documented_by')` fully undoes the run.
      (Copy: total restored 21651→baseline; prod pre-fix batch of 675 rolled back by run_ts.)
- [x] No O(n²) python double loop introduced (reuses vectorized `sim`).
- [x] `graph_lane.py` acceptance test now finds typed neighbors (closes the L1 loop) —
      `tests/test_graph_lane.py` green (16 tests incl. `test_brain_expand_alternative_to_neighbors`,
      `test_brain_expand_returns_typed_neighbors_best_first`); full suite 465 passed.

## As built (deltas vs the sketch)
- **Wired default-ON** into `main()` after `pass_7f` (no `--typed-edges` flag): consistent
  with sibling passes; reversibility via `run_ts` makes default-on safe.
- **`alternative_to` existing-set narrowed** to same-type pairs (+ `co_triggered` exclusion).
  The sketch's blanket `existing` (all edge types) would starve the pass of exactly the
  `>=0.82` pairs `pass_7b` already typed `similar_to` — the high-cos substitutes it exists
  to label. UNIQUE(source,target,edge_type) permits both types on one pair.
- **`TYPED_ALT_CAP=4` is cumulative per-node incident** (counts source+target, pre-seeded
  from existing rows), not cap-per-run. A per-run cap was non-idempotent on the live brain
  (dry-run found 106 re-addable pairs after the first write; 96 nodes over cap with max 16).
  Fixed, rolled back the pre-fix batch by run_ts, re-ran: 462 edges, max incident 4,
  0 over-cap, second run `0/0`.
- **Cluster guard requires non-NULL cluster** (`ca is None → skip`): the sketch's
  `id_to_cluster.get(a) != id_to_cluster.get(b)` lets `None == None` pass.
- **`documented_by` direction**: `source` = documented skill/tool, `target` = explaining
  doc-ish resource (per the role statement "skill/tool → the doc that explains it", not the
  sketch's `(doc_id, target_id)` comment).
- **Tool-type filter** (`skill/agent/command/cli_tool/mcp/hook`): the wikilink signal
  produced 162 explainer→explainer chains (159 topic→topic, 3 doc→topic) that aren't
  "documented_by" semantics — dropped; 158 high-signal pairs remain (108 skill, 27 agent,
  21 command, 1 mcp sources; 318 topic + 1 doc targets).
- **Name/path correspondence** prefixes: `skills-`, `docs-`, `claude-skill-` (dropped
  `gstack-` — false-positive-prone, e.g. `gstack-config` → generic `config` skill).
- **Tests**: `tests/test_densify_typed_edges.py` (6: dry-run no-write, same-cluster +
  non-co-triggered, direction/type filter incl. explainer-chain negative, idempotency,
  cumulative cap, reversibility) — 6/6 green.

## Impact gate + rollback
```
# offline script; no live-path impact until graph_lane folds these edges in
grep -n "def main" scripts/densify_kg.py     # confirm pass registration point
```
Rollback: delete `pass_7h_typed_edges` + its `main()` call; run the timestamped DELETE above to
drop any edges it wrote (`resource_edges` schema unchanged, no migration to revert).
**Prod run record**: documented_by run_ts `2026-09-25 09:04:17` (158 edges); alternative_to
run_ts `2026-09-25 09:06:40` (462 edges); pre-fix alternative_to batch (675, same 09:04:17
run_ts) already deleted. State: edges 21031 → 21651 (+620 = 462 + 158).
