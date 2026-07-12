# Context Harness Quality Upgrade — Execution Plan v2

**Plan ID:** 57v2 (NEXUS)  
**Supersedes:** v1 (`context-harness-execution-plan.md`)  
**Project:** hermes  
**Created:** 2026-07-01  
**Method:** First-Principles Deconstruction → 4 Parallel Source-Code Audits → Anti-Hallucination Grounding  
**Total atoms:** 28  
**Estimated effort:** 8-12 days  

---

## CRITICAL CORRECTIONS FROM v1 RESEARCH

The v1 plan was written without reading the actual source code of the three target projects. Four parallel deep-audit agents have now verified the real APIs. The following corrections are MANDATORY:

| v1 Assumption | Verified Reality | Impact |
|---|---|---|
| "Build MCP server scaffold from scratch" | **Scrapling already ships a complete MCP server** (`ScraplingMCPServer` in `scrapling/core/ai.py`, 9 tools, FastMCP, Pydantic models, stdio+HTTP transports) | Phase 1 changes from "build" to "wrap/proxy" — 60% effort reduction |
| "PatternLearner learns CSS selectors from examples" | **PatternLearner does NOT learn selectors.** It is a Redis-backed statistics aggregator with 3 hardcoded seed patterns (ecommerce/news/weather). No ML, no LLM, no DOM inspection. 549 lines. | Phase 3 must either (a) scope claims to "scraping statistics & heuristic tips" or (b) add genuine selector inference as net-new code |
| "Sherlock is async-compatible" | **`sherlock()` is synchronous**, uses `requests_futures.FuturesSession` (threaded, 20 workers). Blocks the calling thread. Module-global `globvar` makes `QueryNotifyPrint` unsafe for concurrent use. | Phase 2 must use `asyncio.to_thread()` wrapper + custom `QueryNotify` subclass |
| "RRF fusion has a pluggable backend interface" | **`fusion.py` `fuse()` takes 3 hardcoded positional lists** (nexus, internet, memory). No registry, no plugin pattern. Adding backends requires editing `fusion.py` + `engine.py`. | Phase 0 MUST add a `BackendProtocol` + registry before any new channels can be added |

---

## VERIFIED SOURCE CODE REFERENCES

All code paths, class names, method signatures, and line counts in this plan are verified by reading the actual source files:

| Project | Path | Key File(s) Audited | Lines |
|---|---|---|---|
| **Scrapling** | `/home/roshan/Scrapling/` | `scrapling/core/ai.py` (MCP server), `scrapling/fetchers/requests.py`, `scrapling/fetchers/chrome.py`, `scrapling/fetchers/stealth_chrome.py`, `scrapling/engines/toolbelt/custom.py` (Response), `scrapling/spiders/engine.py`, `scrapling/spiders/spider.py`, `scrapling/spiders/result.py`, `scrapling/spiders/checkpoint.py` | ~15,000 |
| **Sherlock** | `/home/roshan/sherlock/` | `sherlock_project/sherlock.py` (932 lines, `sherlock()` function lines 170-502), `sherlock_project/sites.py` (260 lines), `sherlock_project/result.py` (89 lines), `sherlock_project/notify.py` (279 lines), `sherlock_project/resources/data.json` (3,283 lines, 478 sites) | ~1,550 |
| **Scrapecraft** | `/home/roshan/scrapecraft/` | `backend/app/services/pattern_learner.py` (549 lines), `backend/app/services/scrapegraph.py` (177 lines), `backend/app/services/database.py` (20 lines), `backend/app/config.py` (35 lines), `backend/requirements.txt` | ~781 |
| **Context-Harness** | `/home/roshan/nexus/server/context_harness/` | ALL 30 Python files (9,499 lines total). Key: `server.py` (97 lines, 4 MCP tools), `engine.py` (277 lines), `fusion.py` (59 lines), `agent_reach.py` (155 lines, 13 platforms), `gcode_backend.py` (94 lines), `nexus_bridge.py` (82 lines) | 9,499 |

---

## PHASE 0 — Fusion Refactor & Backend Registry (1 day)

**Goal:** Refactor `fusion.py` from hardcoded 3-backend positional lists to a plugin/registry pattern so new backends can be added without editing core code.

**Why this is Phase 0:** The current `fuse()` function signature is `fuse(nexus_results, internet_results, memory_results, max_results)` — three positional list parameters. You CANNOT add a 4th backend without changing this signature, which means changing `engine.py` too. This is the single blocker for all subsequent phases.

### Atom 0.1: Define `BackendProtocol` and `BackendRegistry`

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/backend_protocol.py` (NEW) |
| **Risk** | LOW — pure Python, no external deps |
| **Verified context** | Current `fusion.py` line 7: `def fuse(nexus_results, internet_results, memory_results, max_results=10)` — hardcoded 3 lists. Current `engine.py` lines 197-265: `harness()` builds 3 lists and passes them positionally. |

**Implementation:**

```python
from typing import Protocol, runtime_checkable
from server.context_harness.models import HarnessResult

@runtime_checkable
class BackendProtocol(Protocol):
    """Protocol that all RRF backends must implement."""
    name: str           # unique backend identifier
    weight: float       # RRF weight multiplier (default 1.0)
    cache_ttl: int      # cache TTL in seconds (0 = no cache)

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        """Execute a search and return ranked results."""
        ...

    def is_available(self) -> bool:
        """Check if this backend is currently operational."""
        ...


class BackendRegistry:
    """Registry for RRF backends. Backends self-register on import."""
    _backends: dict[str, BackendProtocol] = {}

    @classmethod
    def register(cls, backend: BackendProtocol) -> None:
        if backend.name in cls._backends:
            raise ValueError(f"Backend '{backend.name}' already registered")
        cls._backends[backend.name] = backend

    @classmethod
    def get(cls, name: str) -> BackendProtocol | None:
        return cls._backends.get(name)

    @classmethod
    def all(cls) -> dict[str, BackendProtocol]:
        return dict(cls._backends)

    @classmethod
    def available(cls) -> list[BackendProtocol]:
        return [b for b in cls._backends.values() if b.is_available()]
```

**Verification:** `python -c "from server.context_harness.backend_protocol import BackendProtocol, BackendRegistry; print('OK')"` succeeds  
**Rollback:** `rm backend_protocol.py`

### Atom 0.2: Refactor `fusion.py` to use registry

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/fusion.py` (MODIFY — currently 59 lines) |
| **Risk** | MEDIUM — must maintain backward compatibility with existing `engine.py` calls |
| **Verified context** | Current `fuse()` iterates 3 hardcoded lists in order: nexus, internet, memory. Dedup key = `f"{source_name}:{r.content[:80]}"`. Score = `r.score * (1.0/(RRF_K + i))`. `RRF_K = 60.0`. `rank_by_relevance()` exists but is dead code (never called). |

**Changes:**
1. Keep `fuse()` with old signature as backward-compat wrapper (deprecated).
2. Add `fuse_registered(query: str, backends: list[BackendProtocol], max_results: int = 10) -> list[HarnessResult]`:
   - For each available backend: call `backend.search(query, max_results)`.
   - Apply `backend.weight` as score multiplier.
   - RRF scoring: `score = raw_score * weight * (1.0 / (RRF_K + rank_index))`.
   - Dedup key: `hashlib.sha256(content[:200].encode()).hexdigest()`.
   - Merge duplicates: sum scores, merge metadata, keep first source name.
   - Sort descending, truncate to `max_results`.
3. Per-backend timeout: `asyncio.wait_for(backend.search(...), timeout=backend.timeout_seconds)`.

**Verification:** Unit test: 3 mock backends → `fuse_registered("test", mocks, 10)` returns fused results with no duplicates.  
**Rollback:** `git checkout fusion.py`

### Atom 0.3: Refactor `engine.py` `harness()` to use registry

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/engine.py` (MODIFY — currently 277 lines) |
| **Risk** | MEDIUM — `harness()` is the MCP-exposed path; must not break existing behavior |
| **Verified context** | `harness()` at lines 197-265: splits `sources` on `,`, branches into 3 if-blocks (nexus/internet/memory), builds 3 lists, calls `fuse(nexus, internet, memory, max_results)`. `source_used` tracks which branches produced results. |

**Changes:**
1. Keep existing 3 branches as-is (backward compat) but wrap each as a `LegacyBackend` adapter implementing `BackendProtocol`.
2. Add new path: if `sources == "all"`, call `fuse_registered(query, BackendRegistry.available(), max_results)`.
3. If `sources` contains specific backend names (e.g. `"scrapling-fetch"`, `"sherlock-probe"`), filter registry by name.

**Verification:** `context_harness("test query")` MCP tool still returns same-shaped `HarnessResponse`. New backends appear in results if registered.  
**Rollback:** `git checkout engine.py`

### Atom 0.4: Migrate existing 3 backends to `BackendProtocol`

| Aspect | Detail |
|---|---|
| **Files** | 3 new adapter files in `/home/roshan/nexus/server/context_harness/backends/` |
| **Risk** | LOW — wrapping existing functions, no logic changes |
| **Verified context** | `nexus_bridge.search_resources()` (LIKE search on SQLite, lines 15-40 of nexus_bridge.py). `agent_reach.route_to_channel()` (keyword scoring, returns dict, no fetching — lines 133-155). `gcode_backend.search()` (always falls back to `search_learnings`, lines 54-66). |

**Three adapter files:**
- `backends/nexus_backend.py` — wraps `nexus_bridge.search_resources` + `search_learnings`. weight=1.0, cache_ttl=0.
- `backends/internet_backend.py` — wraps `agent_reach.route_to_channel` + `doctor`. weight=1.2, cache_ttl=300.
- `backends/memory_backend.py` — wraps `gcode_backend.search`. weight=1.0, cache_ttl=0.

Each adapter: `async def search()` wraps sync calls in `asyncio.to_thread()`. `is_available()` returns True (nexus/memory) or probes (internet).

**Verification:** `BackendRegistry.available()` returns exactly 3 backends. `fuse_registered("test", all_backends, 10)` returns same results as old `fuse()`.  
**Rollback:** `rm -rf backends/`

---

## PHASE 1 — Scrapling Fetch Channel (1.5 days)

**Goal:** Add Scrapling's web fetching capability as a new RRF backend.

**KEY INSIGHT FROM RESEARCH:** Scrapling already has a complete MCP server (`ScraplingMCPServer` in `scrapling/core/ai.py`) with 9 tools. We do NOT build an MCP server. We wrap the fetcher API directly as a `BackendProtocol` and register it in the RRF pipeline.

### Atom 1.1: Scrapling fetcher backend adapter

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/backends/scrapling_fetch.py` (NEW) |
| **Risk** | MEDIUM — Scrapling uses `anyio` internally; calling from asyncio context requires care |
| **Verified context** | `Fetcher.get(url, **kwargs) -> Response` (sync, `scrapling/fetchers/requests.py`). `AsyncFetcher.get(url, **kwargs) -> Response` (async). `Response` extends `Selector` with `.status`, `.reason`, `.cookies`, `.headers`, `.body`, `.html_content`, `.text`, `.get_all_text()`, `.css()`, `.xpath()`. Package install: `pip install "scrapling[fetchers]"`. Import path: `from scrapling.fetchers import AsyncFetcher`. |

**Implementation:**
```python
from scrapling.fetchers import AsyncFetcher
from server.context_harness.backend_protocol import BackendProtocol
from server.context_harness.models import HarnessResult

class ScraplingFetchBackend:
    name = "scrapling-fetch"
    weight = 1.2
    cache_ttl = 300  # 5 min
    timeout_seconds = 10

    def is_available(self) -> bool:
        try:
            import scrapling
            return True
        except ImportError:
            return False

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        # Treat query as URL if it looks like one; otherwise skip
        query = query.strip()
        if not (query.startswith("http://") or query.startswith("https://")):
            return []
        try:
            response = await AsyncFetcher.get(
                query,
                timeout=30,
                stealthy_headers=True,
                follow_redirects="safe",
            )
            content = response.get_all_text(separator="\n", strip=True, valid_values=True)
            title = response.css("title::text").get(default="") if response.css("title") else ""
            return [HarnessResult(
                source="scrapling-fetch",
                content=f"[{response.status}] {title}\n{content[:2000]}",
                score=1.0 if response.status == 200 else 0.3,
                metadata={
                    "url": query,
                    "status": response.status,
                    "fetch_mode": "static",
                },
            )]
        except Exception as e:
            return [HarnessResult(
                source="scrapling-fetch",
                content=f"Fetch error: {e}",
                score=0.0,
                metadata={"url": query, "error": str(e)},
            )]
```

**Verification:** `AsyncFetcher.get("https://example.com")` returns Response with status 200. Backend registered in `BackendRegistry`.  
**Rollback:** `rm backends/scrapling_fetch.py`

### Atom 1.2: Stealth fetch with auto-fallback

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapling_fetch.py` (EXTEND) |
| **Risk** | MEDIUM — StealthyFetcher requires `patchright` browser, needs `scrapling install` |
| **Verified context** | `StealthyFetcher.async_fetch(url, **kwargs)` in `scrapling/fetchers/stealth_chrome.py`. Extra kwargs: `hide_canvas`, `block_webrtc`, `allow_webgl`, `solve_cloudflare`. `DynamicFetcher.async_fetch(url, **kwargs)` in `scrapling/fetchers/chrome.py` (Playwright, needs `playwright install`). Auto-fallback chain: static (AsyncFetcher) → dynamic (DynamicFetcher) → stealth (StealthyFetcher) on 403/429/503. |

**Implementation:** Extend `search()` method:
1. Try `AsyncFetcher.get()` first (fastest, curl_cffi).
2. If status in `{403, 429, 503}` or exception: try `DynamicFetcher.async_fetch()`.
3. If still blocked: try `StealthyFetcher.async_fetch(solve_cloudflare=True)`.
4. Record `fetch_mode` in metadata: `"static"` / `"dynamic"` / `"stealth"`.

**Verification:** Fetch a known 403 page (e.g. Cloudflare-protected site) → auto-fallback triggers → returns content from stealth fetch.  
**Rollback:** Revert to static-only fetch.

### Atom 1.3: Batch fetch tool

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapling_fetch.py` (EXTEND) |
| **Risk** | LOW — wraps Atom 1.1-1.2 with `asyncio.gather` + semaphore |
| **Verified context** | `AsyncFetcher` supports concurrent calls. Scrapling's own MCP server has `bulk_get` (line in `ai.py`) that uses `asyncio.gather`. |

**Implementation:**
```python
async def search_batch(self, urls: list[str], max_concurrent: int = 5) -> list[HarnessResult]:
    semaphore = asyncio.Semaphore(max_concurrent)
    async def _fetch_one(url):
        async with semaphore:
            return await self.search(url)
    results = await asyncio.gather(*[_fetch_one(u) for u in urls], return_exceptions=True)
    # Flatten and filter
```

**Verification:** Batch of 3 URLs returns 3 results. One failing URL doesn't kill others.  
**Rollback:** Remove `search_batch` method.

### Atom 1.4: Register in RRF pipeline + cache

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapling_fetch.py` + `engine.py` (MODIFY — add import) |
| **Risk** | LOW — config change |
| **Verified context** | `BackendRegistry.register()` from Atom 0.1. `fusion.fuse_registered()` from Atom 0.2. |

**Changes:**
1. At end of `scrapling_fetch.py`: `BackendRegistry.register(ScraplingFetchBackend())`.
2. In `engine.py` `__init__`: add `import server.context_harness.backends.scrapling_fetch  # noqa: F401` to trigger registration.
3. Cache: SQLite-backed, key = `f"scrapling-fetch:{hashlib.sha256(url.encode()).hexdigest()}"`, TTL = 300s.

**Verification:** `context_harness("https://example.com")` includes scrapling-fetch results in fused output. Same query twice → second hits cache.  
**Rollback:** Comment out import in `engine.py`.

---

## PHASE 2 — Sherlock Probe Channel (1 day)

**Goal:** Add username identity search across 478 sites as a new RRF backend.

**KEY INSIGHT FROM RESEARCH:** `sherlock()` is synchronous (uses `requests_futures.FuturesSession`, 20 threads). It blocks the calling thread. Must use `asyncio.to_thread()`. The `QueryNotify` base class can be subclassed for silent result collection — `QueryNotifyPrint` is unsafe (uses module-global `globvar`).

### Atom 2.1: Custom `QueryNotify` collector

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/backends/sherlock_probe.py` (NEW) |
| **Risk** | LOW — subclassing `QueryNotify` per verified pattern |
| **Verified context** | `QueryNotify` base class (`sherlock_project/notify.py` lines 14-104): `__init__(self, result=None)`, `start(message=None)`, `update(result)` (sets `self.result = result`), `finish(message=None)`. `QueryResult` (`sherlock_project/result.py` lines 30-89): attributes `username`, `site_name`, `site_url_user`, `status` (QueryStatus enum), `query_time` (float|None), `context` (str|None). `QueryStatus` enum values: CLAIMED, AVAILABLE, UNKNOWN, ILLEGAL, WAF. |

**Implementation:**
```python
from sherlock_project.notify import QueryNotify
from sherlock_project.result import QueryResult, QueryStatus

class Collector(QueryNotify):
    """Collects QueryResult objects silently. Thread-safe via list.append."""
    def __init__(self):
        super().__init__(result=None)
        self.results: list[QueryResult] = []

    def update(self, result: QueryResult) -> None:
        super().update(result)
        self.results.append(result)
```

**Verification:** Instantiate `Collector()`, call `.update(QueryResult(...))` 3 times → `len(collector.results) == 3`.  
**Rollback:** Remove class.

### Atom 2.2: Site manifest loader with local caching

| Aspect | Detail |
|---|---|
| **File** | `backends/sherlock_probe.py` (EXTEND) |
| **Risk** | LOW — wrapping `SitesInformation` |
| **Verified context** | `SitesInformation(data_file_path, honor_exclusions, do_not_exclude)` in `sherlock_project/sites.py` lines 79-206. If `data_file_path=None`, fetches from `https://data.sherlockproject.xyz` (network call). If local path, reads JSON file. Conversion pattern (verified from `sherlock.py` line 775): `site_data = {site.name: site.information for site in sites}`. 478 sites in `data.json`. 19 NSFW sites. `remove_nsfw_sites(do_not_remove)` mutates in place. `site_name_list()` returns sorted list. |

**Implementation:**
```python
from sherlock_project.sites import SitesInformation
from pathlib import Path
import json

# Vendored copy for offline use
LOCAL_MANIFEST = Path(__file__).parent / "data" / "sherlock_sites.json"

class SherlockProbeBackend:
    name = "sherlock-probe"
    weight = 0.8
    cache_ttl = 86400  # 24h
    timeout_seconds = 60

    _site_data_cache: dict | None = None

    def _get_site_data(self, sites_filter: list[str] | None = None) -> dict:
        if self._site_data_cache is None:
            path = str(LOCAL_MANIFEST) if LOCAL_MANIFEST.exists() else None
            sites = SitesInformation(
                data_file_path=path,
                honor_exclusions=True,
            )
            self._site_data_cache = {s.name: s.information for s in sites}
        if sites_filter:
            return {k: v for k, v in self._site_data_cache.items()
                    if k.lower() in [f.lower() for f in sites_filter]}
        return self._site_data_cache
```

**Verification:** `_get_site_data()` returns dict with ~459 keys (478 - 19 NSFW). Filter to `["GitHub", "Twitter"]` returns 2 keys.  
**Rollback:** Remove method.

### Atom 2.3: Async probe executor

| Aspect | Detail |
|---|---|
| **File** | `backends/sherlock_probe.py` (EXTEND) |
| **Risk** | MEDIUM — `sherlock()` blocks for 30-60s with 478 sites; must run in thread pool |
| **Verified context** | `sherlock()` signature (verified from `sherlock_project/sherlock.py` lines 170-177): `sherlock(username: str, site_data: dict, query_notify: QueryNotify, dump_response: bool=False, proxy: Optional[str]=None, timeout: int=60) -> dict[str, dict[str, str|QueryResult]]`. Return dict keys: `url_main`, `url_user`, `status` (QueryResult), `http_status` (str|int), `response_text` (bytes|str). Internally uses 20 worker threads via `SherlockFuturesSession`. |

**Implementation:**
```python
import asyncio
from sherlock_project.sherlock import sherlock

class SherlockProbeBackend:
    # ... (from Atom 2.2)

    def is_available(self) -> bool:
        try:
            import sherlock_project
            return True
        except ImportError:
            return False

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        # query must look like a username probe: "probe:username" or "username sites:GitHub,Twitter"
        username, sites_filter = self._parse_query(query)
        if not username:
            return []

        site_data = self._get_site_data(sites_filter)
        collector = Collector()

        # sherlock() is synchronous and blocks — run in thread pool
        results_dict = await asyncio.to_thread(
            sherlock,
            username,
            site_data,
            collector,
            False,    # dump_response
            None,     # proxy
            60,       # timeout
        )

        # Serialize results
        harness_results = []
        for site_name, r in results_dict.items():
            qr: QueryResult = r["status"]
            harness_results.append(HarnessResult(
                source="sherlock-probe",
                content=f"[{qr.status.value}] {site_name}: {r['url_user']}",
                score=1.0 if qr.status == QueryStatus.CLAIMED else 0.1,
                metadata={
                    "site": site_name,
                    "url": r["url_user"],
                    "status": qr.status.name,
                    "http_status": r["http_status"],
                    "query_time_s": qr.query_time,
                    "context": qr.context,
                    "username": username,
                },
            ))

        # Sort: CLAIMED first, then by query_time
        harness_results.sort(key=lambda x: (-x.score, x.metadata.get("query_time_s") or 999))
        return harness_results[:max_results]

    def _parse_query(self, query: str) -> tuple[str | None, list[str] | None]:
        """Parse 'probe:john sites:GitHub,Reddit' → ('john', ['GitHub', 'Reddit'])"""
        query = query.strip()
        # Pattern 1: "probe:username"
        if query.startswith("probe:"):
            rest = query[6:]
        elif query.startswith("username:"):
            rest = query[9:]
        else:
            return None, None

        # Check for site filter
        if " sites:" in rest:
            username, sites_part = rest.split(" sites:", 1)
            sites_filter = [s.strip() for s in sites_part.split(",") if s.strip()]
            return username.strip(), sites_filter
        return rest.strip(), None
```

**Verification:** `await backend.search("probe:blue")` returns results with `CLAIMED` status for sites where "blue" exists. Completes in <60s.  
**Rollback:** Remove `search()` and `_parse_query()`.

### Atom 2.4: Rate limiting and registration

| Aspect | Detail |
|---|---|
| **File** | `backends/sherlock_probe.py` (EXTEND) + `engine.py` (MODIFY) |
| **Risk** | LOW |
| **Verified context** | Sherlock has no built-in rate limiting. 20 concurrent threads hit 478 sites simultaneously. Per-domain delay is not implemented. |

**Changes:**
1. Add `_last_probe_time: float = 0` and `_min_probe_interval: float = 5.0` (5 seconds between probes).
2. In `search()`: `if time.time() - self._last_probe_time < self._min_probe_interval: return []`.
3. Register: `BackendRegistry.register(SherlockProbeBackend())`.
4. Import in `engine.py`: `import server.context_harness.backends.sherlock_probe  # noqa`.

**Verification:** `BackendRegistry.get("sherlock-probe")` returns instance. Two probes within 5s → second returns empty.  
**Rollback:** Comment out import.

---

## PHASE 3 — Scrapecraft Pattern Channel (2 days)

**Goal:** Add scraping statistics aggregation and semantic extraction as a new RRF backend.

**KEY INSIGHT FROM RESEARCH:** `PatternLearner` (549 lines) does NOT learn CSS selectors from HTML. It is a Redis-backed statistics aggregator with 3 hardcoded seed patterns (ecommerce/news/weather). It uses no ML/LLM. The ORM models (`ScrapingPattern`, `DomainKnowledge`) are dead code — never read or written. Two hard external deps to sever: `app.config.settings` (just for `REDIS_URL`) and `app.services.database.Base` (only used by dead ORM models). `scrapegraph.py` (177 lines) is a thin async wrapper around ScrapeGraphAI's hosted API (`scrapegraph_py.AsyncClient.smartscraper`).

**SCOPE CORRECTION:** This backend provides two capabilities:
1. **Scraping statistics & heuristic tips** — from PatternLearner (Redis-backed)
2. **Semantic extraction** — from ScrapeGraph API (LLM-powered, hosted)

It does NOT provide CSS selector inference. If selector inference is needed, it must be added as net-new code using Scrapling's `SelectorsGeneration` (`scrapling/core/mixins.py`) or LLM-based selector generation.

### Atom 3.1: Extract PatternLearner as standalone module

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/backends/scrapecraft_pattern.py` (NEW) |
| **Risk** | HIGH — decoupling from Scrapecraft's stack. 549 lines to extract. |
| **Verified context** | `PatternLearner.__init__()` (line 81-85): `self.redis_client = None`, `self.pattern_cache = {}`, `self.domain_cache = {}`, `self.learning_threshold = 5`. `initialize()` (line 87-97): connects Redis via `redis.asyncio.from_url(settings.REDIS_URL)`, calls `_load_pattern_cache()`. `_load_pattern_cache()` (lines 99-128): seeds 3 hardcoded patterns (ecommerce: `["h1", ".product-title", "[itemprop='name']"]`, news: `["h1", ".article-title", "article h2"]`, weather: `["h1", ".temperature", ".forecast"]`). `_extract_domain(url)` (lines 130-134): `urlparse(url).netloc`. `_classify_data_type(schema)` (lines 136-150): keyword matching on schema keys. Public methods: `learn_from_interaction()`, `learn_from_execution()`, `suggest_optimizations()`, `get_field_suggestions()`, `get_extraction_tips()`, `cleanup()`. All async. All use Redis for persistence. `unified_agent.py` line 71 imports non-existent `pipeline_repository` — PatternLearner is never reached in live server. |

**Implementation:**
1. Copy `PatternLearner` class + `_extract_domain` + `_classify_data_type` + seed patterns.
2. DELETE `ScrapingPattern(Base)` and `DomainKnowledge(Base)` ORM classes (dead code, lines 18-75).
3. DELETE `from app.config import settings` → replace with `__init__(self, redis_url: str | None = None, learning_threshold: int = 5)`.
4. DELETE `from app.services.database import Base` and `from sqlalchemy import ...` imports.
5. DELETE `from sqlalchemy.ext.asyncio import AsyncSession` (unused import).
6. If `redis_url is None`: skip Redis, use in-memory dict fallback for all Redis operations.
7. Keep all 7 public methods with same signatures.

**Verification:** `PatternLearner(redis_url=None).initialize()` succeeds without Redis. `get_extraction_tips("example.com")` returns tips list.  
**Rollback:** `rm scrapecraft_pattern.py`

### Atom 3.2: ScrapeGraph semantic extraction wrapper

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapecraft_pattern.py` (EXTEND) |
| **Risk** | MEDIUM — requires `SCRAPEGRAPH_API_KEY` env var. Fallback needed. |
| **Verified context** | `ScrapingService` in `scrapecraft/backend/app/services/scrapegraph.py` (177 lines). `__init__(self, api_key: str)`. `execute_pipeline(urls, schema, prompt) -> List[Dict]` — uses `scrapegraph_py.AsyncClient.smartscraper(website_url=url, user_prompt=prompt)`. Schema is IGNORED in current implementation (only prompt passed). Returns list of `{url, success, data, error}`. `search_urls(query, max_results=5) -> List[Dict]` — uses `client.searchscraper()`. Fallback: if `searchscraper` raises `AttributeError`, falls back to `smartscraper` on Google search URL + regex URL extraction. Per-URL error isolation via `asyncio.gather(return_exceptions=True)`. |

**Implementation:**
```python
import os
from typing import Optional

class SemanticExtractor:
    def __init__(self):
        self._api_key = os.environ.get("SCRAPEGRAPH_API_KEY")
        self._client = None

    def is_available(self) -> bool:
        return self._api_key is not None

    async def extract(self, url: str, fields: list[str]) -> dict:
        if not self.is_available():
            return {"url": url, "success": False, "error": "SCRAPEGRAPH_API_KEY not set"}
        from scrapegraph_py import AsyncClient
        prompt = f"Extract these fields as JSON: {', '.join(fields)}"
        async with AsyncClient(api_key=self._api_key) as client:
            result = await client.smartscraper(website_url=url, user_prompt=prompt)
            data = result.get("result", result) if isinstance(result, dict) else result.model_dump()
            return {"url": url, "success": True, "data": data}
```

**Verification:** If API key set: `extract("https://example.com", ["title", "description"])` returns dict with data. If not set: returns error dict.  
**Rollback:** Remove `SemanticExtractor` class.

### Atom 3.3: Unified pattern backend adapter

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapecraft_pattern.py` (EXTEND) |
| **Risk** | LOW — wraps Atoms 3.1 + 3.2 |

**Implementation:**
```python
class ScrapecraftPatternBackend:
    name = "scrapecraft-pattern"
    weight = 1.5
    cache_ttl = 0  # persistent (Redis)
    timeout_seconds = 30

    def __init__(self):
        self._learner = PatternLearner(redis_url=os.environ.get("REDIS_URL"))
        self._extractor = SemanticExtractor()
        self._initialized = False

    def is_available(self) -> bool:
        return True  # always available (in-memory fallback)

    async def _ensure_init(self):
        if not self._initialized:
            await self._learner.initialize()
            self._initialized = True

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        await self._ensure_init()
        results = []

        # If query looks like "extract:url field1,field2"
        if query.startswith("extract:"):
            parts = query[8:].split(" ", 1)
            if len(parts) == 2:
                url, fields_str = parts
                fields = [f.strip() for f in fields_str.split(",")]
                ext_result = await self._extractor.extract(url, fields)
                results.append(HarnessResult(
                    source="scrapecraft-pattern",
                    content=json.dumps(ext_result, indent=2),
                    score=1.0 if ext_result.get("success") else 0.0,
                    metadata={"type": "semantic_extraction", "url": url, "fields": fields},
                ))
                # Auto-learn from successful extraction
                if ext_result.get("success"):
                    await self._learner.learn_from_execution(
                        context={"urls": [url], "schema": {f: "" for f in fields}},
                        results=[ext_result],
                    )

        # If query looks like "tips:domain"
        elif query.startswith("tips:"):
            domain = query[5:].strip()
            tips = await self._learner.get_extraction_tips(domain)
            field_suggestions = await self._learner.get_field_suggestions(domain, "auto")
            results.append(HarnessResult(
                source="scrapecraft-pattern",
                content=f"Tips for {domain}:\n" + "\n".join(f"- {t}" for t in tips),
                score=0.7,
                metadata={"type": "extraction_tips", "domain": domain, "tips": tips,
                         "field_suggestions": field_suggestions},
            ))

        return results[:max_results]
```

**Verification:** `backend.search("tips:github.com")` returns extraction tips. `backend.search("extract:https://example.com title,description")` returns semantic extraction (if API key set).  
**Rollback:** Remove `ScrapecraftPatternBackend` class.

### Atom 3.4: Register in RRF pipeline

| Aspect | Detail |
|---|---|
| **File** | `engine.py` (MODIFY — add import) |
| **Risk** | LOW |

**Changes:** Add `import server.context_harness.backends.scrapecraft_pattern  # noqa: F401` to `engine.py`.

**Verification:** `BackendRegistry.get("scrapecraft-pattern")` returns instance. `context_harness("tips:github.com")` includes pattern results.  
**Rollback:** Comment out import.

---

## PHASE 4 — Scrapling Spider Channel (2 days)

**Goal:** Add web crawling capability as a new RRF backend.

**KEY INSIGHT FROM RESEARCH:** Scrapling's spiders are NOT exposed via its existing MCP server. The `Spider` class is an ABC requiring subclassing. `CrawlerEngine` handles async orchestration with `anyio` task groups, checkpointing via `CheckpointManager` (pickle files), URL dedup via SHA1 fingerprints, robots.txt compliance via `RobotsTxtManager` (protego), and per-domain capacity limiters. `CrawlResult` has `.stats: CrawlStats` and `.items: ItemList` (with `.to_json()`, `.to_jsonl()`). `Spider.start()` is sync (runs `anyio.run` internally). `Spider.stream()` is an async generator yielding items as scraped.

### Atom 4.1: Generic spider subclass

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/backends/scrapling_spider.py` (NEW) |
| **Risk** | HIGH — `Spider` is an ABC with `@abstractmethod parse()`. `CrawlerEngine` uses `anyio.create_task_group()` internally. Must not conflict with context-harness's own asyncio loop. |
| **Verified context** | `Spider` class (`scrapling/spiders/spider.py`): class attrs `name`, `start_urls`, `allowed_domains`, `robots_txt_obey`, `concurrent_requests=4`, `download_delay=0.0`, `max_blocked_retries=3`. Constructor: `Spider(crawldir=None, interval=300.0)`. Abstract method: `async def parse(self, response: Response) -> AsyncGenerator[Dict|Request|None, None]`. Override: `async def start_requests(self) -> AsyncGenerator[Request, None]`. Entry points: `start(use_uvloop=False) -> CrawlResult` (sync, runs `anyio.run`), `stream() -> AsyncGenerator[dict, None]` (async). `CrawlResult`: `stats: CrawlStats`, `items: ItemList`, `paused: bool`, `completed: bool`. `CrawlStats`: `requests_count`, `failed_requests_count`, `items_scraped`, `elapsed_seconds`, `requests_per_second`, `to_dict()`. `ItemList(list)`: `to_json(path)`, `to_jsonl(path)`. |

**Implementation:**
```python
from scrapling.spiders import Spider, Request, Response, CrawlResult
from typing import AsyncGenerator
import json

class HarnessSpider(Spider):
    """Generic spider that extracts text content from all crawled pages."""

    name = "harness_spider"

    def __init__(self, start_url: str, max_pages: int = 50, same_domain: bool = True,
                 crawldir: str | None = None, **kwargs):
        self._start_url = start_url
        self._max_pages = max_pages
        self._same_domain = same_domain
        self._pages_crawled = 0
        if same_domain:
            from urllib.parse import urlparse
            self.allowed_domains = {urlparse(start_url).netloc}
        super().__init__(crawldir=crawldir, **kwargs)

    async def start_requests(self) -> AsyncGenerator[Request, None]:
        yield Request(url=self._start_url, sid="default")

    async def parse(self, response: Response) -> AsyncGenerator[dict | Request | None, None]:
        self._pages_crawled += 1
        if self._pages_crawled > self._max_pages:
            return

        # Extract text content
        text = response.get_all_text(separator="\n", strip=True, valid_values=True)
        title = ""
        if response.css("title::text"):
            title = response.css("title::text").get(default="")

        yield {
            "url": response.url,
            "status": response.status,
            "title": title,
            "text": text[:5000],  # truncate for storage
        }

        # Follow links on the same domain
        if self._pages_crawled < self._max_pages:
            links = response.css("a::attr(href)").getall()
            for link in links:
                absolute = response.urljoin(link)
                if absolute.startswith("http") and self._is_allowed(absolute):
                    yield Request(url=absolute, sid="default")

    def _is_allowed(self, url: str) -> bool:
        if not self._same_domain:
            return True
        from urllib.parse import urlparse
        return urlparse(url).netloc in self.allowed_domains
```

**Verification:** `HarnessSpider("https://example.com", max_pages=3)` subclass instantiates without error.  
**Rollback:** Remove class.

### Atom 4.2: Spider session manager (start/status/results)

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapling_spider.py` (EXTEND) |
| **Risk** | MEDIUM — `Spider.start()` is sync (uses `anyio.run` internally). Must use `asyncio.to_thread()` to avoid blocking. `Spider.stream()` is async but requires its own event loop management. |
| **Verified context** | `Spider.start(use_uvloop=False) -> CrawlResult` — runs `anyio.run(self._run_engine)`, sets up SIGINT handler (1st Ctrl+C = graceful pause + checkpoint, 2nd = force stop). `CrawlResult.stats.elapsed_seconds`, `CrawlResult.stats.requests_per_second`, `CrawlResult.items` (ItemList, list of dicts). `CrawlResult.paused` (bool). `CheckpointManager(crawldir, interval=300.0)` — `has_checkpoint()`, `save(CheckpointData)`, `load() -> Optional[CheckpointData]`, `cleanup()`. |

**Implementation:**
```python
import asyncio
import time
import uuid
from pathlib import Path

SPIDER_CACHE_DIR = Path.home() / ".nexus" / "cache" / "spiders"

class SpiderSessionManager:
    """Manages background spider crawls."""
    _sessions: dict[str, dict] = {}  # session_id -> {spider, result, status, started_at}

    async def start_spider(self, url: str, max_pages: int = 50, same_domain: bool = True) -> str:
        session_id = str(uuid.uuid4())[:8]
        crawldir = str(SPIDER_CACHE_DIR / session_id)
        SPIDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        spider = HarnessSpider(
            start_url=url,
            max_pages=max_pages,
            same_domain=same_domain,
            crawldir=crawldir,
        )

        self._sessions[session_id] = {
            "spider": spider,
            "result": None,
            "status": "running",
            "started_at": time.time(),
            "url": url,
        }

        # Run spider.start() in thread pool (it uses anyio.run internally)
        def _run():
            try:
                result = spider.start()
                self._sessions[session_id]["result"] = result
                self._sessions[session_id]["status"] = "completed"
            except Exception as e:
                self._sessions[session_id]["status"] = f"error: {e}"

        asyncio.get_event_loop().create_task(asyncio.to_thread(_run))
        return session_id

    def get_status(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if not session:
            return {"error": "session not found"}
        result = session.get("result")
        return {
            "session_id": session_id,
            "status": session["status"],
            "url": session["url"],
            "elapsed_seconds": time.time() - session["started_at"] if session["status"] == "running" else None,
            "stats": result.stats.to_dict() if result else None,
            "pages_crawled": len(result.items) if result else 0,
        }

    def get_results(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        if not session or not session.get("result"):
            return []
        return list(session["result"].items)
```

**Verification:** `await manager.start_spider("https://example.com", max_pages=3)` returns session_id. `manager.get_status(id)` shows "running" then "completed". `manager.get_results(id)` returns list of page dicts.  
**Rollback:** Remove `SpiderSessionManager`.

### Atom 4.3: Spider backend adapter + registration

| Aspect | Detail |
|---|---|
| **File** | `backends/scrapling_spider.py` (EXTEND) + `engine.py` (MODIFY) |
| **Risk** | LOW — wraps Atom 4.2 |

**Implementation:**
```python
class ScraplingSpiderBackend:
    name = "scrapling-spider"
    weight = 1.0
    cache_ttl = 0  # per-session
    timeout_seconds = 120

    def __init__(self):
        self._manager = SpiderSessionManager()

    def is_available(self) -> bool:
        try:
            import scrapling
            return True
        except ImportError:
            return False

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        # query format: "crawl:url max_pages:50"
        if not query.startswith("crawl:"):
            return []
        parts = query[6:].split()
        url = parts[0]
        max_pages = 50
        for part in parts[1:]:
            if part.startswith("max_pages:"):
                max_pages = int(part.split(":")[1])

        session_id = await self._manager.start_spider(url, max_pages=max_pages)
        return [HarnessResult(
            source="scrapling-spider",
            content=f"Spider started: session {session_id} crawling {url} (max {max_pages} pages)",
            score=0.5,
            metadata={"session_id": session_id, "url": url, "max_pages": max_pages},
        )]

# Register
BackendRegistry.register(ScraplingSpiderBackend())
```

**Also expose MCP tools for status/results (new tools in server.py):**
```python
@server.tool()
def spider_status(session_id: str) -> str:
    """Check spider crawl status."""
    mgr = SpiderSessionManager()
    return json.dumps(mgr.get_status(session_id), indent=2)

@server.tool()
def spider_results(session_id: str) -> str:
    """Get spider crawl results."""
    mgr = SpiderSessionManager()
    results = mgr.get_results(session_id)
    return json.dumps(results, indent=2)
```

**Verification:** `context_harness("crawl:https://example.com max_pages:3")` starts spider and returns session_id. `spider_status(id)` shows progress. `spider_results(id)` returns crawled pages.  
**Rollback:** Comment out registration + MCP tools.

---

## PHASE 5 — RRF Fusion Integration (0.5 days)

**Goal:** Wire all 7 backends into the fusion pipeline with proper dedup, cache, and timeout handling.

### Atom 5.1: Finalize `fuse_registered()` with all 7 backends

| Aspect | Detail |
|---|---|
| **File** | `fusion.py` (MODIFY) |
| **Risk** | MEDIUM — must handle 7 backends with different response shapes and latency profiles |
| **Verified context** | Backend weights (from this plan): NEXUS_DB=1.0, agent_reach=1.2, memory_graph=1.0, scrapling_fetch=1.2, scrapling_spider=1.0, scrapecraft_pattern=1.5, sherlock_probe=0.8. RRF_K=60.0. Timeout per backend: fetch=10s, spider=120s, pattern=30s, probe=60s, nexus=5s, internet=5s, memory=5s. |

**Changes to `fuse_registered()`:**
1. Execute all available backends in parallel via `asyncio.gather(*tasks, return_exceptions=True)`.
2. Per-backend timeout: `asyncio.wait_for(backend.search(query, max_results), timeout=backend.timeout_seconds)`.
3. Error isolation: exceptions become empty result lists (logged but not fatal).
4. Dedup: `hashlib.sha256(content[:200].encode()).hexdigest()` — content-based, not source-based.
5. Weight application: `final_score = raw_score * backend.weight * (1.0 / (RRF_K + rank_index))`.
6. Sort descending, truncate to `max_results`.

**Verification:** `fuse_registered("test", all_7_backends, 10)` returns results from 3+ backends. One backend failing → others still return. No duplicates.  
**Rollback:** Revert to 3-backend `fuse()`.

### Atom 5.2: Cache layer for backends

| Aspect | Detail |
|---|---|
| **File** | `/home/roshan/nexus/server/context_harness/backend_cache.py` (NEW) |
| **Risk** | LOW |
| **Verified context** | Existing `nexus_bridge.py` uses `~/nexus/db/nexus.db` (SQLite). `cache.py` (1285 lines) has `TTLLRUCache` but it's not used by the MCP path. |

**Implementation:**
```python
import sqlite3
import hashlib
import json
import time
from pathlib import Path

CACHE_DB = Path.home() / "nexus" / "db" / "backend_cache.db"

def _get_conn():
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backend_cache (
            cache_key TEXT PRIMARY KEY,
            backend_name TEXT,
            query TEXT,
            results_json TEXT,
            created_at REAL,
            ttl INTEGER
        )
    """)
    return conn

def get_cached(backend_name: str, query: str, ttl: int) -> list | None:
    if ttl <= 0:
        return None
    key = f"{backend_name}:{hashlib.sha256(query.encode()).hexdigest()}"
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT results_json, created_at FROM backend_cache WHERE cache_key=?",
            (key,)
        ).fetchone()
        if row and (time.time() - row[1]) < ttl:
            return json.loads(row[0])
        return None
    finally:
        conn.close()

def set_cached(backend_name: str, query: str, results: list, ttl: int) -> None:
    if ttl <= 0:
        return
    key = f"{backend_name}:{hashlib.sha256(query.encode()).hexdigest()}"
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backend_cache (cache_key, backend_name, query, results_json, created_at, ttl) VALUES (?, ?, ?, ?, ?, ?)",
            (key, backend_name, query, json.dumps(results, default=str), time.time(), ttl)
        )
        conn.commit()
    finally:
        conn.close()
```

**Integrate into `fuse_registered()`:** Before calling `backend.search()`, check cache. After successful search, store in cache.

**Verification:** Same query twice → second call returns from cache (faster). Cache with TTL=0 → no caching.  
**Rollback:** `rm backend_cache.py`, remove cache calls from `fuse_registered()`.

### Atom 5.3: Wire all backends into `engine.py`

| Aspect | Detail |
|---|---|
| **File** | `engine.py` (MODIFY) |
| **Risk** | LOW — adding imports |

**Changes:**
```python
# At top of engine.py, after existing imports:
import server.context_harness.backends.nexus_backend      # noqa: F401
import server.context_harness.backends.internet_backend    # noqa: F401
import server.context_harness.backends.memory_backend      # noqa: F401
import server.context_harness.backends.scrapling_fetch     # noqa: F401
import server.context_harness.backends.sherlock_probe      # noqa: F401
import server.context_harness.backends.scrapecraft_pattern # noqa: F401
import server.context_harness.backends.scrapling_spider    # noqa: F401
```

**In `harness()` method:** Replace 3-branch logic with:
```python
if "all" in sources:
    backends = BackendRegistry.available()
else:
    backends = [BackendRegistry.get(s.strip()) for s in sources if BackendRegistry.get(s.strip())]
fused = await fuse_registered_async(q, backends, max_results)
```

**Note:** `harness()` must become `async def` or use `asyncio.run()` to call `fuse_registered_async()`. Since FastMCP supports async tools, change `context_harness` tool to `async def`.

**Verification:** `context_harness("test query")` returns results from all available backends. `context_harness("https://example.com")` includes scrapling-fetch results. `context_harness("probe:blue")` includes sherlock-probe results.  
**Rollback:** Revert to old `harness()` logic.

---

## PHASE 6 — Testing (1.5 days)

### Atom 6.1: Unit tests for fusion + registry

| File | `tests/test_fusion.py` |
|---|---|
| **Tests** | (1) 3 mock backends → RRF fuses correctly. (2) 7 backends → no duplicates. (3) Backend timeout → returns empty, others succeed. (4) Cache hit → returns cached, skips search. (5) Cache miss → calls search, stores result. (6) Dedup with same content different source → merges. (7) Weight application → higher weight ranks higher. |

### Atom 6.2: Unit tests for scrapling fetch

| File | `tests/test_scrapling_fetch.py` |
|---|---|
| **Tests** | (1) Static fetch of `https://example.com` → status 200, content non-empty. (2) URL query detection → non-URL returns empty list. (3) Auto-fallback on 403 → tries dynamic/stealth (mock). (4) Batch fetch 3 URLs → returns 3 results. (5) Cache hit → second call faster. (6) Timeout → returns error result. |

### Atom 6.3: Unit tests for sherlock probe

| File | `tests/test_sherlock_probe.py` |
|---|---|
| **Tests** | (1) `Collector` subclass collects results. (2) `_parse_query("probe:john sites:GitHub,Reddit")` → `("john", ["GitHub", "Reddit"])`. (3) `_parse_query("not a probe")` → `(None, None)`. (4) Site manifest loads 459+ sites (NSFW removed). (5) Rate limiter blocks within 5s. (6) Full probe of "blue" → returns CLAIMED results (integration test, requires network). |

### Atom 6.4: Unit tests for scrapecraft pattern

| File | `tests/test_scrapecraft_pattern.py` |
|---|---|
| **Tests** | (1) `PatternLearner(redis_url=None)` initializes without Redis. (2) `get_extraction_tips("example.com")` returns list. (3) `_extract_domain("https://news.example.com/article")` → `"news.example.com"`. (4) `_classify_data_type({"price": "", "name": ""})` → `"ecommerce"`. (5) Semantic extractor without API key → returns error dict. (6) `search("tips:github.com")` → returns tips result. |

### Atom 6.5: Unit tests for spider

| File | `tests/test_spider.py` |
|---|---|
| **Tests** | (1) `HarnessSpider` subclass instantiates. (2) `_is_allowed("https://other.com")` → False (different domain). (3) `start_spider("https://example.com", max_pages=2)` → returns session_id. (4) `get_status(id)` → shows "running" or "completed". (5) `get_results(id)` → returns list of page dicts. (6) Checkpoint created in cache dir. |

### Atom 6.6: Integration test for full RRF pipeline

| File | `tests/test_integration_rrf.py` |
|---|---|
| **Tests** | (1) `context_harness("test")` → returns results from nexus + memory at minimum. (2) `context_harness("https://example.com")` → includes scrapling-fetch. (3) `context_harness("probe:blue")` → includes sherlock-probe (network test). (4) `context_harness("tips:github.com")` → includes scrapecraft-pattern. (5) `context_harness("crawl:https://example.com max_pages:2")` → includes scrapling-spider. (6) One backend down → others still return. |

---

## PHASE 7 — Verification (0.5 days)

### Atom 7.1: Phase-by-phase verification report

| Check | Criteria |
|---|---|
| P0 | `BackendRegistry.available()` returns 3+ backends. `fuse_registered()` works with mock backends. |
| P1 | `ScraplingFetchBackend.is_available()` returns True. Fetch of `https://example.com` returns status 200. Auto-fallback triggers on 403. |
| P2 | `SherlockProbeBackend.is_available()` returns True. Probe of "blue" returns CLAIMED results for 3+ sites. Rate limiter blocks within 5s. |
| P3 | `ScrapecraftPatternBackend.is_available()` returns True. `get_extraction_tips("github.com")` returns non-empty list. PatternLearner works without Redis. |
| P4 | `ScraplingSpiderBackend.is_available()` returns True. Spider on 5-page site returns 5 results. Checkpoint created. |
| P5 | `context_harness("test")` returns results from 3+ backends. Cache hit returns faster. One backend failure doesn't kill others. |
| P6 | All test files pass with ≥80% coverage. |

### Atom 7.2: Performance benchmark

| Metric | Target |
|---|---|
| NEXUS search latency | <50ms (SQLite LIKE, existing) |
| Scrapling static fetch | <3s (curl_cffi) |
| Scrapling stealth fetch | <15s (Playwright + patchright) |
| Sherlock probe (478 sites) | <60s (20 threads) |
| Scrapecraft tips (cached) | <10ms (in-memory) |
| Scrapecraft semantic extract | <10s (ScrapeGraph API) |
| Spider crawl (5 pages) | <30s |
| RRF fusion (3 backends) | <100ms (parallel) |
| RRF fusion (7 backends) | <500ms (parallel with timeouts) |
| Cache hit latency | <5ms (SQLite read) |

---

## PHASE 8 — Audit (0.5 days)

### Atom 8.1: Execution audit report

Document: plan vs actual comparison, deviations, token usage, time per phase, recommendations.

### Atom 8.2: Save learnings to NEXUS

```python
nexus_add_learning_note(
    note="Context Harness v2: 4 new backends added (scrapling-fetch, sherlock-probe, "
         "scrapecraft-pattern, scrapling-spider). Key learnings: (1) Scrapling already "
         "has MCP server — wrap, don't rebuild. (2) Sherlock is synchronous — use "
         "asyncio.to_thread. (3) PatternLearner doesn't learn selectors — scope claims "
         "to stats aggregation. (4) fusion.py was hardcoded to 3 backends — refactored "
         "to plugin/registry pattern first.",
    project="hermes",
    task_type="integration",
    tools=["context-harness", "scrapling", "sherlock", "scrapecraft"]
)
```

### Atom 8.3: Update context-harness documentation

Update `server.py` docstrings, `engine.py` class docstring, and `agent_reach.py` to reflect the new 7-backend architecture.

---

## DAG Dependencies

```
Phase 0 (Fusion Refactor) — BLOCKING for all others
  ├── Atom 0.1 (BackendProtocol + Registry)
  ├── Atom 0.2 (Refactor fusion.py)
  ├── Atom 0.3 (Refactor engine.py harness())
  └── Atom 0.4 (Migrate 3 existing backends)
       │
       ├── Phase 1 (Scrapling Fetch) ──┐
       ├── Phase 2 (Sherlock Probe) ───┤
       ├── Phase 3 (Scrapecraft) ──────┤── PARALLEL
       └── Phase 4 (Scrapling Spider) ─┘
            │
       Phase 5 (RRF Integration)
            │
       Phase 6 (Testing)
            │
       Phase 7 (Verification)
            │
       Phase 8 (Audit)
```

**Phases 1-4 are fully parallelizable** — each wraps a different project with no cross-dependencies. Phase 5 requires all 4 to be complete. Phases 6-8 are sequential.

---

## Risk Register (Updated with Verified Findings)

| Risk | Probability | Impact | Mitigation | Verified Source |
|---|---|---|---|---|
| Scrapling `anyio` conflicts with asyncio event loop | MEDIUM | HIGH | Use `asyncio.to_thread()` for sync calls. `AsyncFetcher` is already async-safe. | `scrapling/fetchers/requests.py` — `AsyncFetcher.get()` is async |
| Sherlock blocks event loop | HIGH | HIGH | `asyncio.to_thread(sherlock, ...)` — verified sync function | `sherlock_project/sherlock.py` lines 170-502 — uses `requests_futures.FuturesSession` (threaded, sync) |
| PatternLearner doesn't actually learn selectors | **CONFIRMED** | MEDIUM | Scope backend to "stats + tips + semantic extraction". Document clearly. | `pattern_learner.py` — no ML, no LLM, hardcoded seed patterns |
| ScrapeGraph API key unavailable | MEDIUM | LOW | Fallback to in-memory PatternLearner tips only. Document API key requirement. | `scrapegraph.py` — `AsyncClient(api_key=self.api_key)` |
| Spider `anyio.run()` conflicts with running event loop | HIGH | HIGH | `asyncio.to_thread(spider.start)` — `start()` uses `anyio.run()` which creates its own loop | `scrapling/spiders/spider.py` — `start()` calls `anyio.run(self._run_engine)` |
| `unified_agent.py` broken import (pipeline_repository) | CONFIRMED | NONE | We extract PatternLearner directly, not through unified_agent | `unified_agent.py` line 71 — imports non-existent module |
| RRF with 7 backends exceeds latency budget | LOW | MEDIUM | Parallel execution with per-backend timeout. Cache hot results. | Calculated: max(timeout) = 120s (spider), but parallel with timeout=10s for most |

---

## Rollback Plan

If any phase fails verification:
1. Revert the specific atom's files (`git checkout <file>` or `rm <file>`).
2. If backend registered, remove import from `engine.py`.
3. If MCP tools registered, comment out in `server.py`.
4. Restart context-harness MCP server.
5. Verify old 3-backend RRF still works.

**Full rollback:** `git revert` all changes in `server/context_harness/backends/` + `fusion.py` + `engine.py` + `server.py`. Restart MCP server.

---

## Summary of MCP Tools After Upgrade

### Existing (4 tools, unchanged):
1. `context_harness(query, sources, max_results)` — now routes through 7 backends
2. `context_search(query, source, max_results)` — search across backends
3. `context_store(key, value, source, tags)` — store context
4. `context_doctor()` — health check (updated to probe new backends)

### New (2 tools):
5. `spider_status(session_id)` — check spider crawl status
6. `spider_results(session_id)` — get spider crawl results

### Backend query patterns:
| Pattern | Backend | Example |
|---|---|---|
| Any URL | scrapling-fetch | `context_harness("https://example.com")` |
| `probe:username` | sherlock-probe | `context_harness("probe:blue sites:GitHub,Reddit")` |
| `tips:domain` | scrapecraft-pattern | `context_harness("tips:github.com")` |
| `extract:url fields` | scrapecraft-pattern | `context_harness("extract:https://news.com title,body")` |
| `crawl:url` | scrapling-spider | `context_harness("crawl:https://example.com max_pages:50")` |
| Plain text | nexus + memory + internet | `context_harness("how to design REST APIs")` |
