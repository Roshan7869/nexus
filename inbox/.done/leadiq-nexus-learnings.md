# LeadIQ Collector Resurrection — NEXUS Learnings

## What We Built

We resurrected a dead collector infrastructure (35 collectors, only 1 working) by merging patterns from 5 external projects using a systematic 9-phase typed DAG execution plan.

## Pattern Sources & Logic

### 1. Scrapling (pip library)
**Location:** `/home/roshan/Scrapling`
**What we used:**
- `AsyncFetcher` — async HTTP fetcher with curl_cffi Chrome TLS fingerprinting. Replaced raw `httpx` in all collectors.
- `StealthyFetcher.async_fetch()` — async stealth browser with Cloudflare Turnstile solver. Replaced fragile Playwright `StealthSession`.
- `ProxyRotator` + `cyclic_rotation()` — thread-safe proxy rotation with pluggable strategies.
- `Scheduler` — async priority queue with URL SHA1 fingerprint dedup for crawl management.
- `CheckpointManager` — pickle-based pause/resume for long crawls.
- `is_proxy_error()` — classifies proxy failures (timeout, tunnel, DNS).

**Logic:** Replace fragile custom browser automation with production-grade anti-detection library. Async-native, tree-shakeable (can use just `AsyncFetcher.get()` or full `Spider` framework).

### 2. sherlock (username probe engine)
**Location:** `/home/roshan/sherlock`
**What we used:**
- `QueryStatus` enum (CLAIMED, AVAILABLE, UNKNOWN, ILLEGAL, WAF) → ported to `JobStatus` (LIVE, DEAD, UNCERTAIN, INVALID, BLOCKED).
- 3-tier classification engine (`sherlock.py:377-451`): WAF gate → message match → status code → response URL.
- WAF fingerprint list (Cloudflare, AWS WAF, PerimeterX).
- Error context mapper (HTTP error, proxy error, connection error, timeout, unknown).

**Logic:** Exhaustive classification taxonomy prevents ambiguous results. Tier ordering matters — WAF check first (short-circuits), then message match, then status code, then URL redirect. Each tier is a fallback for the previous.

### 3. career-ops (job URL liveness checker)
**Location:** `/home/roshan/career-ops`
**What we used:**
- `check-liveness.mjs` — 16 expired patterns across 3 languages, 8 apply patterns, `MIN_CONTENT_CHARS = 300`.
- `checkUrl()` pipeline: HTTP status → SPA hydration wait → URL redirect check → apply button detection (FIRST) → expired pattern match → content length gate.
- ATS career URL patterns (Ashby, Greenhouse, Lever, Workable).

**Logic:** Apply button must be checked BEFORE expired patterns to avoid Workday false positives. Check order is debugged by real usage across 50+ ATS platforms.

### 4. scrapecraft (scraping framework with pattern learning)
**Location:** `/home/roshan/scrapecraft`
**What we used:**
- `scraping_service_enhanced.py` — `ScrapingError` hierarchy, `tenacity @retry` with exponential backoff, `asyncio.Semaphore` concurrency limiting, `asyncio.wait_for` timeout enforcement, URL validation via HEAD requests.
- `pattern_learner.py` — `_extract_domain()`, `_classify_data_type()` (detects "jobs" by field name analysis), `suggest_optimizations()` (concurrency, retry, JS rendering, rate limiting), `_get_domain_knowledge()` (cached Redis-backed), `get_field_suggestions()`, `get_extraction_tips()`.

**Logic:** Per-domain success tracking enables adaptive fetch mode selection. Domains with >80% success get static fetch (fast). Domains with <20% success get stealth (WAF bypass). Domains with repeated failures get auto-disabled.

### 5. gemma4-browser-extension (content scoring engine)
**Location:** `/home/roshan/gemma4-browser-extension`
**What we used:**
- `cosine_similarity()` — pure math port from TypeScript to Python.
- Max-pooled sentence scoring — embed each sentence, take max similarity per section.
- Multi-vector aggregation — score title, company, description independently, use `max` for composite.
- `extractWebsiteParts.ts` — HTML → structured sections with hierarchical IDs.
- LLM output cleaner — regex-based JSON normalization for structured LLM output parsing.

**Logic:** Pre-LLM deterministic scoring filters 40-60% of non-matching posts before hitting expensive LLM calls. Section-level scoring captures "any sentence matches" rather than requiring full-document match.

## Architecture Decisions

### Why ScraplingAdapter instead of direct httpx?
- httpx fails against WAF (Indeed returned 403). Scrapling's curl_cffi impersonates Chrome TLS fingerprint.
- Scrapling provides built-in retry, proxy rotation, and Cloudflare solving.
- One adapter class replaces 16 copy-pasted `_stealth_headers()` implementations.

### Why typed DAG execution plan (POLARIS)?
- Each phase has clear inputs, outputs, guardrails, and resource mappings.
- Parallel execution where possible (Phases 2-5 independent).
- Checkpoint between every phase for resumability.
- Verification block at end ensures no regressions.

### Why consolidate 2 pipeline systems into 1?
- Path A (`IngestionOrchestrator` + `get_collectors()`) and Path B (`CrawlerOrchestrator` + `BaseCrawler`) overlapped with different interfaces (`collect()` vs `crawl(session)`).
- Chose Path A as the canonical path since it has Redis stream integration, dedup, and Celery scheduling.

### Why health monitoring as a service instead of endpoint?
- Running all 31 collectors is expensive (~2 minutes total).
- Health check uses 10s timeout per collector and caches results for 60s.
- CLI (`python3 -m backend.services.collector_health`) and API (`/api/collectors/health`) share same logic.

## Metrics

| Metric | Before | After |
|--------|--------|-------|
| Working collectors | 1 (Indeed) | 6+ (Reddit 700, SO 450, Internshala 147, HN 120, GH Issues 89, RSS 76) |
| Total posts per run | ~16 | ~1,582 |
| Silently broken collectors | 16 | 0 (all visible via health check) |
| Collector instantiation | 35 (4 crashed) | 31 (4 properly disabled) |
| Infrastructure layers | httpx + Playwright (mixed) | ScraplingAdapter (unified) |
| Lines of duplicated code | ~1,200 (16x _stealth_headers) | ~75 (1 consolidated class) |

## NEXUS Resource Indexing

This document captures the complete build logic for the LeadIQ collector resurrection. Index into NEXUS as:
- Resource type: `skill`
- Tags: `collector-architecture`, `scraper-resurrection`, `lead-generation`, `nexus-pattern-merge`
- Related resources: scrapecraft, sherlock, career-ops, Scrapling, gemma4-browser-extension

Key reusable patterns:
1. **Multi-project pattern merge** — Extract algorithmic logic from external projects, not wholesale imports.
2. **Typed DAG execution** — Every phase has type, guardrails, resources, verification.
3. **Health-first infrastructure** — Measure every collector before fixing it.
4. **Fallback everywhere** — Static→Stealth→Error chain, demo data fallback in frontend.
