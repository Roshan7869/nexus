"""Hermes-style toolsets: load only what the task needs (P3)."""
from __future__ import annotations

import re
from typing import Iterable

# Always available on NEXUS MCP (resource intelligence)
CORE_TOOLS: tuple[str, ...] = (
    "find_skills",
    "report_outcome",
    "router_status",
    "learning_stats",
)

RESEARCH_TOOLS: tuple[str, ...] = (
    "harness_research",
    "harness_smart_research",
    "harness_research_plan",
    "harness_arxiv",
    "harness_scholar_s2",
    "harness_github_search",
    "harness_paperswithcode",
    "harness_openalex",
    "harness_crossref",
    "harness_pubmed",
    "research_sites_list",
)

BROWSER_TOOLS: tuple[str, ...] = (
    "harness_fetch",
    "harness_fetch_batch",
    "harness_target_site",
    "harness_spider_start",
    "harness_spider_status",
    "harness_spider_results",
    "harness_pattern_extract",
    "harness_sherlock",
    "browser_harness_status",  # adapter stub
)

MEMORY_TOOLS: tuple[str, ...] = (
    "context_harness",
    "context_search",
    "context_store",
    "context_doctor",
)

TOOLSETS: dict[str, tuple[str, ...]] = {
    "core": CORE_TOOLS,
    "research": RESEARCH_TOOLS,
    "browser": BROWSER_TOOLS,
    "memory": MEMORY_TOOLS,
}

_RESEARCH_RE = re.compile(
    r"\b(?:research|paper|papers|arxiv|ssrn|study|survey|literature|academic|"
    r"journal|citation|cite|doi|semantic.?scholar|papers.?with.?code|"
    r"state.of.the.art|bibliography|peer.review|"
    # added: common scholarly phrasings that missed the original list
    # (e.g. "retrieval augmented generation" scored as general)
    r"retrieval|benchmark|dataset|preprint|proceedings|manuscript|"
    r"publication|corpus|methodology)\b",
    re.I,
)
_BROWSE_RE = re.compile(
    r"\b(?:browse|browser|scrape|scraping|website|web page|screenshot|"
    r"click|login to|fill form|cdp|playwright)\b",
    re.I,
)
_MEMORY_RE = re.compile(
    r"\b(?:remember|recall|learning note|what did we|prior session|"
    r"store context|search memory)\b",
    re.I,
)


def detect_intent(task: str) -> str:
    t = task or ""
    if _RESEARCH_RE.search(t):
        return "research"
    if _BROWSE_RE.search(t):
        return "browse"
    if _MEMORY_RE.search(t):
        return "memory"
    return "general"


def plan_toolsets(task: str, *, always_memory: bool = True) -> list[str]:
    """Return ordered toolset names for a task."""
    intent = detect_intent(task)
    sets = ["core"]
    if intent == "research":
        sets.append("research")
        sets.append("memory")
    elif intent == "browse":
        sets.append("browser")
        sets.append("memory")
    elif intent == "memory":
        sets.append("memory")
    elif always_memory:
        sets.append("memory")
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for s in sets:
        if s not in seen and s in TOOLSETS:
            seen.add(s)
            out.append(s)
    return out


def tools_for_toolsets(toolsets: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ts in toolsets:
        for name in TOOLSETS.get(ts, ()):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def is_research_intent(task: str) -> bool:
    return detect_intent(task) == "research"


def is_browse_intent(task: str) -> bool:
    return detect_intent(task) == "browse"
