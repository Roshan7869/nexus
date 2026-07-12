"""Context Harnessing Engine — FastMCP server.

Exposes:
  - context_harness(q)      → unified context retrieval (legacy)
  - context_search(q)        → search across backends
  - context_store(k, v)      → store context
  - context_doctor()         → platform health check
  - harness_fetch(url)       → Scrapling-powered page fetch
  - harness_fetch_batch(urls) → batch fetch
  - harness_sherlock(user)   → Sherlock username probe (400+ sites)
  - harness_spider_start(url)  → start background spider crawl
  - harness_spider_status(id)  → spider crawl status
  - harness_spider_results(id) → spider crawl results
  - harness_pattern_extract(url, fields) → pattern extraction
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastmcp import FastMCP
from server.context_harness.models import HarnessQuery
from server.context_harness.engine import ContextHarnessEngine
from server.context_harness.channels.scrapling_fetch import get_channel as get_fetch
from server.context_harness.channels.sherlock_probe import get_channel as get_sherlock
from server.context_harness.channels.scrapling_spider import get_channel as get_spider
from server.context_harness.channels.scrapecraft_pattern import get_channel as get_pattern
from server.context_harness.channels.arxiv_search import get_channel as get_arxiv
from server.context_harness.channels.semantic_scholar import get_channel as get_s2
from server.context_harness.channels.github_search import get_channel as get_github
from server.context_harness.channels.paperswithcode_search import get_channel as get_pwc
from server.context_harness.channels.google_scholar import get_channel as get_scholar
from server.context_harness.channels.openalex_search import get_channel as get_openalex
from server.context_harness.channels.crossref_search import get_channel as get_crossref
from server.context_harness.channels.pubmed_search import get_channel as get_pubmed
from server.context_harness.channels.target_site import get_channel as get_target
from server.context_harness.channels.dispatcher import get_dispatcher
from server.context_harness.fusion import get_fuser, BackendDef

server = FastMCP("Context Harness")
engine = ContextHarnessEngine()
fetch_channel = get_fetch()
sherlock_channel = get_sherlock()
spider_channel = get_spider()
pattern_channel = get_pattern()
arxiv_channel = get_arxiv()
s2_channel = get_s2()
github_channel = get_github()
pwc_channel = get_pwc()
scholar_channel = get_scholar()
openalex_channel = get_openalex()
crossref_channel = get_crossref()
pubmed_channel = get_pubmed()
target_channel = get_target()
dispatcher = get_dispatcher()


@server.tool()
def context_harness(
    query: str,
    sources: str = "all",
    max_results: int = 10,
) -> str:
    """Harness context from all available sources — NEXUS resources, internet platforms, and memory.

    Args:
        query: Natural language query describing what context you need
        sources: Backend sources to query: 'nexus', 'internet', 'memory', or 'all'
        max_results: Maximum results to return (1-50)
    """
    q = HarnessQuery(query=query, sources=sources, max_results=max_results)
    resp = engine.harness(q)
    return resp.model_dump_json(indent=2)


@server.tool()
def context_search(
    query: str,
    source: str = "all",
    max_results: int = 10,
) -> str:
    """Search across context sources for matching entries.

    Args:
        query: Natural language search query
        source: Source to search: 'nexus', 'memory', 'internet', or 'all'
        max_results: Maximum results to return (1-50)
    """
    results = engine.search(query, source, max_results)
    if not results:
        return '{"results": []}'
    return f'{{"results": [{", ".join(r.model_dump_json() for r in results)}]}}'


@server.tool()
def context_store(
    key: str,
    value: str,
    source: str = "memory",
    tags: str = "",
) -> str:
    """Store context for future retrieval.

    Args:
        key: Unique identifier for this context
        value: Context content to store
        source: Storage backend: 'memory' (gcode) or 'learning' (NEXUS)
        tags: Comma-separated tags for categorization
    """
    import json
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    result = engine.store(key, value, source, tag_list)
    return json.dumps(result, indent=2)


@server.tool()
def context_doctor() -> str:
    """Check availability of all context sources — NEXUS DB, internet platforms, memory backends."""
    from server.context_harness.agent_reach import doctor as reach_doctor

    nexus_db = Path.home() / "nexus/db/nexus.db"
    gcode_sock = Path.home() / ".gcode/runtime/gcode.sock"

    import json
    report = {
        "nexus_db": {"available": nexus_db.exists(), "path": str(nexus_db)},
        "gcode_socket": {"available": gcode_sock.exists(), "path": str(gcode_sock)},
        "internet_platforms": reach_doctor(),
        "backends": get_fuser().backend_names,
    }
    return json.dumps(report, indent=2)


# ── Phase 1: Scrapling Fetch Tools ────────────────────────────────────────


@server.tool()
def harness_fetch(url: str, mode: str = "auto") -> str:
    """Fetch a web page using Scrapling with auto-fallback.

    Args:
        url: Target URL to fetch
        mode: Fetch mode — 'auto' (static→dynamic→stealth), 'static', 'dynamic', 'stealth'

    Returns:
        JSON with url, title, text_content, links, meta, status, fetch_mode, latency_ms
    """
    import asyncio

    result = asyncio.run(fetch_channel.fetch(url, mode=mode))
    import json

    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_fetch_batch(urls: list[str], mode: str = "auto", max_concurrent: int = 5) -> str:
    """Fetch multiple URLs concurrently.

    Args:
        urls: List of URLs to fetch
        mode: Fetch mode per URL
        max_concurrent: Max concurrent fetches (default: 5)

    Returns:
        JSON list of per-URL results
    """
    import asyncio
    import json

    async def _batch() -> list[dict]:
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(u: str) -> dict:
            async with sem:
                return await fetch_channel.fetch(u, mode=mode)

        tasks = [_one(u) for u in urls]
        return await asyncio.gather(*tasks)

    results = asyncio.run(_batch())
    return json.dumps(results, indent=2, default=str)


# ── Phase 2-4: New Channel Tools ──────────────────────────────────────────


@server.tool()
def harness_sherlock(username: str, sites: str = "", timeout: float = 60.0) -> str:
    """Probe 400+ sites for a username (Sherlock engine).

    Args:
        username: username to search for
        sites: comma-separated site names to check (empty = all)
        timeout: total timeout in seconds

    Returns:
        JSON with sites_checked, sites_found, per-site results
    """
    import asyncio
    import json

    site_list = [s.strip() for s in sites.split(",") if s.strip()] or None

    async def _run():
        await sherlock_channel.initialize()
        return await sherlock_channel.probe_username(
            username, sites=site_list, timeout=timeout
        )

    result = _async_run(_run())
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_spider_start(
    url: str,
    max_pages: int = 50,
    same_domain: bool = True,
    download_delay: float = 1.0,
) -> str:
    """Start a background spider crawl.

    Args:
        url: seed URL to start crawling from
        max_pages: max pages to crawl
        same_domain: stay on same domain
        download_delay: seconds between requests

    Returns:
        spider session info with spider_id for polling
    """
    import asyncio
    import json

    result = _async_run(
        spider_channel.start_spider(
            url, max_pages=max_pages, same_domain=same_domain, download_delay=download_delay
        )
    )
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_spider_status(spider_id: str) -> str:
    """Get spider crawl status.

    Args:
        spider_id: ID from harness_spider_start

    Returns:
        current spider session status
    """
    import asyncio
    import json

    result = _async_run(spider_channel.spider_status(spider_id))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_spider_results(spider_id: str) -> str:
    """Get spider crawl results (including pages crawled).

    Args:
        spider_id: ID from harness_spider_start

    Returns:
        spider session with full page data
    """
    import asyncio
    import json

    result = _async_run(spider_channel.spider_results(spider_id))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_pattern_extract(url: str, fields: str = "") -> str:
    """Extract structured data from a URL using learned patterns + semantic fallback.

    Learns CSS selectors from (field, value) examples, then applies them.
    Falls back to LLM-based semantic extraction when patterns don't match.

    Args:
        url: target URL to extract from
        fields: JSON object mapping field names to example values, or empty to use known patterns

    Returns:
        extraction result with extracted data and method used
    """
    import asyncio
    import json

    parsed_fields: dict[str, str] | None = None
    if fields:
        try:
            parsed_fields = json.loads(fields)
        except json.JSONDecodeError:
            pass

    result = _async_run(pattern_channel.extract(url, fields=parsed_fields))
    return json.dumps(result, indent=2, default=str)


# ── Academic Research Tools ──────────────────────────────────────────────


@server.tool()
def harness_research(
    query: str,
    channels: str = "arxiv,s2,github,pwc,scholar",
    max_results: int = 5,
) -> str:
    """Unified academic research search across arXiv, Semantic Scholar,
    GitHub, Papers with Code, and Google Scholar.

    Receives a research query from NEXUS analysis and fetches context
    from academic channels — papers, journals, open-source projects.

    Args:
        query: research query (e.g. "Thompson Sampling regret bounds")
        channels: comma-separated list: arxiv, s2, github, pwc, scholar
        max_results: max results per channel (default 5)

    Returns:
        JSON with per-channel results fused by RRF.
    """
    import asyncio
    import json

    selected = [c.strip() for c in channels.split(",") if c.strip()]
    all_results: list[dict] = []

    async def _search_all():
        tasks = []
        if "arxiv" in selected:
            tasks.append(("arxiv", arxiv_channel.search(query, max_results=max_results)))
        if "s2" in selected:
            tasks.append(("semantic_scholar", s2_channel.search(query, max_results=max_results)))
        if "github" in selected:
            tasks.append(("github", github_channel.search_repos(query, max_results=max_results)))
        if "pwc" in selected:
            tasks.append(("paperswithcode", pwc_channel.search(query, max_results=max_results)))
        if "scholar" in selected:
            tasks.append(("google_scholar", scholar_channel.search(query, max_results=max_results)))

        results = await asyncio.gather(
            *[t[1] for t in tasks], return_exceptions=True
        )
        for (name, _), res in zip(tasks, results):
            if isinstance(res, Exception):
                all_results.append({"channel": name, "error": str(res), "results": []})
            else:
                all_results.append({"channel": name, "results": res})

    _async_run(_search_all())

    fused = _fuse_research_results(all_results, max_results=max_results * len(selected))
    return json.dumps(
        {"query": query, "channels_searched": selected, "per_channel": all_results, "fused": fused},
        indent=2,
        default=str,
    )


def _fuse_research_results(per_channel: list[dict], max_results: int = 25) -> list[dict]:
    """RRF-fuse results across academic channels."""
    from server.context_harness.fusion import RRF_K

    rrf_scores: dict[str, float] = {}
    rrf_content: dict[str, dict] = {}
    weights = {
        "arxiv": 1.5,
        "semantic_scholar": 1.3,
        "github": 1.0,
        "paperswithcode": 1.4,
        "google_scholar": 0.9,
    }

    for channel_data in per_channel:
        name = channel_data.get("channel", "")
        results = channel_data.get("results", [])
        w = weights.get(name, 1.0)
        for rank, r in enumerate(results):
            title = r.get("title", r.get("name", r.get("full_name", "")))
            if not title:
                continue
            key = title.lower().strip()[:200]
            score = w * (1.0 / (RRF_K + rank))
            if key in rrf_scores:
                rrf_scores[key] += score
            else:
                rrf_scores[key] = score
                rrf_content[key] = r

    sorted_keys = sorted(rrf_scores.keys(), key=lambda k: -rrf_scores[k])
    return [
        {**rrf_content[k], "fused_score": round(rrf_scores[k], 4)}
        for k in sorted_keys[:max_results]
    ]


@server.tool()
def harness_arxiv(query: str, max_results: int = 10) -> str:
    """Search arXiv for academic papers.

    Args:
        query: search terms (e.g. "Thompson Sampling regret bounds")
        max_results: max papers to return (1-50)

    Returns:
        JSON list of papers with arxiv_id, title, authors, abstract,
        categories, published, pdf_url.
    """
    import json
    result = _async_run(arxiv_channel.search(query, max_results=max_results))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_scholar_s2(query: str, max_results: int = 10) -> str:
    """Search Semantic Scholar for academic papers with citation data.

    Args:
        query: search terms
        max_results: max papers (1-100)

    Returns:
        JSON list with title, abstract, authors, year, citationCount,
        DOI, arxiv_id, TLDR, fields_of_study.
    """
    import json
    result = _async_run(s2_channel.search(query, max_results=max_results))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_github_search(query: str, max_results: int = 10, language: str = "") -> str:
    """Search GitHub repositories for open-source projects.

    Args:
        query: search terms
        max_results: max repos (1-100)
        language: filter by language (e.g. "python")

    Returns:
        JSON list of repos with name, description, stars, forks, url, topics.
    """
    import json
    result = _async_run(github_channel.search_repos(query, max_results=max_results, language=language))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_paperswithcode(query: str, max_results: int = 10) -> str:
    """Search Papers with Code for papers linked to implementations.

    Args:
        query: search terms
        max_results: max results

    Returns:
        JSON list with title, abstract, paper_url, code_url, stars, area.
    """
    import json
    result = _async_run(pwc_channel.search(query, max_results=max_results))
    return json.dumps(result, indent=2, default=str)


# ── New Research Publisher Channels ───────────────────────────────────────


@server.tool()
def harness_openalex(query: str, max_results: int = 10, mode: str = "all") -> str:
    """Search OpenAlex — universal coverage of 250M+ works from ALL publishers.

    The highest-leverage single channel for journal articles. Covers
    Springer, Elsevier, Wiley, Taylor & Francis, Nature, IEEE, ACM,
    SSRN, NBER, and thousands more — all from one query.

    Args:
        query: search terms
        max_results: max results (1-200)
        mode: 'all' (default), 'journal' (peer-reviewed only),
              'ssrn' (SSRN preprints — finance/econ/law),
              'recent' (last 90 days)

    Returns:
        JSON list with title, abstract, authors, year, citation_count, doi,
        open_access_url, venue, concepts, type, is_oa.
    """
    import json
    if mode == "journal":
        result = _async_run(openalex_channel.search_journal_only(query, max_results))
    elif mode == "ssrn":
        result = _async_run(openalex_channel.search_ssrn(query, max_results))
    elif mode == "recent":
        result = _async_run(openalex_channel.search_recent(query, max_results=max_results))
    else:
        result = _async_run(openalex_channel.search(query, max_results))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_crossref(query: str, max_results: int = 10, publisher: str = "") -> str:
    """Search CrossRef — the universal DOI registry across every publisher.

    Best for DOI lookups, funder queries, and finding the canonical version
    of a preprint. Covers IEEE, ACM, Springer, Elsevier, Wiley, Nature, and
    15,000+ publishers.

    Args:
        query: bibliographic search terms
        max_results: max results (1-100)
        publisher: optional filter — 'ieee', 'acm', 'springer', 'elsevier',
                   'wiley', 'nature', 'oxford', 'tandf', 'aaas', 'plos'

    Returns:
        JSON list with title, abstract, authors, year, doi, container_title,
        publisher, citation_count, type, issn, subject.
    """
    import json
    filter_type = ""
    prefix_map = {
        "ieee": "10.1109", "acm": "10.1145", "springer": "10.1007",
        "elsevier": "10.1016", "nature": "10.1038", "wiley": "10.1111",
        "oxford": "10.1093", "tandf": "10.1080", "aaas": "10.1126",
        "plos": "10.1371",
    }
    if publisher.lower() in prefix_map:
        filter_type = f"prefix:{prefix_map[publisher.lower()]},type:journal-article"
    result = _async_run(
        crossref_channel.search(query, max_results=max_results, filter_type=filter_type)
    )
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_pubmed(query: str, max_results: int = 10, mesh: str = "") -> str:
    """Search PubMed — biomedical and life sciences literature (36M+ citations).

    Covers medicine, clinical research, genetics, molecular biology,
    neuroscience, pharmacology, and more. Includes MeSH controlled vocabulary.

    Args:
        query: PubMed query (supports MeSH terms, boolean ops, field tags)
               e.g. '"CRISPR"[mh] AND cancer[tiab]'
        max_results: max results (1-200)
        mesh: optional MeSH term filter (e.g. 'Neoplasms', 'Machine Learning')

    Returns:
        JSON list with title, abstract, authors, year, journal, pmid, doi,
        pmc_id, mesh_terms, publication_types.
    """
    import json
    if mesh:
        result = _async_run(pubmed_channel.search_mesh(mesh, max_results=max_results))
    else:
        result = _async_run(pubmed_channel.search(query, max_results=max_results))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_lookup(doi: str = "", arxiv_id: str = "", pmid: str = "") -> str:
    """Direct lookup of a paper by DOI, arXiv ID, or PMID.

    Exactly one identifier should be provided. Returns full metadata from
    the canonical source.

    Args:
        doi: e.g. '10.1109/5.771073'
        arxiv_id: e.g. '2401.04088'
        pmid: e.g. '32166017'

    Returns:
        JSON with the resolved paper's full metadata.
    """
    import json
    if doi:
        result = _async_run(crossref_channel.lookup_doi(doi))
        return json.dumps(result or {"error": f"DOI not found: {doi}"}, indent=2, default=str)
    if arxiv_id:
        result = _async_run(arxiv_channel.fetch_abstract(arxiv_id))
        return json.dumps(result or {"error": f"arXiv ID not found: {arxiv_id}"}, indent=2, default=str)
    if pmid:
        result = _async_run(pubmed_channel.search(f"{pmid}[uid]", max_results=1))
        return json.dumps(result[0] if result else {"error": f"PMID not found: {pmid}"},
                         indent=2, default=str)
    return '{"error": "Provide one of: doi, arxiv_id, pmid"}'


@server.tool()
def harness_target_site(url: str, max_pages: int = 1, depth: int = 1) -> str:
    """Harvest context from a specific project/tech documentation website.

    Fetches the page (or a small crawl), strips boilerplate, and returns
    clean structured text ready for LLM context. Handles GitHub READMEs,
    MDN, docs sites (FastAPI, Rust, k8s), and generic blogs.

    Args:
        url: seed URL to harvest (e.g. 'https://docs.python.org/3/tutorial/')
        max_pages: pages to crawl if depth > 1 (default 1 = single page)
        depth: BFS depth — 1 = seed only, 2 = seed + direct links

    Returns:
        JSON with url, title, text_content, word_count, links, and
        (if crawled) results from all pages.
    """
    import json
    if depth > 1 and max_pages > 1:
        result = _async_run(target_channel.harvest_site(url, max_pages=max_pages, depth=depth))
    else:
        result = _async_run(target_channel.fetch_page(url))
    return json.dumps(result, indent=2, default=str)


@server.tool()
def harness_study_project(
    urls: str = "",
    project: str = "default",
    max_pages: int = 3,
    depth: int = 1,
    ingest: bool = False,
) -> str:
    """Batch-harvest project documentation URLs into a compressed context block.

    Uses ``config/site_registry.yaml`` project entries when *urls* is empty.
    Comma- or newline-separated custom URLs override/extend the registry list.

    Args:
        urls: optional comma/newline-separated URLs (empty = registry project sites)
        project: project id (nexus, kaari, impeccable, …)
        max_pages: pages per URL when depth > 1
        depth: BFS crawl depth per seed URL
        ingest: if True, store harvest summary in project_contexts

    Returns:
        JSON with urls, pages, context_block, and per-page summaries.
    """
    import json
    import re

    seed_urls: list[str] = []
    if urls.strip():
        for part in re.split(r"[\n,]+", urls):
            u = part.strip()
            if u.startswith("http"):
                seed_urls.append(u)
    else:
        from server.site_registry import get_project_urls
        seed_urls = get_project_urls(project)

    if not seed_urls:
        return json.dumps({"error": f"No URLs for project '{project}'", "project": project}, indent=2)

    async def _harvest_all():
        out: list[dict] = []
        for url in seed_urls[:8]:
            if depth > 1 and max_pages > 1:
                r = await target_channel.harvest_site(url, max_pages=max_pages, depth=depth)
                for page in r.get("results") or []:
                    out.append(page)
            else:
                out.append(await target_channel.fetch_page(url))
        return out

    pages = _async_run(_harvest_all())
    from server.site_registry import compress_harvest_results
    context_block = compress_harvest_results(pages)

    result = {
        "project": project,
        "urls": seed_urls,
        "pages_harvested": len([p for p in pages if p.get("text_content")]),
        "context_block": context_block,
        "pages": [
            {
                "url": p.get("url"),
                "title": p.get("title"),
                "word_count": p.get("word_count", 0),
                "preview": (p.get("text_content") or "")[:400],
            }
            for p in pages
        ],
    }

    if ingest and context_block:
        try:
            from server.db import db_conn
            with db_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO project_contexts (project, context_type, title, content, tags_json)
                    VALUES (?, 'doc_harvest', ?, ?, ?)
                    """,
                    (project, f"study_project:{project}", context_block[:8000], json.dumps(seed_urls)),
                )
            result["ingest"] = {"status": "stored", "project": project}
        except Exception as exc:
            result["ingest"] = {"status": "error", "error": str(exc)}

    return json.dumps(result, indent=2, default=str)


@server.tool()
def research_sites_list(project: str = "") -> str:
    """List publishers and project doc URLs from ``config/site_registry.yaml``."""
    import json
    from server.site_registry import list_publishers, list_projects, research_context_for_project

    out: dict = {
        "publishers": list_publishers(),
        "projects": list_projects(),
    }
    if project:
        out["project_detail"] = research_context_for_project(project)
    return json.dumps(out, indent=2, default=str)


@server.tool()
def harness_research_plan(query: str, explain: bool = True, project: str = "default") -> str:
    """Analyze a research query and return the optimal channel execution plan.

    This is the SMART DISPATCHER: it inspects the query for DOIs, arXiv IDs,
    PMIDs, URLs, publisher names (IEEE/ACM/Springer/...), and field keywords
    (biomedical→PubMed, finance→SSRN, CS→arXiv) — then decides which channels
    to query, in what order, with what parameters.

    Use this BEFORE harness_research when you want to understand WHY certain
    channels are chosen, or to preview the plan before executing it.

    Args:
        query: the research query
        explain: if True, include a human-readable explanation

    Returns:
        JSON with detected_ids, detected_publishers, detected_fields, intent,
        and a ranked list of {channel, reason, priority, max_results, params}.
    """
    import json
    plan = dispatcher.plan(query, project=project)
    out = {
        "query": plan.query,
        "project": project,
        "detected_ids": plan.detected_ids,
        "detected_urls": plan.detected_urls,
        "detected_publishers": plan.detected_publishers,
        "detected_fields": plan.detected_fields,
        "intent": plan.intent,
        "deep_research": plan.deep_research,
        "channels": [
            {
                "channel": c.channel, "reason": c.reason,
                "priority": round(c.priority, 2),
                "max_results": c.max_results, "params": c.params,
            }
            for c in plan.channels
        ],
    }
    if explain:
        out["explanation"] = dispatcher.explain(plan)
    return json.dumps(out, indent=2, default=str)


@server.tool()
def harness_smart_research(
    query: str,
    max_results: int = 8,
    execute: bool = True,
    project: str = "default",
    ingest: bool = False,
) -> str:
    """Smart multi-channel research with automatic channel selection.

    Analyzes the query, builds an optimal channel plan, then EXECUTES it —
    querying only the relevant channels (not all 10 blindly). Fuses results
    via RRF with channel-specific weights.

    This is the recommended entry point for research queries. It routes:
      - DOI → CrossRef direct lookup
      - arXiv ID → arXiv fetch
      - PMID → PubMed lookup
      - URLs → target-site harvester
      - 'IEEE'/'ACM'/publisher names → CrossRef filtered
      - biomedical keywords → PubMed
      - finance/econ/law → SSRN via OpenAlex
      - CS/ML/physics/math → arXiv + S2 + PwC + GitHub
      - 'study'/'survey' intent → broad multi-channel sweep

    Args:
        query: research query
        max_results: max fused results
        execute: if True (default), run the plan and return fused results.
                 If False, returns only the plan (like harness_research_plan).

    Returns:
        JSON with plan + fused results (if execute=True).
    """
    import json
    plan = dispatcher.plan(query, max_results=max_results, project=project)

    if not execute:
        out = {
            "query": query, "intent": plan.intent,
            "channels": [
                {"channel": c.channel, "reason": c.reason, "priority": round(c.priority, 2)}
                for c in plan.channels
            ],
        }
        return json.dumps(out, indent=2, default=str)

    # Execute the plan — query each channel in the plan
    all_channel_results: list[dict] = []

    async def _execute_plan():
        tasks = []
        for cp in plan.channels:
            tasks.append((cp.channel, _exec_one(cp, query, max_results)))
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        for (name, _), res in zip(tasks, results):
            if isinstance(res, Exception):
                all_channel_results.append({"channel": name, "error": str(res), "results": []})
            else:
                all_channel_results.append({"channel": name, "results": res})

    _async_run(_execute_plan())
    fused = _fuse_research_results(all_channel_results, max_results=max_results * len(plan.channels))

    payload = {
        "query": query,
        "intent": plan.intent,
        "project": project,
        "detected_fields": plan.detected_fields,
        "detected_publishers": plan.detected_publishers,
        "channels_executed": [c.channel for c in plan.channels],
        "per_channel": all_channel_results,
        "fused": fused,
    }
    if ingest and fused:
        try:
            from server.research_ingest import ingest_from_fused_payload
            payload["ingest"] = ingest_from_fused_payload(payload, project=project)
        except Exception as exc:
            payload["ingest"] = {"status": "error", "error": str(exc)}

    return json.dumps(payload, indent=2, default=str)


async def _exec_one(cp, query: str, max_results: int):
    """Execute a single ChannelPlan against its channel, return raw results.

    *query* is the original research query — passed verbatim to search-based
    channels. Lookup channels (crossref_doi, arxiv_fetch, pubmed_lookup) use
    params extracted by the dispatcher instead.
    """
    ch = cp.channel
    if ch == "arxiv":
        return await arxiv_channel.search(query, max_results=max_results)
    if ch == "arxiv_fetch":
        r = await arxiv_channel.fetch_abstract(cp.params["arxiv_id"])
        return [r] if r else []
    if ch == "openalex":
        return await openalex_channel.search(query, max_results=max_results)
    if ch == "openalex_ssrn":
        return await openalex_channel.search_ssrn(query, max_results=max_results)
    if ch in ("openalex_harvard", "openalex_publisher"):
        filt = cp.params.get("filter_type", "")
        return await openalex_channel.search(query, max_results=max_results, filter_type=filt)
    if ch == "crossref":
        return await crossref_channel.search(
            query, max_results=max_results,
            filter_type=cp.params.get("filter_type", ""),
        )
    if ch == "crossref_doi":
        r = await crossref_channel.lookup_doi(cp.params["doi"])
        return [r] if r else []
    if ch == "semantic_scholar":
        return await s2_channel.search(query, max_results=max_results)
    if ch == "pubmed":
        return await pubmed_channel.search(query, max_results=max_results)
    if ch == "pubmed_lookup":
        return await pubmed_channel.search(f"{cp.params['pmid']}[uid]", max_results=1)
    if ch == "github":
        return await github_channel.search_repos(query, max_results=max_results)
    if ch == "paperswithcode":
        return await pwc_channel.search(query, max_results=max_results)
    if ch == "google_scholar":
        return await scholar_channel.search(query, max_results=max_results)
    if ch == "target_site":
        r = await target_channel.fetch_page(cp.params["url"])
        if r.get("text_content"):
            return [{
                "title": r.get("title", ""),
                "abstract": r.get("text_content", "")[:2000],
                "url": r.get("url", ""), "source": "target_site",
            }]
        return []
    return []


# ── RRF Backend Registration ──────────────────────────────────────────────


def _register_rrf_backends() -> None:
    fuser = get_fuser()
    if "scrapling_fetch" in fuser.backend_names:
        return

    async def _fetch_backend(query: str, top_k: int):
        import json
        from server.context_harness.models import HarnessResult

        result = await fetch_channel.fetch(f"https://duckduckgo.com/html/?q={query}", mode="static")
        text = result.get("text_content", "")[:1000]
        return [HarnessResult(source="scrapling_fetch", content=text, score=0.5, metadata={"url": result.get("url", "")})]

    fuser.register(BackendDef(
        name="scrapling_fetch",
        weight=1.2,
        fetch=lambda q, k: _async_run(_fetch_backend(q, k)),
        cache_ttl=300,
    ))

    async def _sherlock_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult

        await sherlock_channel.initialize()
        result = await sherlock_channel.probe_username(query, timeout=30.0)
        found = result.get("results", {})
        items = [
            HarnessResult(
                source="sherlock_probe",
                content=f"Username '{query}' {'found' if v.get('exists') else 'not found'} on {k}",
                score=0.9 if v.get("exists") else 0.1,
                metadata={"site": k, "url": v.get("url", ""), "status": v.get("status")},
            )
            for k, v in found.items()
            if v.get("exists")
        ]
        return items[:top_k]

    fuser.register(BackendDef(
        name="sherlock_probe",
        weight=0.8,
        fetch=lambda q, k: _async_run(_sherlock_backend(q, k)),
        cache_ttl=600,
    ))

    async def _pattern_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult

        result = await pattern_channel.extract(
            f"https://duckduckgo.com/html/?q={query}",
            fields=None,
        )
        extracted = result.get("extracted", {})
        return [
            HarnessResult(
                source="scrapecraft_pattern",
                content=str(v),
                score=0.7,
                metadata={"field": k, "method": result.get("method", "")},
            )
            for k, v in extracted.items()
        ][:top_k]

    fuser.register(BackendDef(
        name="scrapecraft_pattern",
        weight=1.5,
        fetch=lambda q, k: _async_run(_pattern_backend(q, k)),
        cache_ttl=300,
    ))

    async def _spider_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult

        result = await spider_channel.start_spider(
            f"https://duckduckgo.com/html/?q={query}",
            max_pages=5,
        )
        return [
            HarnessResult(
                source="scrapling_spider",
                content=f"Spider {result.get('spider_id', '')}: {result.get('status', '')}",
                score=0.6,
                metadata=result,
            )
        ][:top_k]

    fuser.register(BackendDef(
        name="scrapling_spider",
        weight=1.0,
        fetch=lambda q, k: _async_run(_spider_backend(q, k)),
        cache_ttl=600,
    ))

    # ── Academic Research Backends ──────────────────────────────────────

    async def _arxiv_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await arxiv_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="arxiv",
                content=f"[arXiv:{p.get('arxiv_id','')}] {p.get('title','')} — {', '.join(p.get('authors',[])[:3])}. {p.get('abstract','')[:300]}",
                score=0.9,
                metadata={"arxiv_id": p.get("arxiv_id", ""), "url": p.get("abs_url", ""), "year": p.get("published", "")[:4]},
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="arxiv", weight=1.5,
        fetch=lambda q, k: _async_run(_arxiv_backend(q, k)),
        cache_ttl=600,
    ))

    async def _s2_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await s2_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="semantic_scholar",
                content=f"[{p.get('year','')}] {p.get('title','')} — {', '.join(p.get('authors',[])[:3])}. Citations: {p.get('citation_count',0)}. {p.get('abstract','')[:300]}",
                score=0.85,
                metadata={"url": p.get("url", ""), "doi": p.get("doi", ""), "arxiv_id": p.get("arxiv_id", "")},
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="semantic_scholar", weight=1.3,
        fetch=lambda q, k: _async_run(_s2_backend(q, k)),
        cache_ttl=600,
    ))

    async def _github_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        repos = await github_channel.search_repos(query, max_results=top_k)
        return [
            HarnessResult(
                source="github",
                content=f"[GitHub] {r.get('full_name','')} ★{r.get('stars',0)} — {r.get('description','')}. Language: {r.get('language','')}. Topics: {', '.join(r.get('topics',[])[:5])}",
                score=0.7,
                metadata={"url": r.get("url", ""), "stars": r.get("stars", 0), "license": r.get("license", "")},
            )
            for r in repos
        ]

    fuser.register(BackendDef(
        name="github", weight=1.0,
        fetch=lambda q, k: _async_run(_github_backend(q, k)),
        cache_ttl=300,
    ))

    async def _pwc_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await pwc_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="paperswithcode",
                content=f"[PwC] {p.get('title','')} — {p.get('abstract','')[:300]}. Code: {p.get('code_url','')}",
                score=0.8,
                metadata={"paper_url": p.get("paper_url", ""), "code_url": p.get("code_url", ""), "arxiv_id": p.get("arxiv_id", "")},
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="paperswithcode", weight=1.4,
        fetch=lambda q, k: _async_run(_pwc_backend(q, k)),
        cache_ttl=600,
    ))

    async def _scholar_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await scholar_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="google_scholar",
                content=f"[Scholar] {p.get('title','')} — {p.get('authors','')} ({p.get('year','')}). {p.get('snippet','')[:300]}",
                score=0.75,
                metadata={"url": p.get("url", "")},
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="google_scholar", weight=0.9,
        fetch=lambda q, k: _async_run(_scholar_backend(q, k)),
        cache_ttl=600,
    ))

    # ── OpenAlex backend — universal publisher coverage ─────────────────
    async def _openalex_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await openalex_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="openalex",
                content=f"[{p.get('venue','')}] {p.get('title','')} — {', '.join(p.get('authors',[])[:3])} ({p.get('year','')}). Citations: {p.get('citation_count',0)}. {p.get('abstract','')[:300]}",
                score=0.88,
                metadata={
                    "doi": p.get("doi", ""), "url": p.get("open_access_url") or p.get("url", ""),
                    "venue": p.get("venue", ""), "is_oa": p.get("is_oa", False),
                    "concepts": p.get("concepts", []),
                },
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="openalex", weight=1.4,
        fetch=lambda q, k: _async_run(_openalex_backend(q, k)),
        cache_ttl=600,
    ))

    # ── CrossRef backend — DOI/publisher search ─────────────────────────
    async def _crossref_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await crossref_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="crossref",
                content=f"[{p.get('container_title','')}] {p.get('title','')} — {', '.join(p.get('authors',[])[:3])} ({p.get('year','')}). {p.get('publisher','')}. {p.get('abstract','')[:300]}",
                score=0.85,
                metadata={
                    "doi": p.get("doi", ""), "url": p.get("url", ""),
                    "publisher": p.get("publisher", ""), "type": p.get("type", ""),
                },
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="crossref", weight=1.3,
        fetch=lambda q, k: _async_run(_crossref_backend(q, k)),
        cache_ttl=600,
    ))

    # ── PubMed backend — biomedical literature ─────────────────────────
    async def _pubmed_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        papers = await pubmed_channel.search(query, max_results=top_k)
        return [
            HarnessResult(
                source="pubmed",
                content=f"[{p.get('journal','')}] {p.get('title','')} — {', '.join(p.get('authors',[])[:3])} ({p.get('year','')}). MeSH: {', '.join(p.get('mesh_terms',[])[:3])}. {p.get('abstract','')[:300]}",
                score=0.92,
                metadata={
                    "pmid": p.get("pmid", ""), "doi": p.get("doi", ""),
                    "url": p.get("url", ""), "pmc_id": p.get("pmc_id", ""),
                    "mesh_terms": p.get("mesh_terms", []),
                },
            )
            for p in papers
        ]

    fuser.register(BackendDef(
        name="pubmed", weight=1.5,
        fetch=lambda q, k: _async_run(_pubmed_backend(q, k)),
        cache_ttl=600,
    ))

    # ── Target-site backend — specific project/tech websites ────────────
    # Only fires when the query contains a URL (detected at fetch-time, not
    # registered as a backend that fires on every query). Registered low-
    # weight so it doesn't compete with academic channels on text queries.
    async def _target_backend(query: str, top_k: int):
        from server.context_harness.models import HarnessResult
        import re
        urls = re.findall(r"https?://[^\s\"<>]+", query)
        if not urls:
            return []
        results = []
        for url in urls[:top_k]:
            r = await target_channel.fetch_page(url)
            if r.get("text_content"):
                results.append(HarnessResult(
                    source="target_site",
                    content=f"[{r.get('domain','')}] {r.get('title','')}: {r.get('text_content','')[:500]}",
                    score=0.95,
                    metadata={"url": url, "word_count": r.get("word_count", 0)},
                ))
        return results

    fuser.register(BackendDef(
        name="target_site", weight=1.6,
        fetch=lambda q, k: _async_run(_target_backend(q, k)),
        cache_ttl=300,
    ))


def _async_run(coro):
    """Run an awaitable from both sync and async contexts.

    Uses nest_asyncio to allow nested event loops in the running event loop case.
    """
    import asyncio
    import nest_asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside a running loop — use nest_asyncio to allow re-entry
    nest_asyncio.apply(loop)
    return loop.run_until_complete(coro)


_register_rrf_backends()
