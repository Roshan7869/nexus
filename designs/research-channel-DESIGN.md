# Research Channel — Implementation Design

## Overview

A dedicated backend channel for research papers and open-source journals, integrating into the context-harness MCP's RRF fusion pipeline as the 4th source category.

## Architecture

### Data Flow

```
User Query
    │
    ▼
Research Router (intent classifier)
    │
    ├── paper_search ──────► Tier 0 Sources ──► arXiv, Semantic Scholar, CrossRef, DBLP, DOAJ
    │                              │
    │                              ▼
    │                         Tier 1 Sources ──► CORE, Unpaywall, Google Scholar
    │
    ├── citation_lookup ───► Semantic Scholar Citations API
    │                              │
    │                              ▼
    │                         CrossRef References API
    │
    ├── full_text ──────────► arXiv PDF/HTML
    │                              │
    │                              ▼
    │                         PubMed Central full-text XML
    │                         Unpaywall OA PDF link
    │
    └── author_search ─────► Semantic Scholar Author API
                                   │
                                   ▼
                              DBLP Author Search
```

### Source Selection Strategy

1. **Primary (always queried):** arXiv + Semantic Scholar — broadest coverage, zero-config, complementary (arXiv = preprints, S2 = citations + impact)
2. **Secondary (queried on intent match):** CrossRef (DOI resolution), DBLP (CS-specific), PubMed Central (biomedical)
3. **Tertiary (fallback / enrichment):** CORE (full-text), Unpaywall (OA links), Google Scholar (last resort)

### RRF Integration Point

The Research channel registers itself with the RRF fuser as a 4th source category. Each source adapter returns a list of `ResearchResult` objects with a `confidence` score. The RRF fuser:

1. Collects results from all 4 backends (NEXUS, Internet, Memory, Research)
2. Normalizes to a common schema
3. Deduplicates by DOI → arXiv ID → normalized title
4. Applies RRF with k=60
5. Weights: Research=1.5x, NEXUS=1.0x, Memory=1.2x, Internet=0.8x
6. Returns top-N fused results

## Implementation

### Core Module: `research/__init__.py`

```python
"""Research channel registration for context-harness MCP."""

from .router import ResearchRouter
from .schema import ResearchResult, normalize_result
from .cache import ResearchCache

__all__ = ["ResearchRouter", "ResearchResult", "normalize_result", "ResearchCache"]

# Channel registration hook
def register_channel(rrf_fuser):
    """Register the research channel with the RRF fuser."""
    router = ResearchRouter()
    rrf_fuser.register_source("research", router.query, weight=1.5)
    return router
```

### Schema: `research/schema.py`

```python
"""Unified result schema for all research sources."""

from dataclasses import dataclass, field, asdict
from typing import Optional
import re


@dataclass
class ResearchResult:
    """Normalized research paper result from any source."""
    title: str
    authors: list[str]
    abstract: str
    venue: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    pmid: Optional[str] = None
    pdf_url: Optional[str] = None
    html_url: Optional[str] = None
    citation_count: Optional[int] = None
    reference_count: Optional[int] = None
    source: str = ""
    source_url: str = ""
    keywords: list[str] = field(default_factory=list)
    fields_of_study: list[str] = field(default_factory=list)
    is_open_access: Optional[bool] = None
    confidence: float = 1.0

    def dedup_key(self) -> str:
        """Return the best deduplication key for this result."""
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id.split('v')[0]}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        # Fallback: normalized title
        title_norm = re.sub(r'[^a-z0-9]', '', self.title.lower().strip())
        return f"title:{title_norm[:80]}"

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_title(title: str) -> str:
    """Normalize a paper title for comparison/dedup."""
    return re.sub(r'\s+', ' ', title.strip().lower())


def extract_arxiv_id(text: str) -> Optional[str]:
    """Extract arXiv ID from URL or raw ID string."""
    patterns = [
        r'arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)',
        r'arxiv\.org/(?:abs|pdf|html)/([a-z\-]+\/\d{7})',
        r'^(\d{4}\.\d{4,5}(?:v\d+)?)$',
        r'^([a-z\-]+\/\d{7})$',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None
```

### Cache: `research/cache.py`

```python
"""SQLite-backed cache for research API results."""

import json
import sqlite3
import time
import hashlib
from pathlib import Path
from typing import Optional


class ResearchCache:
    """Local cache for research API responses to reduce redundant calls."""

    def __init__(self, db_path: str = "~/.context-harness/research_cache.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_cache (
                    cache_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    query_type TEXT NOT NULL,
                    query_params TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    ttl_hours INTEGER DEFAULT 24,
                    hit_count INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_source
                ON research_cache(source)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_fetched
                ON research_cache(fetched_at)
            """)
            conn.commit()

    def _make_key(self, source: str, query_type: str, params: dict) -> str:
        raw = f"{source}:{query_type}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, source: str, query_type: str, params: dict) -> Optional[list]:
        key = self._make_key(source, query_type, params)
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT result_json, fetched_at, ttl_hours FROM research_cache WHERE cache_key = ?",
                (key,)
            ).fetchone()
        if not row:
            return None
        result_json, fetched_at, ttl_hours = row
        elapsed = (time.time() - fetched_at) / 3600
        if elapsed > ttl_hours:
            return None  # Expired
        # Update hit count
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE research_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (key,)
            )
            conn.commit()
        return json.loads(result_json)

    def set(self, source: str, query_type: str, params: dict, results: list, ttl_hours: int = 24):
        key = self._make_key(source, query_type, params)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_cache
                (cache_key, source, query_type, query_params, result_json, fetched_at, ttl_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                key, source, query_type, json.dumps(params),
                json.dumps([r.to_dict() if hasattr(r, 'to_dict') else r for r in results]),
                int(time.time()), ttl_hours
            ))
            conn.commit()

    def stats(self) -> dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM research_cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM research_cache WHERE (strftime('%s','now') - fetched_at) > ttl_hours * 3600"
            ).fetchone()[0]
            top_sources = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM research_cache GROUP BY source ORDER BY cnt DESC"
            ).fetchall()
        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "top_sources": dict(top_sources),
        }

    def prune_expired(self) -> int:
        with sqlite3.connect(str(self.db_path)) as conn:
            pruned = conn.execute(
                "DELETE FROM research_cache WHERE (strftime('%s','now') - fetched_at) > ttl_hours * 3600"
            ).rowcount
            conn.commit()
        return pruned
```

### Router: `research/router.py`

```python
"""Intent classifier and source router for research queries."""

import re
from typing import Callable
from .schema import ResearchResult


INTENT_PATTERNS = {
    "paper_search": [
        r"(paper|article|publication|research).*(about|on|for|regarding)",
        r"find.*(paper|article|publication|research)",
        r"search.*(arxiv|scholar|academic|papers)",
        r"what.*(known|published|written).*(about|on)",
        r"recent.*(work|research|papers|advances)",
    ],
    "citation_lookup": [
        r"(cite|citation|references|bibliography|refs)",
        r"who.*cited",
        r"how.*many.*citations",
        r"most.*cited",
        r"influential.*(paper|work)",
    ],
    "full_text": [
        r"full.*text|pdf|download|read.*(paper|article)",
        r"get.*(content|text|body|full)",
        r"open.*access|oa",
    ],
    "author_search": [
        r"author|researcher|scientist|professor|scholar",
        r"papers by|work by|publications by|written by",
        r"h-index|h index",
    ],
    "journal_search": [
        r"journal|conference|proceedings|venue|transaction",
        r"open.*access.*journal",
        r"where.*(published|appear)",
    ],
}


class ResearchRouter:
    """Routes research queries to appropriate sources based on intent."""

    def __init__(self):
        self.sources = {}  # name -> adapter function
        self._compiled = {
            intent: [re.compile(p, re.IGNORECASE) for p in patterns]
            for intent, patterns in INTENT_PATTERNS.items()
        }

    def register_source(self, name: str, adapter: Callable, intents: list[str] = None):
        """Register a source adapter for specific intents."""
        self.sources[name] = {
            "adapter": adapter,
            "intents": intents or ["paper_search"],
        }

    def classify_intent(self, query: str) -> list[str]:
        """Classify query into one or more intents."""
        scores = {}
        for intent, patterns in self._compiled.items():
            score = sum(1 for p in patterns if p.search(query))
            if score > 0:
                scores[intent] = score
        if not scores:
            return ["paper_search"]  # default
        # Return all matched intents, sorted by match count
        return sorted(scores, key=scores.get, reverse=True)

    def query(self, query: str, limit: int = 5, sources: list[str] = None) -> list[ResearchResult]:
        """Route query to appropriate sources and collect results."""
        intents = self.classify_intent(query)
        results = []

        # Determine which sources to query
        active_sources = sources or list(self.sources.keys())

        for source_name in active_sources:
            source = self.sources.get(source_name)
            if not source:
                continue
            # Check if this source handles any of the matched intents
            if not any(i in source["intents"] for i in intents):
                continue
            try:
                source_results = source["adapter"](query, limit=limit, intents=intents)
                results.extend(source_results)
            except Exception as e:
                # Log error but don't fail the whole query
                import logging
                logging.warning(f"Research source '{source_name}' failed: {e}")

        # Sort by confidence descending
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results[:limit]
```

### arXiv Adapter: `research/sources/arxiv.py`

```python
"""arXiv API adapter — Tier 0, zero-config."""

import xml.etree.ElementTree as ET
import time
import urllib.parse
import urllib.request
from typing import Optional
from ..schema import ResearchResult, extract_arxiv_id

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}
MIN_INTERVAL = 3.0  # seconds between requests
_last_request = 0.0


def _rate_limit():
    global _last_request
    now = time.time()
    elapsed = now - _last_request
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request = time.time()


def _parse_entry(entry) -> ResearchResult:
    """Parse an Atom entry into a ResearchResult."""
    title = entry.find("a:title", ARXIV_NS).text.strip().replace("\n", " ")
    arxiv_id = entry.find("a:id", ARXIV_NS).text.strip().split("/abs/")[-1]
    published = entry.find("a:published", ARXIV_NS).text[:10]
    summary = entry.find("a:summary", ARXIV_NS).text.strip()
    authors = [
        a.find("a:name", ARXIV_NS).text
        for a in entry.findall("a:author", ARXIV_NS)
    ]
    categories = [
        c.get("term") for c in entry.findall("a:category", ARXIV_NS)
    ]
    doi_tag = entry.find("a:link", {"title": "doi"})
    doi = doi_tag.get("href", "").replace("https://doi.org/", "") if doi_tag is not None else None

    return ResearchResult(
        title=title,
        authors=authors,
        abstract=summary[:2000],
        venue=f"arXiv ({', '.join(categories)})",
        year=int(published[:4]) if published else None,
        doi=doi,
        arxiv_id=arxiv_id,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        html_url=f"https://arxiv.org/abs/{arxiv_id}",
        source="arxiv",
        source_url=f"https://arxiv.org/abs/{arxiv_id}",
        keywords=categories,
        fields_of_study=categories,
        is_open_access=True,
        confidence=0.95,
    )


def search(query: str, limit: int = 5, intents: list[str] = None) -> list[ResearchResult]:
    """Search arXiv papers."""
    _rate_limit()
    params = {
        "search_query": f"all:{urllib.parse.quote(query)}",
        "max_results": min(limit, 50),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "context-harness/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_data = resp.read().decode("utf-8")

    root = ET.fromstring(xml_data)
    results = []
    for entry in root.findall("a:entry", ARXIV_NS):
        result = _parse_entry(entry)
        results.append(result)
    return results


def fetch_by_id(arxiv_id: str) -> Optional[ResearchResult]:
    """Fetch a specific paper by arXiv ID."""
    _rate_limit()
    url = f"{ARXIV_API}?id_list={arxiv_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "context-harness/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_data = resp.read().decode("utf-8")
    root = ET.fromstring(xml_data)
    entry = root.find("a:entry", ARXIV_NS)
    if entry is None:
        return None
    return _parse_entry(entry)
```

### Semantic Scholar Adapter: `research/sources/semantic_scholar.py`

```python
"""Semantic Scholar API adapter — Tier 0, zero-config."""

import json
import time
import urllib.parse
import urllib.request
from typing import Optional
from ..schema import ResearchResult, extract_arxiv_id

S2_API = "https://api.semanticscholar.org/graph/v1"
MIN_INTERVAL = 1.0
_last_request = 0.0

FIELDS = "title,authors,year,abstract,citationCount,referenceCount,influentialCitationCount,isOpenAccess,openAccessPdf,fieldsOfStudy,externalIds,publicationVenue"


def _rate_limit():
    global _last_request
    now = time.time()
    elapsed = now - _last_request
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request = time.time()


def _parse_paper(data: dict) -> ResearchResult:
    """Parse Semantic Scholar paper JSON into ResearchResult."""
    ext_ids = data.get("externalIds") or {}
    arxiv_id = ext_ids.get("ArXiv")
    doi = ext_ids.get("DOI")
    venue_data = data.get("publicationVenue") or {}
    venue = venue_data.get("name") if venue_data else None
    oa_info = data.get("openAccessPdf") or {}
    pdf_url = oa_info.get("url") if oa_info else None
    authors = [a.get("name", "") for a in (data.get("authors") or [])]

    return ResearchResult(
        title=data.get("title", ""),
        authors=authors,
        abstract=(data.get("abstract") or "")[:2000],
        venue=venue,
        year=data.get("year"),
        doi=doi,
        arxiv_id=arxiv_id,
        pdf_url=pdf_url,
        html_url=f"https://www.semanticscholar.org/paper/{data.get('paperId', '')}",
        citation_count=data.get("citationCount"),
        reference_count=data.get("referenceCount"),
        source="semantic_scholar",
        source_url=f"https://api.semanticscholar.org/graph/v1/paper/{data.get('paperId', '')}",
        fields_of_study=data.get("fieldsOfStudy") or [],
        is_open_access=data.get("isOpenAccess"),
        confidence=0.9,
    )


def search(query: str, limit: int = 5, intents: list[str] = None) -> list[ResearchResult]:
    """Search Semantic Scholar papers."""
    _rate_limit()
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": FIELDS,
    }
    url = f"{S2_API}/paper/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "context-harness/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = []
    for paper in data.get("data", []):
        results.append(_parse_paper(paper))
    return results


def fetch_by_id(paper_id: str) -> Optional[ResearchResult]:
    """Fetch a specific paper by S2 ID, arXiv ID, or DOI."""
    _rate_limit()
    url = f"{S2_API}/paper/{urllib.parse.quote(paper_id, safe='')}?fields={FIELDS}"
    req = urllib.request.Request(url, headers={"User-Agent": "context-harness/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _parse_paper(data)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def get_citations(paper_id: str, limit: int = 10) -> list[ResearchResult]:
    """Get papers citing a given paper."""
    _rate_limit()
    url = f"{S2_API}/paper/{urllib.parse.quote(paper_id, safe='')}/citations?fields={FIELDS}&limit={min(limit, 100)}"
    req = urllib.request.Request(url, headers={"User-Agent": "context-harness/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = []
    for entry in data.get("data", []):
        citing = entry.get("citingPaper")
        if citing:
            results.append(_parse_paper(citing))
    return results


def get_references(paper_id: str, limit: int = 10) -> list[ResearchResult]:
    """Get references of a given paper."""
    _rate_limit()
    url = f"{S2_API}/paper/{urllib.parse.quote(paper_id, safe='')}/references?fields={FIELDS}&limit={min(limit, 100)}"
    req = urllib.request.Request(url, headers={"User-Agent": "context-harness/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = []
    for entry in data.get("data", []):
        ref = entry.get("citedPaper")
        if ref:
            results.append(_parse_paper(ref))
    return results


def get_recommendations(positive_ids: list[str], limit: int = 5) -> list[ResearchResult]:
    """Get paper recommendations based on positive examples."""
    _rate_limit()
    url = f"{S2_API}/recommendations/v1/papers/"
    body = json.dumps({
        "positivePaperIds": positive_ids,
        "negativePaperIds": [],
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": "context-harness/1.0", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = []
    for paper in data.get("recommendedPapers", []):
        results.append(_parse_paper(paper))
    return results
```

### NEXUS Indexer: `research/nexus_indexer.py`

```python
"""Index research papers into NEXUS DB for offline retrieval."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional
from .schema import ResearchResult


class NexusIndexer:
    """Indexes fetched papers into NEXUS DB for offline search."""

    def __init__(self, nexus_db_path: str = "~/.hermes/nexus.db"):
        self.db_path = Path(nexus_db_path).expanduser()

    def index_paper(self, paper: ResearchResult) -> bool:
        """Index a single paper into NEXUS resources table."""
        if not self.db_path.exists():
            return False

        resource_name = f"paper:{paper.doi or paper.arxiv_id or paper.title[:50]}"
        body = f"# {paper.title}\n\n**Authors:** {', '.join(paper.authors)}\n"
        if paper.venue:
            body += f"**Venue:** {paper.venue}\n"
        if paper.year:
            body += f"**Year:** {paper.year}\n"
        if paper.doi:
            body += f"**DOI:** {paper.doi}\n"
        if paper.arxiv_id:
            body += f"**arXiv:** {paper.arxiv_id}\n"
        if paper.citation_count is not None:
            body += f"**Citations:** {paper.citation_count}\n"
        body += f"\n{paper.abstract}"

        metadata = {
            "source": paper.source,
            "venue": paper.venue,
            "year": paper.year,
            "authors": paper.authors,
            "doi": paper.doi,
            "arxiv_id": paper.arxiv_id,
            "citation_count": paper.citation_count,
            "is_open_access": paper.is_open_access,
            "pdf_url": paper.pdf_url,
            "indexed_at": int(time.time()),
        }

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO resources
                (name, type, body, metadata, category, project, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                resource_name,
                "paper",
                body,
                json.dumps(metadata),
                "research_paper",
                "default",
                int(time.time()),
                int(time.time()),
            ))
            conn.commit()
        return True

    def search_indexed(self, query: str, limit: int = 5) -> list[dict]:
        """Search indexed papers in NEXUS DB."""
        if not self.db_path.exists():
            return []

        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute("""
                SELECT name, body, metadata, created_at
                FROM resources
                WHERE category = 'research_paper'
                AND (body LIKE ? OR name LIKE ?)
                ORDER BY created_at DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", limit)).fetchall()

        results = []
        for name, body, metadata_json, created_at in rows:
            results.append({
                "name": name,
                "body": body,
                "metadata": json.loads(metadata_json) if metadata_json else {},
                "indexed_at": created_at,
            })
        return results
```

## Testing Strategy

### Unit Tests
- Each source adapter: mock HTTP responses, verify parsing
- Router: test intent classification with various query patterns
- Cache: test get/set/expiry/prune
- Schema: test dedup_key generation, arXiv ID extraction

### Integration Tests
- Live API calls to arXiv (1 req) and Semantic Scholar (1 req) to verify connectivity
- RRF integration: mock all 4 backends, verify fusion output

### Test Fixtures
- arXiv Atom XML sample responses
- Semantic Scholar JSON sample responses
- Cache database with known entries

## Configuration

```yaml
# ~/.context-harness/config.yaml
research:
  enabled: true
  cache:
    path: ~/.context-harness/research_cache.db
    default_ttl_hours: 24
  sources:
    arxiv:
      enabled: true
      rate_limit: 3.0  # seconds between requests
    semantic_scholar:
      enabled: true
      api_key: ""  # Optional: 100 req/s instead of 1 req/s
    crossref:
      enabled: true
    pubmed:
      enabled: true
    dblp:
      enabled: true
    openreview:
      enabled: true
    doaj:
      enabled: true
    core:
      enabled: false
      api_key: ""  # Required for Tier 1
    unpaywall:
      enabled: false
      api_key: ""  # Required for Tier 1
    google_scholar:
      enabled: false
      cookie: ""  # Required for Tier 1
  nexus_indexing:
    enabled: false  # Opt-in
    db_path: ~/.hermes/nexus.db
  rrf:
    weight: 1.5
    k: 60
```

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Cold query (all Tier 0) | < 5s | 3 sources × 1 req/s rate limit |
| Warm query (cached) | < 50ms | SQLite lookup |
| Cache hit rate | > 60% | After 1 week of use |
| NEXUS index time | < 100ms/paper | SQLite INSERT |
| Memory per adapter | < 5MB | No heavy dependencies |
