# Gaps 4–6 (IDE loop, OKF residual, chunk→resource)

## Gap 4 — IDE event loop

**Problem:** `ide_event_client` posted to LLM gateway `:8000` (off). Events never hit the resource brain; table stuck at ~38 test fixtures.

**Fix:**
- `POST /v1/event` (+ `/nexus/event`) on `nexus_http_daemon` → `handle_ide_event`
- Always `INSERT` into `ide_events` on the resource DB
- Client defaults to `http://127.0.0.1:8080/v1/event`

```bash
curl -s -X POST http://127.0.0.1:8080/v1/event \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"code_accepted","ide":"claude-code","model":"sonnet","provider":"anthropic","metadata":{"skill_name":"qa","project":"nexus"}}'
```

## Gap 5 — OKF residual sections

**Problem:** 105 cold actives lacked `resource_sections`; file paths mostly missing (binaries / unresolved).

**Fix:** `server/okf_sections.py` — file OKF when path resolves, else **synthesize** sections from `description` + `body_text`.

```bash
python scripts/run_okf_residual.py --apply
```

## Gap 6 — chunk_embeddings → resource_id

**Problem:** 66k embeddings keyed only by `chunk_id`; no denormalized resource link for retrieval.

**Fix:** `resource_id` column + backfill from `ingestion_chunks` (`chunk_id = id`). New stores populate `resource_id`.

```bash
python scripts/run_chunk_resource_link.py --apply
```

## Ops gates

| Gate | Target | Live (post-apply) |
|------|--------|-------------------|
| ide_event_loop | ≥5 real/30d | PASS |
| okf_residual | cold_without_sections ≤20 | **0** |
| chunk_resource_link | linked_pct ≥50% | **54.41%** |
