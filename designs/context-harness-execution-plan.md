# Context Harness Quality Upgrade — Execution Plan

**Plan ID:** 57 (NEXUS)
**Project:** hermes
**Goal:** Integrate Scrapling (fetch + spider), Scrapecraft (pattern learning), and Sherlock (identity probe) as 4 new RRF backend channels in the context harness
**Total atoms:** 22
**Total estimated effort:** 10-14 days

---

## Phase 0 — Foundation & Environment Setup (1 day)

### Atom 0.1: Verify project dependencies
- **Owner:** executor
- **Risk:** LOW — all three projects exist on disk
- **Inputs:** /home/roshan/scrapecraft, /home/roshan/Scrapling, /home/roshan/sherlock
- **Outputs:** dependency check report
- **Verification:** `pip list | grep -E "scrapling|scrapecraft|sherlock"` shows installed packages
- **Rollback:** N/A (read-only)

### Atom 0.2: Create MCP server scaffold
- **Owner:** executor
- **Risk:** LOW — standard pattern
- **Inputs:** existing MCP server patterns in ~/.hermes/plugins/
- **Outputs:** `/home/roshan/hermes/plugins/context_harness/` directory with `__init__.py`, `server.py`, `tools/` subpackage
- **Verification:** `python -c "from hermes.plugins.context_harness import server; print('OK')"` succeeds
- **Rollback:** `rm -rf /home/roshan/hermes/plugins/context_harness/`

### Atom 0.3: Define RRF fusion interface
- **Owner:** executor
- **Risk:** LOW — RRF is well-understood
- **Inputs:** existing RRF code in context-harness (conceptual)
- **Outputs:** `rrf_fusion.py` with `RRFBackend` protocol class, `fuse_results()` function, weight config
- **Verification:** unit test passes with 3 mock backends
- **Rollback:** revert rrf_fusion.py

---

## Phase 1 — Scrapling Fetch Channel (2 days)

### Atom 1.1: Wrap Scrapling Fetcher as MCP tool
- **Owner:** executor
- **Risk:** MEDIUM — Scrapling uses anyio, MCP server may need async bridge
- **Inputs:** Scrapling's `Fetcher`, `StealthyFetcher`, `DynamicFetcher` from `scrapling/fetchers/`
- **Outputs:** `tools/fetch.py` with `harness_fetch(url, mode='auto')` tool
  - Auto-fallback: static → dynamic → stealth on 403/429/503
  - Returns: `{url, title, text_content, links[], meta{}, status, fetch_mode, latency_ms}`
- **Verification:** `harness_fetch("https://example.com")` returns valid content in <5s
- **Rollback:** comment out tool registration in server.py

### Atom 1.2: Implement response-to-text extraction pipeline
- **Owner:** executor
- **Risk:** LOW — Scrapling's parser.py already has CSS/XPath selectors
- **Inputs:** `scrapling/parser.py` (AttributesHandler, TextHandler, custom type handlers)
- **Outputs:** `tools/extract.py` with:
  - `extract_text(html)` — strip tags, normalize whitespace
  - `extract_links(html, base_url)` — absolute URL resolution
  - `extract_meta(html)` — og:title, og:description, meta keywords
  - `extract_tables(html)` — structured table → list of dicts
- **Verification:** extract from a known HTML string returns correct fields
- **Rollback:** revert extract.py

### Atom 1.3: Add batch fetch tool
- **Owner:** executor
- **Risk:** LOW — wraps Atom 1.1 with asyncio.gather
- **Inputs:** Atom 1.1 output
- **Outputs:** `harness_fetch_batch(urls, mode='auto', max_concurrent=5)` tool
  - Returns list of per-url results
  - Rate-limited by semaphore
- **Verification:** batch of 3 URLs returns all 3 results
- **Rollback:** comment out tool registration

### Atom 1.4: Register in RRF pipeline
- **Owner:** executor
- **Risk:** LOW — config change
- **Inputs:** `rrf_fusion.py` from Atom 0.3
- **Outputs:** scrapling-fetch backend registered with weight 1.2, 5min cache TTL
- **Verification:** RRF query includes scrapling-fetch results in fused output
- **Rollback:** remove backend from RRF config

---

## Phase 2 — Sherlock Probe Channel (1.5 days)

### Atom 2.1: Port Sherlock probe engine
- **Owner:** executor
- **Risk:** MEDIUM — Sherlock is synchronous, needs async wrapper
- **Inputs:** `/home/roshan/sherlock/sherlock_project/sherlock.py`, `sites.py`, `result.py`, `resources/data.json` (3,283 lines, 400+ sites)
- **Outputs:** `tools/probe.py` with:
  - `SherlockProbe` class wrapping core logic
  - Async wrapper using `asyncio.to_thread()` or `anyio.to_thread.run_sync()`
  - Per-site error detection: status_code, message, response_url
  - Regex validation before probing
- **Verification:** `harness_probe_username("blue")` returns results for 400+ sites in <60s
- **Rollback:** revert probe.py

### Atom 2.2: Implement targeted probe tool
- **Owner:** executor
- **Risk:** LOW — subset of Atom 2.1
- **Inputs:** Atom 2.1 output
- **Outputs:** `harness_probe_sites(username, sites=['GitHub','Twitter','Reddit'])` tool
  - Filters site manifest by name match
  - Returns only requested sites
- **Verification:** targeted probe returns exactly the requested sites
- **Rollback:** comment out tool registration

### Atom 2.3: Add rate limiting and politeness
- **Owner:** executor
- **Risk:** LOW — standard pattern
- **Inputs:** Sherlock's existing rate limiting (none — needs adding)
- **Outputs:** Rate limiter with configurable requests/sec, per-domain delay, max concurrent probes
- **Verification:** rate limiter blocks when threshold exceeded
- **Rollback:** remove rate limiter wrapper

### Atom 2.4: Register in RRF pipeline
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** `rrf_fusion.py`
- **Outputs:** sherlock-probe backend registered with weight 0.8, 24h cache TTL
- **Verification:** RRF query for identity tasks includes sherlock results
- **Rollback:** remove backend from RRF config

---

## Phase 3 — Scrapecraft Pattern Learning Channel (2.5 days)

### Atom 3.1: Port PatternLearner
- **Owner:** executor
- **Risk:** HIGH — PatternLearner is 23,428 lines, tightly coupled to Scrapecraft's DB/Redis/OpenRouter stack
- **Inputs:** `/home/roshan/scrapecraft/backend/app/services/pattern_learner.py`
- **Outputs:** `tools/pattern.py` with:
  - `PatternLearner` class (extracted core logic, no DB/Redis deps)
  - `learn_pattern(url, examples: list[dict])` — given 2-3 (field, value) examples, learns CSS selectors
  - `apply_pattern(pattern_id, url)` — applies learned selectors to new URL
  - Pattern storage in NEXUS DB (SQLite)
- **Verification:** learn pattern from 2 examples, apply to 3rd URL, get correct values
- **Rollback:** revert pattern.py

### Atom 3.2: Integrate ScrapeGraph (or local LLM fallback)
- **Owner:** executor
- **Risk:** MEDIUM — ScrapeGraph API key required, may not be available
- **Inputs:** `/home/roshan/scrapecraft/backend/app/services/scrapegraph.py`
- **Outputs:** `tools/semantic.py` with:
  - `semantic_extract(url, fields: list[str])` — LLM-based extraction from raw HTML
  - Fallback: if ScrapeGraph API unavailable, use local LLM via OpenRouter (Kimi K2)
  - Returns: `{url, fields: {field: value}, confidence, method}`
- **Verification:** extract 3 fields from a news article, all non-null
- **Rollback:** revert semantic.py

### Atom 3.3: Implement unified extraction tool
- **Owner:** executor
- **Risk:** LOW — wraps Atoms 3.1 + 3.2
- **Inputs:** Atoms 3.1, 3.2
- **Outputs:** `harness_extract(url, fields: dict[str, str])` tool
  - If pattern exists for URL domain → apply pattern
  - Else → semantic extraction
  - Auto-learns pattern from successful semantic extractions
- **Verification:** extract same fields from 2 similar pages, second is faster (pattern cached)
- **Rollback:** comment out tool registration

### Atom 3.4: Register in RRF pipeline
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** `rrf_fusion.py`
- **Outputs:** scrapecraft-pattern backend registered with weight 1.5, persistent cache
- **Verification:** RRF query includes pattern results
- **Rollback:** remove backend from RRF config

---

## Phase 4 — Scrapling Spider Channel (3 days)

### Atom 4.1: Port CrawlerEngine core
- **Owner:** executor
- **Risk:** HIGH — CrawlerEngine is 440 lines with complex async orchestration (task groups, memory streams, checkpointing)
- **Inputs:** `/home/roshan/Scrapling/scrapling/spiders/engine.py`, `spider.py`, `scheduler.py`, `cache.py`
- **Outputs:** `tools/spider.py` with:
  - `SpiderSession` class wrapping `CrawlerEngine` + `Scheduler` + `CheckpointManager`
  - `harness_spider_start(url, max_pages=50, same_domain=True)` — starts crawl in background
  - `harness_spider_status(spider_id)` — returns pages_crawled, queue_size, errors
  - `harness_spider_results(spider_id)` — returns all crawled pages with extracted text
- **Verification:** start spider on a small site (5 pages), verify all pages crawled
- **Rollback:** revert spider.py

### Atom 4.2: Implement checkpoint/resume
- **Owner:** executor
- **Risk:** MEDIUM — checkpoint system uses anyio file I/O
- **Inputs:** `scrapling/spiders/checkpoint.py`, `scrapling/spiders/cache.py`
- **Outputs:** Checkpoint save/restore in `SpiderSession`
  - Checkpoints saved to `~/.hermes/cache/spiders/<spider_id>/`
  - Resume on restart if checkpoint exists
- **Verification:** kill spider mid-crawl, restart, verify it resumes from checkpoint
- **Rollback:** disable checkpointing

### Atom 4.3: Add politeness controls
- **Owner:** executor
- **Risk:** LOW — Scrapling already has domain delays, robots.txt
- **Inputs:** `scrapling/spiders/engine.py` (domain limiters, robots.txt manager)
- **Outputs:** Configurable: `download_delay`, `concurrent_requests`, `robots_txt_obey`, `max_blocked_retries`
- **Verification:** spider respects robots.txt disallow rules
- **Rollback:** reset to defaults

### Atom 4.4: Register in RRF pipeline
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** `rrf_fusion.py`
- **Outputs:** scrapling-spider backend registered with weight 1.0, per-session cache
- **Verification:** RRF query includes spider results
- **Rollback:** remove backend from RRF config

---

## Phase 5 — RRF Fusion Integration (1 day)

### Atom 5.1: Implement RRF fusion engine
- **Owner:** executor
- **Risk:** MEDIUM — must handle 7 backends with different response shapes
- **Inputs:** Atoms 0.3, 1.4, 2.4, 3.4, 4.4
- **Outputs:** `rrf_fusion.py` with:
  - `RRFFuser` class managing 7 backends
  - `fuse(query, backends=None)` — runs selected backends, fuses via RRF
  - Dedup: URL canonicalization + content SHA-256 hash
  - Weights: NEXUS_DB=1.0, agent_reach=1.2, memory_graph=1.0, scrapling_fetch=1.2, scrapling_spider=1.0, scrapecraft_pattern=1.5, sherlock_probe=0.8
  - Timeout per backend: 10s (fetch/spider), 30s (pattern), 60s (probe)
- **Verification:** fuse("quantum computing") returns results from 3+ backends, no duplicates
- **Rollback:** revert to old 3-backend RRF

### Atom 5.2: Add cache layer
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** existing cache patterns
- **Outputs:** `cache.py` with:
  - SQLite-backed cache at `~/.hermes/cache/harness/`
  - Per-backend TTL: fetch=5min, spider=session, pattern=persistent, probe=24h
  - Cache key: `{backend}:{query_hash}`
  - Cache invalidation: manual + TTL expiry
- **Verification:** same query twice, second returns from cache (faster)
- **Rollback:** disable cache

### Atom 5.3: Wire into context-harness MCP server
- **Owner:** executor
- **Risk:** LOW — final wiring
- **Inputs:** all previous atoms
- **Outputs:** All 8 MCP tools registered in `server.py`:
  1. `harness_fetch`
  2. `harness_fetch_batch`
  3. `harness_fetch_stealth`
  4. `harness_probe_username`
  5. `harness_probe_sites`
  6. `harness_extract`
  7. `harness_spider_start`
  8. `harness_spider_results`
- **Verification:** `hermes tools` lists all 8 tools
- **Rollback:** comment out tool registrations

---

## Phase 6 — Testing & QA (1.5 days)

### Atom 6.1: Unit tests for fetch channel
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** Phase 1 code
- **Outputs:** `tests/test_fetch.py` — 10+ tests covering:
  - Static fetch returns content
  - Auto-fallback on 403
  - Batch fetch returns all
  - Timeout handling
  - Error responses
- **Verification:** `pytest tests/test_fetch.py -v` — all pass

### Atom 6.2: Unit tests for probe channel
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** Phase 2 code
- **Outputs:** `tests/test_probe.py` — 8+ tests covering:
  - Username validation (regex check)
  - Single site probe
  - Multi-site probe
  - Rate limiter
  - Error handling
- **Verification:** `pytest tests/test_probe.py -v` — all pass

### Atom 6.3: Unit tests for pattern channel
- **Owner:** executor
- **Risk:** MEDIUM — pattern learning is hard to test without real pages
- **Inputs:** Phase 3 code
- **Outputs:** `tests/test_pattern.py` — 8+ tests covering:
  - Pattern learning from examples
  - Pattern application
  - Semantic extraction fallback
  - Pattern storage/retrieval
- **Verification:** `pytest tests/test_pattern.py -v` — all pass

### Atom 6.4: Unit tests for spider channel
- **Owner:** executor
- **Risk:** MEDIUM — spider tests need a local test server
- **Inputs:** Phase 4 code
- **Outputs:** `tests/test_spider.py` — 8+ tests covering:
  - Spider starts and crawls
  - URL dedup works
  - Checkpoint save/restore
  - robots.txt compliance
  - Domain limiting
- **Verification:** `pytest tests/test_spider.py -v` — all pass

### Atom 6.5: Integration tests for RRF fusion
- **Owner:** executor
- **Risk:** MEDIUM
- **Inputs:** Phase 5 code
- **Outputs:** `tests/test_rrf.py` — 6+ tests covering:
  - RRF with 2 backends
  - RRF with all 7 backends
  - Dedup removes duplicates
  - Cache hit/miss
  - Timeout handling
  - Error isolation (one backend fails, others still return)
- **Verification:** `pytest tests/test_rrf.py -v` — all pass

### Atom 6.6: Coverage report
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** all test atoms
- **Outputs:** coverage report showing ≥80% line coverage
- **Verification:** `pytest --cov=context_harness --cov-report=term-missing`
- **Rollback:** N/A

---

## Phase 7 — Verification (0.5 days)

### Atom 7.1: Verify each phase output
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** all phase outputs
- **Outputs:** `verification-report.md` with:
  - Each phase's outputs checked against planned outputs
  - Test results summary
  - Coverage report
  - Any deviations documented
- **Verification:** report shows all phases green
- **Rollback:** N/A

### Atom 7.2: Performance benchmark
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** all channels
- **Outputs:** `performance-matrix.md` with:
  - Fetch latency p50/p95 (static, dynamic, stealth)
  - Probe time for 400+ sites
  - Pattern learn time
  - Spider crawl rate (pages/sec)
  - RRF fusion time (3 vs 7 backends)
  - Memory usage per channel
- **Verification:** all metrics recorded
- **Rollback:** N/A

---

## Phase 8 — Audit & Learnings (0.5 days)

### Atom 8.1: Execution audit
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** all phases
- **Outputs:** `audit-report.md` with:
  - Plan vs actual comparison
  - Deviations and root causes
  - Token usage per phase
  - Time spent per phase
  - Recommendations for future
- **Verification:** report complete
- **Rollback:** N/A

### Atom 8.2: Save learnings to NEXUS
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** audit report
- **Outputs:** Learning notes in NEXUS via `mcp_nexus_add_learning_note`
- **Verification:** learning notes searchable
- **Rollback:** N/A

### Atom 8.3: Update research-channel skill
- **Owner:** executor
- **Risk:** LOW
- **Inputs:** all phase outputs
- **Outputs:** Updated `research-channel` skill with implementation details, pitfalls, commands
- **Verification:** `skill_view("research-channel")` shows updated content
- **Rollback:** revert skill via `skill_manage(action='patch')`

---

## DAG Dependencies

```
Phase 0 (Foundation)
  ├── Atom 0.1 (deps check)
  ├── Atom 0.2 (scaffold)
  └── Atom 0.3 (RRF interface)
       │
Phase 1 (Fetch) ── Phase 2 (Probe) ── Phase 3 (Pattern) ── Phase 4 (Spider)
  ├── Atom 1.1      ├── Atom 2.1      ├── Atom 3.1      ├── Atom 4.1
  ├── Atom 1.2      ├── Atom 2.2      ├── Atom 3.2      ├── Atom 4.2
  ├── Atom 1.3      ├── Atom 2.3      ├── Atom 3.3      ├── Atom 4.3
  └── Atom 1.4      └── Atom 2.4      └── Atom 3.4      └── Atom 4.4
       │                 │                 │                 │
       └─────────────────┴─────────────────┴─────────────────┘
                              │
                         Phase 5 (RRF Fusion)
                          ├── Atom 5.1
                          ├── Atom 5.2
                          └── Atom 5.3
                              │
                         Phase 6 (Testing)
                          ├── Atom 6.1-6.5
                          └── Atom 6.6
                              │
                         Phase 7 (Verification)
                          ├── Atom 7.1
                          └── Atom 7.2
                              │
                         Phase 8 (Audit)
                          ├── Atom 8.1
                          ├── Atom 8.2
                          └── Atom 8.3
```

Phases 1-4 are **parallelizable** — each wraps a different project. Phase 5 requires all 4 to be complete. Phases 6-8 are sequential.

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Scrapling anyio conflicts with Hermes event loop | MEDIUM | HIGH | Use `anyio.to_thread.run_sync()` for blocking calls |
| PatternLearner too coupled to Scrapecraft stack | HIGH | MEDIUM | Extract only the CSS selector learning logic, replace DB/Redis with NEXUS SQLite |
| Sherlock synchronous probe blocks async pipeline | MEDIUM | MEDIUM | Wrap in `asyncio.to_thread()` with timeout |
| ScrapeGraph API key unavailable | MEDIUM | LOW | Fallback to local LLM via OpenRouter |
| Spider checkpoint file I/O conflicts | LOW | MEDIUM | Use atomic writes (write to .tmp, rename) |
| RRF with 7 backends exceeds latency budget | LOW | MEDIUM | Parallel execution with per-backend timeout, cache hot results |

---

## Resource Requirements

- **Disk:** ~50MB for Scrapling + Sherlock data files + cache
- **Memory:** ~200MB for Chrome dynamic fetcher (only when used)
- **Network:** Outbound HTTP to target URLs, ScrapeGraph API (optional)
- **Python deps:** scrapling, anyio, orjson, httpx, playwright (for dynamic fetch)
- **Existing deps reused:** NEXUS SQLite, OpenRouter client

---

## Rollback Plan

If any phase fails verification:
1. Revert the specific atom's files
2. If RRF registered, remove the backend from config
3. If MCP tools registered, comment out in server.py
4. Restart gateway: `hermes gateway restart`
5. Verify old 3-backend RRF still works

Full rollback: `git revert` on the context_harness plugin directory, restart gateway.
