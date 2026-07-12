"""Smart channel dispatcher — intent detection → channel selection.

This is the "which tool for which task" brain. It analyzes a query and
decides which research/web channels are most relevant, in what order, and
with what parameters — so the harness doesn't blindly query all 10 backends
for every query (slow, rate-limit-prone, noisy).

Decision signals:
  - Keyword/phrase matching (publisher names, domain terms)
  - DOI/PMID/arXiv ID detection (→ direct lookup channels)
  - URL detection (→ target-site harvester)
  - Field/discipline inference (CS→arxiv, bio→pubmed, finance→SSRN)
  - Intent verbs (study/research/implement/compare/benchmark)
  - NEXUS cluster routing (reuses the existing cluster centroids)

Output: a ranked plan of (channel, params, expected_yield) tuples that the
harness executes in priority order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── ID extractors (direct-lookup triggers) ─────────────────────────────────

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+\b", re.IGNORECASE)
PMID_RE = re.compile(r"\b(?:PMID[:\s]*)(\d{4,9})\b", re.IGNORECASE)
ARXIV_RE = re.compile(r"\b(?:arxiv[:\s]*)(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE)
OPENALEX_RE = re.compile(r"\b(?:openalex[:\s]*)(W\d+)\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\"<>]+", re.IGNORECASE)


# ── Publisher / domain keyword maps ────────────────────────────────────────

PUBLISHER_HINTS: dict[str, list[str]] = {
    "ieee": ["ieee", "ieeexplore", "10.1109"],
    "acm": ["acm", "acm digital library", "10.1145"],
    "springer": ["springer", "springer nature", "10.1007"],
    "elsevier": ["elsevier", "science direct", "sciencedirect", "10.1016"],
    "wiley": ["wiley", "10.1111", "10.1002"],
    "nature": ["nature", "nature.com", "10.1038"],
    "oxford": ["oup", "oxford university press", "10.1093"],
    "tandf": ["taylor francis", "tandfonline", "10.1080"],
    "aaas": ["aaas", "science magazine", "10.1126"],
    "plos": ["plos", "10.1371"],
    "mdpi": ["mdpi", "10.3390"],
    "sage": ["sage", "10.1177"],
    "acm_citation": ["proceedings of", "transactions on", "symposium on"],
}

# Research-field → channel hints. Used when the query contains domain
# vocabulary that signals a specific discipline.
FIELD_HINTS: dict[str, list[str]] = {
    # Biomedical → PubMed is primary
    "pubmed": [
        "biomedical", "clinical", "patient", "disease", "treatment",
        "drug", "therapy", "diagnosis", "prognosis", "pathology",
        "protein", "gene", "genome", "cell", "tissue", "molecular",
        "cancer", "tumor", "infection", "vaccine", "antibody", "rna", "dna",
        "crispr", "alphafold", "epidemiolog", "pharmac", "neurosci",
        "immunolog", "cardiovascular", "metabolic", "trial",
        "medicine", "medical", "health",
    ],
    # Finance/econ/law → SSRN via OpenAlex
    "ssrn": [
        "finance", "financial", "stock", "portfolio", "asset pricing",
        "market", "trading", "derivatives", "cryptocurrenc", "bitcoin",
        "monetary", "banking", "fiscal", "economics", "econometri",
        "economet", "regression discontinuity", "instrumental variable",
        "panel data", "accounting", "audit", "corporate governance",
        "law", "legal", "regulation", "antitrust", "constitutional",
        "tort", "contract law",
    ],
    # CS / ML / AI → arXiv + PwC + GitHub
    "cs": [
        "neural", "deep learning", "transformer", "attention", "gpt", "llm",
        "embedding", "reinforcement learning", "bandit", "thompson sampling",
        "gradient", "backprop", "convolutional", "transformer", "diffusion",
        "gan", "autoencoder", "language model", "code generation",
        "machine learning", "ml", "ai", "artificial intelligence",
        "algorithm", "complexity", "graph theory", "distributed systems",
        "database", "compilation", "operating system", "networking",
        "security", "cryptography", "blockchain", "consensus",
        "software engineering", "devops", "kubernetes", "docker",
        "retrieval", "rag", "rerank", "search engine",
        "robotics", "computer vision", "nlp", "speech",
    ],
    # Physics / math → arXiv
    "physics_math": [
        "quantum", "particle", "relativity", "cosmology", "topology",
        "manifold", "theorem", "proof", "algebra", "calculus",
        "differential equation", "probability", "stochastic",
        "mathematic", "physics", "statistical mechanics",
    ],
}

# Intent verbs → research depth
STUDY_VERBS = [
    "study", "research", "investigate", "explore", "survey", "review",
    "understand", "analyze", "analyse", "learn",
]
BUILD_VERBS = [
    "implement", "build", "create", "develop", "design", "construct",
    "code", "write", "make", "generate",
]
COMPARE_VERBS = [
    "compare", "contrast", "versus", "vs", "benchmark", "evaluate",
    "evaluate", "best",
]


@dataclass
class ChannelPlan:
    """A single channel entry in a research plan."""
    channel: str              # 'arxiv', 'openalex', 'pubmed', etc.
    reason: str               # why this channel was selected
    priority: float           # 0.0-1.0, higher = run first
    max_results: int          # suggested result count
    params: dict = field(default_factory=dict)  # channel-specific params


@dataclass
class ResearchPlan:
    """Full research plan for a query — which channels, in what order."""
    query: str
    detected_ids: dict[str, str]    # {'doi': '10.xxxx/...', 'arxiv': '2401.xxxx', ...}
    detected_urls: list[str]
    detected_publishers: list[str]
    detected_fields: list[str]
    intent: str                     # 'study' | 'build' | 'compare' | 'lookup' | 'general'
    channels: list[ChannelPlan]     # ranked by priority
    deep_research: bool = False     # whether to traverse citation graph


class ChannelDispatcher:
    """Analyze a query and produce a ranked channel-execution plan."""

    def plan(
        self,
        query: str,
        *,
        max_results: int = 10,
        project: str = "default",
    ) -> ResearchPlan:
        q_lower = query.lower()

        # ── 1. Detect direct IDs (DOI, PMID, arXiv, OpenAlex) ───────────
        detected_ids: dict[str, str] = {}
        if m := DOI_RE.search(query):
            detected_ids["doi"] = m.group(0)
        if m := ARXIV_RE.search(query):
            detected_ids["arxiv"] = m.group(1)
        if m := PMID_RE.search(query):
            detected_ids["pmid"] = m.group(1)
        if m := OPENALEX_RE.search(query):
            detected_ids["openalex"] = m.group(1)

        # ── 2. Detect URLs → target-site harvester ──────────────────────
        detected_urls = URL_RE.findall(query)

        # ── 3. Detect named publishers (registry + built-in hints) ─────
        detected_publishers: list[str] = []
        try:
            from server.site_registry import match_publishers
            detected_publishers.extend(match_publishers(query))
        except Exception:
            pass
        for key, hints in PUBLISHER_HINTS.items():
            if key not in detected_publishers and any(h in q_lower for h in hints):
                detected_publishers.append(key)
        # preserve order, dedupe
        seen_pub: set[str] = set()
        detected_publishers = [p for p in detected_publishers if not (p in seen_pub or seen_pub.add(p))]

        # ── 4. Detect research fields ───────────────────────────────────
        detected_fields: list[str] = []
        for field_name, hints in FIELD_HINTS.items():
            if any(h in q_lower for h in hints):
                detected_fields.append(field_name)

        # ── 5. Detect intent ────────────────────────────────────────────
        if any(v in q_lower for v in STUDY_VERBS):
            intent = "study"
        elif any(v in q_lower for v in BUILD_VERBS):
            intent = "build"
        elif any(v in q_lower for v in COMPARE_VERBS):
            intent = "compare"
        elif detected_ids:
            intent = "lookup"
        else:
            intent = "general"

        # ── 6. Build the channel plan ───────────────────────────────────
        channels: list[ChannelPlan] = []

        # Direct ID lookup — highest priority, bypasses search
        if "doi" in detected_ids:
            channels.append(ChannelPlan(
                channel="crossref_doi",
                reason=f"DOI detected: {detected_ids['doi']}",
                priority=1.0,
                max_results=1,
                params={"doi": detected_ids["doi"]},
            ))
        if "arxiv" in detected_ids:
            channels.append(ChannelPlan(
                channel="arxiv_fetch",
                reason=f"arXiv ID detected: {detected_ids['arxiv']}",
                priority=1.0,
                max_results=1,
                params={"arxiv_id": detected_ids["arxiv"]},
            ))
        if "pmid" in detected_ids:
            channels.append(ChannelPlan(
                channel="pubmed_lookup",
                reason=f"PMID detected: {detected_ids['pmid']}",
                priority=1.0,
                max_results=1,
                params={"pmid": detected_ids["pmid"]},
            ))

        # Target URLs → harvester
        for url in detected_urls[:3]:
            channels.append(ChannelPlan(
                channel="target_site",
                reason=f"URL detected: {url}",
                priority=0.95,
                max_results=1,
                params={"url": url},
            ))

        # Registry publishers (Harvard, HBR, SSRN, …)
        for pub_id in detected_publishers:
            try:
                from server.site_registry import publisher_channel_spec
                spec = publisher_channel_spec(pub_id)
            except Exception:
                spec = None
            if not spec:
                continue
            channel = spec.get("channel", "")
            priority = float(spec.get("priority", 0.85))
            params: dict[str, Any] = {"publisher_id": pub_id}
            if spec.get("openalex_filter"):
                params["filter_type"] = spec["openalex_filter"]
            if spec.get("crossref_prefix"):
                params["filter_type"] = f"prefix:{spec['crossref_prefix']},type:journal-article"
            if channel and not any(c.channel == channel and c.params.get("publisher_id") == pub_id for c in channels):
                channels.append(ChannelPlan(
                    channel=channel,
                    reason=f"Site registry publisher: {spec.get('label', pub_id)}",
                    priority=priority,
                    max_results=max_results,
                    params=params,
                ))

        # Project-scoped doc URLs from site_registry
        try:
            from server.site_registry import get_project_urls, project_matches_query
            if project_matches_query(project, query) or intent in ("study", "build"):
                for url in get_project_urls(project)[:3]:
                    if url not in detected_urls:
                        channels.append(ChannelPlan(
                            channel="target_site",
                            reason=f"Project site ({project}): {url}",
                            priority=0.82,
                            max_results=1,
                            params={"url": url, "project": project},
                        ))
        except Exception:
            pass

        # Publisher-specific → CrossRef filtered search (legacy hints)
        if "ieee" in detected_publishers and not any(c.params.get("publisher_id") == "ieee" for c in channels):
            channels.append(ChannelPlan(
                channel="crossref", reason="IEEE publisher hint",
                priority=0.9, max_results=max_results,
                params={"filter_type": "prefix:10.1109,type:journal-article"},
            ))
        if "acm" in detected_publishers:
            channels.append(ChannelPlan(
                channel="crossref", reason="ACM publisher hint",
                priority=0.9, max_results=max_results,
                params={"filter_type": "prefix:10.1145,type:journal-article"},
            ))
        if "springer" in detected_publishers:
            channels.append(ChannelPlan(
                channel="crossref", reason="Springer publisher hint",
                priority=0.85, max_results=max_results,
                params={"filter_type": "prefix:10.1007,type:journal-article"},
            ))

        # Field-specific routing
        if "pubmed" in detected_fields and not detected_ids:
            channels.append(ChannelPlan(
                channel="pubmed", reason="Biomedical field keywords",
                priority=0.9, max_results=max_results,
            ))
        if "ssrn" in detected_fields and not any(c.channel == "openalex_ssrn" for c in channels):
            channels.append(ChannelPlan(
                channel="openalex_ssrn", reason="Finance/econ/law keywords",
                priority=0.9, max_results=max_results,
            ))
        if "cs" in detected_fields or "physics_math" in detected_fields:
            channels.append(ChannelPlan(
                channel="arxiv", reason="CS/physics/math keywords",
                priority=0.85, max_results=max_results,
            ))

        # General fallbacks — run these if nothing more specific fired,
        # OR if the intent is 'study' (broad survey wants multiple sources)
        has_specific = any(c.priority >= 0.85 for c in channels)
        if not has_specific or intent == "study":
            if not any(c.channel == "openalex" for c in channels):
                channels.append(ChannelPlan(
                    channel="openalex", reason="Universal journal coverage",
                    priority=0.7 if has_specific else 0.8,
                    max_results=max_results,
                ))
            if not any(c.channel == "semantic_scholar" for c in channels):
                channels.append(ChannelPlan(
                    channel="semantic_scholar", reason="CS-heavy citation graph",
                    priority=0.65, max_results=max_results,
                ))
            if not any(c.channel == "crossref" for c in channels):
                channels.append(ChannelPlan(
                    channel="crossref", reason="Cross-publisher DOI search",
                    priority=0.6, max_results=max_results,
                ))

        # Always include code-implementation channels for 'build' intent
        if intent in ("build", "compare"):
            channels.append(ChannelPlan(
                channel="github", reason="Implementation search (build/compare intent)",
                priority=0.75, max_results=max_results,
            ))
            channels.append(ChannelPlan(
                channel="paperswithcode", reason="Paper+code pairs",
                priority=0.72, max_results=max_results,
            ))

        # Scholar as a low-priority fallback (scrape quality varies)
        if intent == "study" and not detected_ids:
            channels.append(ChannelPlan(
                channel="google_scholar", reason="Broad survey fallback",
                priority=0.4, max_results=max_results,
            ))

        # Deep research: traverse citation graph for study intent with high-value hits
        deep = intent == "study" and (
            "cs" in detected_fields or "pubmed" in detected_fields
            or len(detected_publishers) > 0
        )

        # Sort by priority descending
        channels.sort(key=lambda c: -c.priority)

        return ResearchPlan(
            query=query,
            detected_ids=detected_ids,
            detected_urls=detected_urls,
            detected_publishers=detected_publishers,
            detected_fields=detected_fields,
            intent=intent,
            channels=channels,
            deep_research=deep,
        )

    def explain(self, plan: ResearchPlan) -> str:
        """Human-readable explanation of why each channel was chosen."""
        lines = [
            f"Query: {plan.query}",
            f"Intent: {plan.intent}",
            f"Detected IDs: {plan.detected_ids or 'none'}",
            f"Detected URLs: {plan.detected_urls or 'none'}",
            f"Publishers: {plan.detected_publishers or 'none'}",
            f"Fields: {plan.detected_fields or 'none'}",
            f"Deep research: {plan.deep_research}",
            "",
            "Channel plan (priority order):",
        ]
        for i, c in enumerate(plan.channels, 1):
            lines.append(
                f"  {i}. {c.channel} (priority={c.priority:.2f}, "
                f"max={c.max_results}) — {c.reason}"
            )
        return "\n".join(lines)


_dispatcher: ChannelDispatcher | None = None


def get_dispatcher() -> ChannelDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = ChannelDispatcher()
    return _dispatcher
