# ADR-001: Dedicated Research Papers & Open-Source Journals Backend Channel

**Status:** Proposed  
**Date:** 2026-07-01  
**Author:** Hermes Agent (NEXUS Architecture)  
**Project:** context-harness MCP  

---

## Context

The context-harness MCP currently fuses three backend source categories via Reciprocal Rank Fusion (RRF):

1. **NEXUS** (local DB) — 2,463 indexed resources (skills, agents, commands, MCP tools, docs, design systems); SQLite-backed, offline.
2. **Internet** (agent-reach) — 13 platforms across tiers (Exa search, GitHub, YouTube, RSS, Twitter/X, Reddit, etc.)
3. **Memory** — gcode-harness memory graph (Unix socket) → falls back to NEXUS learning notes.

**Gap:** No dedicated channel for research papers, academic databases, or open-source journals. Current workaround routes through generic web (Jina reader) or Exa search — no structured metadata, no citation graph, no full-text PDF access, no venue filtering.

## Decision

We will add a **4th backend source category: "Research"** to the RRF fusion pipeline, with its own tiered source architecture mirroring the agent-reach pattern.

### Source Inventory

| Source | Tier | Auth Required | API Limit | Content Type |
|--------|------|-------------|-----------|-------------|
| **arXiv** | 0 (zero-config) | None | 1 req/3s | Metadata + PDF + HTML |
| **Semantic Scholar** | 0 (zero-config) | None (1 req/s) | 1 req/s (100/s with key) | Metadata + citations + recommendations |
| **PubMed Central** | 0 (zero-config) | None | 3 req/s | Metadata + full-text XML |
| **DBLP** | 0 (zero-config) | None | No rate limit | Metadata (CS publications) |
| **OpenReview** | 0 (zero-config) | None | Moderate | Metadata + PDF |
| **Google Scholar** | 1 (cookie/parser) | Cookie | Aggressive rate limit | Metadata + citations |
| **Open-access journals (DOAJ)** | 0 (zero-config) | None | 1 req/s | Metadata + full-text |
| **CORE** | 1 (free API key) | Free key | 1000 req/day | Full-text + metadata |
| **Unpaywall** | 1 (free API key) | Free key | 100k req/day | Open-access PDF links |
| **CrossRef** | 0 (zero-config) | None | 50 req/s | DOI metadata + references |

### Tier Classification

**Tier 0 (zero-config, no auth):** arXiv, Semantic Scholar, PubMed Central, DBLP, OpenReview, DOAJ, CrossRef  
**Tier 1 (free key or cookie):** Google Scholar (cookie), CORE (free key), Unpaywall (free key)  
**Tier 2 (complex/setup):** None initially — all academic sources are Tier 0 or Tier 1

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    context-harness MCP                       │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  NEXUS   │  │ Internet │  │  Memory  │  │ Research │   │
│  │  (local) │  │(agent-   │  │  (graph) │  │  (NEW!)  │   │
│  │          │  │  reach)  │  │          │  │          │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │        │
│       └──────────────┴──────────────┴──────────────┘        │
│                          │                                   │
│                   ┌──────┴──────┐                           │
│                   │  RRF Fuser  │                           │
│                   │ (k=60)      │                           │
│                   └──────┬──────┘                           │
│                          │                                   │
│                   ┌──────┴──────┐                           │
│                   │   Output    │                           │
│                   └─────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

### Research Channel Internal Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Research Channel                           │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Query Router (intent classifier)         │  │
│  │  paper search │ citation lookup │ full-text │ author  │  │
│  └──────────┬───────────────────────┬───────────────────┘  │
│             │                       │                       │
│  ┌──────────▼──────────┐  ┌─────────▼──────────┐          │
│  │  Tier 0 Sources     │  │  Tier 1 Sources     │          │
│  │  ┌──────┬──────┬──┐ │  │  ┌──────┬──────┐   │          │
│  │  │arXiv │S2    │PMC│ │  │  │CORE  │Unpay │   │          │
│  │  ├──────┼──────┼──┤ │  │  │      │wall  │   │          │
│  │  │DBLP  │OR    │DO│ │  │  └──────┴──────┘   │          │
│  │  │      │      │AJ│ │  │                     │          │
│  │  └──────┴──────┴──┘ │  │  Google Scholar     │          │
│  │  CrossRef            │  │  (cookie-based)     │          │
│  └──────────────────────┘  └─────────────────────┘          │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Result Normalizer                        │  │
│  │  (unified schema: title, authors, abstract,          │  │
│  │   venue, year, doi, pdf_url, citation_count,         │  │
│  │   references, source_confidence)                      │  │
│  └──────────────────────┬───────────────────────────────┘  │
│                         │                                   │
│  ┌──────────────────────▼───────────────────────────────┐  │
│  │              Local Cache (SQLite)                      │  │
│  │  papers_cache: (doi, source, query, fetched_at,      │  │
│  │   result_json, ttl_hours)                            │  │
│  └──────────────────────┬───────────────────────────────┘  │
│                         │                                   │
│  ┌──────────────────────▼───────────────────────────────┐  │
│  │              NEXUS Indexer (optional)                 │  │
│  │  Indexes fetched papers into NEXUS DB for offline     │  │
│  │  retrieval. Respects TTL and cache freshness.        │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Unified Result Schema

Every source normalizes to this schema before RRF fusion:

```python
@dataclass
class ResearchResult:
    title: str
    authors: list[str]
    abstract: str
    venue: str | None          # "NeurIPS 2024", "arXiv cs.LG", etc.
    year: int | None
    doi: str | None
    arxiv_id: str | None
    pdf_url: str | None
    html_url: str | None
    citation_count: int | None
    reference_count: int | None
    source: str                 # "arxiv", "semantic_scholar", "pubmed", etc.
    source_url: str             # Direct link to the source page
    keywords: list[str] | None
    fields_of_study: list[str] | None
    is_open_access: bool | None
    confidence: float           # 0.0-1.0 (source reliability weight for RRF)
```

### RRF Integration

The Research channel produces results with `source_type="research"` and a `confidence` score. The RRF fuser already handles multiple source types — the Research channel simply becomes a 4th input stream.

**RRF k parameter:** k=60 (matching existing config)  
**Research weight multiplier:** 1.5x (papers are higher-value context than generic web results)  
**Deduplication key:** `doi` (preferred) → `arxiv_id` → `title_normalized`

### Query Router Intent Classification

```python
INTENT_PATTERNS = {
    "paper_search": [
        r"(paper|article|publication|research).*(about|on|for)",
        r"find.*(paper|article|publication)",
        r"search.*(arxiv|scholar|academic)",
    ],
    "citation_lookup": [
        r"cite|citation|references|bibliography",
        r"who.*cited",
        r"how.*many.*citations",
    ],
    "full_text": [
        r"full.*text|pdf|download|read.*paper",
        r"get.*(content|text|body)",
    ],
    "author_search": [
        r"author|researcher|scientist|professor",
        r"papers by|work by|publications by",
    ],
    "journal_search": [
        r"journal|conference|proceedings|venue",
        r"open.*access|oa",
    ],
}
```

### Source-Specific Implementation Details

#### arXiv (Tier 0)
- **API:** `https://export.arxiv.org/api/query` (Atom XML)
- **Search fields:** `all:`, `ti:`, `au:`, `abs:`, `cat:`
- **Full text:** `https://arxiv.org/pdf/{id}` or `https://arxiv.org/html/{id}`
- **Rate limit:** ~1 req/3 seconds
- **Parser:** Python stdlib `xml.etree.ElementTree`
- **Already exists as skill:** `arxiv` skill with helper script

#### Semantic Scholar (Tier 0)
- **API:** `https://api.semanticscholar.org/graph/v1/`
- **Endpoints:** `/paper/search`, `/paper/{id}`, `/paper/{id}/citations`, `/paper/{id}/references`, `/recommendations/v1/papers/`
- **Fields:** `title,authors,year,abstract,citationCount,referenceCount,influentialCitationCount,isOpenAccess,openAccessPdf,fieldsOfStudy,externalIds`
- **Rate limit:** 1 req/s (100/s with free API key)
- **Format:** JSON

#### PubMed Central (Tier 0)
- **API:** E-utilities (`https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`)
- **Endpoints:** `esearch.fcgi` (search), `efetch.fcgi` (fetch), `esummary.fcgi` (summary)
- **Full text:** PMC Open Access subset via OAI-PMH
- **Rate limit:** 3 req/s (with API key: 10 req/s)
- **Format:** XML

#### DBLP (Tier 0)
- **API:** `https://dblp.org/search/publ/api`
- **Format:** XML/JSON
- **Rate limit:** None documented, be reasonable
- **Specialty:** Computer science publications, strong on conference proceedings

#### OpenReview (Tier 0)
- **API:** `https://api.openreview.net/`
- **Endpoints:** `/notes/search`, `/notes`, `/invitations`
- **Content:** Conference peer reviews, workshop papers
- **Format:** JSON

#### CrossRef (Tier 0)
- **API:** `https://api.crossref.org/works`
- **Search:** `https://api.crossref.org/works?query=...`
- **Rate limit:** 50 req/s
- **Format:** JSON
- **Value:** DOI resolution, reference lists, funding info

#### DOAJ (Directory of Open Access Journals) (Tier 0)
- **API:** `https://doaj.org/api/v2/`
- **Search:** `/search/articles/{query}`
- **Rate limit:** 1 req/s
- **Format:** JSON
- **Value:** Open-access journal articles, verified OA status

#### CORE (Tier 1)
- **API:** `https://api.core.ac.uk/v3/`
- **Auth:** Free API key (1000 req/day)
- **Endpoints:** `/search/works`, `/works/{id}`
- **Format:** JSON
- **Value:** Full-text access to millions of open-access papers

#### Unpaywall (Tier 1)
- **API:** `https://api.unpaywall.org/v2/`
- **Auth:** Free API key (100k req/day)
- **Endpoint:** `/{doi}?email=...`
- **Format:** JSON
- **Value:** Finds legal open-access PDFs for any DOI

#### Google Scholar (Tier 1 — Cookie)
- **Method:** HTML scraping with cookie auth
- **Challenge:** Aggressive rate limiting, CAPTCHA, IP bans
- **Approach:** Use as fallback only; prefer structured APIs first
- **Tool:** `serpapi` or custom parser with rotating user-agents

### Local Cache Schema

```sql
CREATE TABLE research_cache (
    cache_key TEXT PRIMARY KEY,       -- hash(source + query + params)
    source TEXT NOT NULL,             -- "arxiv", "semantic_scholar", etc.
    query_type TEXT NOT NULL,         -- "search", "paper", "citations", "author"
    query_params TEXT NOT NULL,       -- JSON of original query parameters
    result_json TEXT NOT NULL,        -- JSON array of normalized results
    fetched_at INTEGER NOT NULL,      -- Unix timestamp
    ttl_hours INTEGER DEFAULT 24,    -- Cache TTL
    hit_count INTEGER DEFAULT 1       -- Cache usage counter
);

CREATE INDEX idx_research_cache_source ON research_cache(source);
CREATE INDEX idx_research_cache_fetched ON research_cache(fetched_at);
```

### NEXUS Indexing Integration

When a paper is fetched and the user opts in, the Research channel can index papers into NEXUS:

```python
def index_paper_to_nexus(paper: ResearchResult):
    """Index a fetched paper into NEXUS DB for offline retrieval."""
    nexus_resource = {
        "name": f"paper:{paper.doi or paper.arxiv_id}",
        "type": "paper",
        "body": f"# {paper.title}\n\n{paper.abstract}",
        "metadata": {
            "source": paper.source,
            "venue": paper.venue,
            "year": paper.year,
            "authors": paper.authors,
            "doi": paper.doi,
            "arxiv_id": paper.arxiv_id,
            "citation_count": paper.citation_count,
            "is_open_access": paper.is_open_access,
            "pdf_url": paper.pdf_url,
        },
        "category": "research_paper",
        "project": "default",
    }
    # Insert into NEXUS resources table
    # This makes papers searchable offline via NEXUS queries
```

### Implementation Plan

#### Phase 1: Core Sources (Tier 0)
1. arXiv adapter (reuse existing `arxiv` skill code)
2. Semantic Scholar adapter (search + citations + recommendations)
3. CrossRef adapter (DOI resolution + reference lists)
4. Result normalizer + unified schema
5. Local SQLite cache
6. RRF integration point

#### Phase 2: Extended Sources (Tier 0)
7. PubMed Central adapter
8. DBLP adapter
9. OpenReview adapter
10. DOAJ adapter

#### Phase 3: Tier 1 Sources
11. CORE adapter (free API key)
12. Unpaywall adapter (free API key)
13. Google Scholar adapter (cookie-based, fallback only)

#### Phase 4: NEXUS Integration
14. Paper indexing into NEXUS DB
15. Offline paper search via NEXUS
16. Citation graph in NEXUS knowledge graph

### File Structure

```
context-harness/
├── research/
│   ├── __init__.py              # Channel registration + RRF integration
│   ├── router.py                 # Intent classifier + source router
│   ├── schema.py                 # ResearchResult dataclass + normalizer
│   ├── cache.py                  # SQLite cache layer
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── arxiv.py              # arXiv API adapter
│   │   ├── semantic_scholar.py   # Semantic Scholar API adapter
│   │   ├── crossref.py           # CrossRef API adapter
│   │   ├── pubmed.py             # PubMed Central adapter
│   │   ├── dblp.py               # DBLP adapter
│   │   ├── openreview.py         # OpenReview adapter
│   │   ├── doaj.py               # DOAJ adapter
│   │   ├── core.py               # CORE API adapter (Tier 1)
│   │   ├── unpaywall.py          # Unpaywall adapter (Tier 1)
│   │   └── google_scholar.py     # Google Scholar scraper (Tier 1)
│   ├── nexus_indexer.py          # NEXUS DB indexing integration
│   └── tests/
│       ├── test_arxiv.py
│       ├── test_semantic_scholar.py
│       ├── test_router.py
│       ├── test_cache.py
│       └── test_rrf_integration.py
```

### MCP Tool Interface

The Research channel exposes these MCP tools:

| Tool | Description | Parameters |
|------|-------------|------------|
| `research_search` | Search papers across all sources | `query`, `sources` (optional filter), `limit`, `tier` |
| `research_paper` | Get specific paper by ID | `doi` or `arxiv_id` or `pmid`, `include_references` |
| `research_citations` | Get papers citing a paper | `doi` or `arxiv_id`, `limit` |
| `research_references` | Get references of a paper | `doi` or `arxiv_id`, `limit` |
| `research_author` | Search by author | `name`, `limit` |
| `research_recommendations` | Get paper recommendations | `positive_paper_ids`, `negative_paper_ids` |
| `research_index` | Index a paper into NEXUS | `doi` or `arxiv_id` |
| `research_cache_status` | Show cache stats | None |

### RRF Fusion Weights

| Source Type | Base Weight | Notes |
|-------------|-------------|-------|
| NEXUS (local) | 1.0 | Ground truth for indexed resources |
| Research (papers) | 1.5 | Higher value per result |
| Internet (web) | 0.8 | Broader but noisier |
| Memory (graph) | 1.2 | Session-specific relevance |

### Security Considerations

- No API keys hardcoded — stored in `~/.context-harness/config.yaml` or env vars
- Google Scholar cookie stored in config file (same pattern as agent-reach's xueqiu_cookie)
- All API calls go through HTTPS
- Rate limiting enforced per source to avoid IP bans
- Cache layer prevents redundant API calls
- NEXUS indexing is opt-in (user must enable)

### Open Questions

1. Should we pre-fetch popular papers (e.g., top-100 cited papers per field) into NEXUS for instant offline access?
2. Should the cache TTL be shorter for citation counts (which change) vs abstracts (which don't)?
3. Should we add a "paper alert" cron job that monitors new papers matching saved queries?
4. How should we handle papers behind paywalls? (Unpaywall handles this for OA, but non-OA papers need different treatment)

---

## Consequences

**Positive:**
- Research papers become a first-class citizen in the context-harness MCP
- No more routing through generic web search for academic content
- Structured metadata (citations, venues, authors) enables richer context
- Offline capability via NEXUS indexing
- Citation graph enables "related work" discovery
- Tier 0 sources work immediately with zero config

**Negative:**
- 4th backend increases RRF fusion complexity
- Google Scholar is fragile (scraping-based, rate-limited)
- Some sources have overlapping coverage (arXiv ∩ Semantic Scholar)
- Cache invalidation for citation counts needs careful TTL management

**Neutral:**
- Requires new MCP tool definitions
- Adds ~10 new source adapter files
- NEXUS DB grows with indexed papers (mitigated by TTL-based pruning)
