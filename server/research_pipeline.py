"""P9: Multi-source research synthesis pipeline.

NEXUS autonomously gathers research from arXiv, GitHub, SSRN, and local repos,
then synthesizes findings and maps them to NEXUS issues.

Sources are queried in parallel via threads; each source returns a normalized
list of findings. Synthesis finds cross-source concept overlaps and unique
insights.

Design notes:
  - Pure stdlib + urllib (no new deps). Each source adapter is a small class.
  - Network calls are optional and best-effort: a source that errors returns
    an empty list with a status note, never raises. This keeps the pipeline
    usable offline (local source always works).
  - Results are plain dicts so they can be stored, logged, or fed to the
    unified router for resource mapping.

Usage:
    from server.research_pipeline import ResearchPipeline
    rp = ResearchPipeline()
    report = rp.research("tool routing for LLM agents", sources=["arxiv","github","local"])
"""
from __future__ import annotations
from server.db import get_db_path as _resolve_db_path

import json
import os
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DB_PATH = _resolve_db_path()
USER_AGENT = "NEXUS-research-pipeline/1.0 (mailto:research@nexus.local)"
HTTP_TIMEOUT = 20


@dataclass
class Finding:
    source: str
    title: str
    url: str
    summary: str
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"source": self.source, "title": self.title,
                "url": self.url, "summary": self.summary, "raw": self.raw}


def _http_get(url: str, headers: dict | None = None) -> str | None:
    """Best-effort HTTP GET. Returns text or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


class ArxivSource:
    name = "arxiv"
    BASE = "https://export.arxiv.org/api/query"

    def search(self, topic: str, limit: int = 10) -> list[Finding]:
        params = urllib.parse.urlencode({
            "search_query": f"all:{topic}", "max_results": str(limit),
            "sortBy": "relevance", "sort_order": "descending",
        })
        text = _http_get(f"{self.BASE}?{params}")
        if not text:
            return []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        findings: list[Finding] = []
        try:
            root = ET.fromstring(text)
            for entry in root.findall("a:entry", ns)[:limit]:
                title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
                url = entry.findtext("a:id", default="", namespaces=ns) or ""
                summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
                findings.append(Finding("arxiv", title, url, re.sub(r"\s+", " ", summary)[:300]))
        except ET.ParseError:
            pass
        return findings


class GithubSource:
    name = "github"
    BASE = "https://api.github.com/search/repositories"

    def search(self, topic: str, limit: int = 10) -> list[Finding]:
        token = os.environ.get("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = urllib.parse.urlencode({"q": topic, "sort": "stars", "order": "desc", "per_page": str(limit)})
        text = _http_get(f"{self.BASE}?{params}", headers=headers)
        if not text:
            return []
        findings: list[Finding] = []
        try:
            data = json.loads(text)
            for repo in data.get("items", [])[:limit]:
                findings.append(Finding(
                    "github", repo.get("full_name", ""),
                    repo.get("html_url", ""),
                    (repo.get("description") or "")[:300],
                    {"stars": repo.get("stargazers_count"), "language": repo.get("language")},
                ))
        except json.JSONDecodeError:
            pass
        return findings


class SsrnSource:
    """Best-effort SSRN (sol3) paper search via urllib HTML scrape.

    SSRN has no public JSON API, so this scrapes the sol3 results page HTML
    for paper titles + abstract links. Offline-safe: ANY failure (no network,
    SSRN blocks the request, HTML format changes, parse error) returns []
    — the research pipeline then continues with the other sources. Never
    raises.
    """
    name = "ssrn"
    BASE = "https://papers.ssrn.com/sol3/results.cfm"

    def search(self, topic: str, limit: int = 10) -> list[Finding]:
        params = urllib.parse.urlencode({"txtKey_Words": topic})
        text = _http_get(f"{self.BASE}?{params}")
        if not text:
            return []
        findings: list[Finding] = []
        try:
            # SSRN result entries link to /sol3/papers.cfm?abstract_id=<id>.
            # Extract abstract_id, then pull the surrounding title text.
            for m in re.finditer(
                r'papers\.cfm\?abstract_id=(\d+)[^>]*>([^<]+)</a>', text
            ):
                aid, title = m.group(1), m.group(2).strip()
                if not title:
                    continue
                findings.append(Finding(
                    "ssrn", title,
                    f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={aid}",
                    "",  # abstract not on the results page; left empty
                    {"abstract_id": aid},
                ))
                if len(findings) >= limit:
                    break
        except Exception:
            return []
        return findings


class LocalSource:
    """Scan local NEXUS resources for matches against the topic via FTS5."""
    name = "local"

    def search(self, topic: str, limit: int = 10) -> list[Finding]:
        findings: list[Finding] = []
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            # sanitize for FTS5 (reuse router_v2/fts5 prepare_query logic inline)
            cleaned = "".join(ch for ch in topic if ch not in "*:^\"()-+")
            tokens = [f'"{t}"' for t in re.split(r"\s+", cleaned) if t and t.upper() not in {"AND", "OR", "NOT", "NEAR"}]
            match = " ".join(tokens)
            if not match:
                return findings
            rows = conn.execute(
                "SELECT c.resource_id, c.heading, c.content, r.name, r.repo_name "
                "FROM chunks_fts f JOIN ingestion_chunks c ON f.rowid = c.id "
                "LEFT JOIN resources r ON c.resource_id = r.id "
                "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                (match, limit),
            ).fetchall()
            for r in rows:
                findings.append(Finding(
                    "local", r["heading"] or r["name"] or f"resource#{r['resource_id']}",
                    f"resource://{r['resource_id']}",
                    (r["content"] or "")[:300],
                    {"resource_id": r["resource_id"], "repo": r["repo_name"]},
                ))
            conn.close()
        except Exception:
            pass
        return findings


SOURCES = {"arxiv": ArxivSource, "github": GithubSource,
           "local": LocalSource, "ssrn": SsrnSource}


class ResearchPipeline:
    """Multi-source research synthesis engine."""

    def research(self, topic: str, sources: list[str] | None = None) -> dict[str, Any]:
        sources = sources or list(SOURCES.keys())
        results: dict[str, list[Finding]] = {}
        threads: list[threading.Thread] = []
        lock = threading.Lock()

        def _run(src_name: str):
            cls = SOURCES.get(src_name)
            if cls is None:
                with lock:
                    results[src_name] = []
                return
            try:
                found = cls().search(topic, limit=10)
            except Exception:
                found = []
            with lock:
                results[src_name] = found

        for src in sources:
            t = threading.Thread(target=_run, args=(src,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=HTTP_TIMEOUT + 5)

        return self._synthesize(topic, results)

    def _synthesize(self, topic: str, results: dict[str, list[Finding]]) -> dict[str, Any]:
        all_findings = [f for fs in results.values() for f in fs]
        return {
            "topic": topic,
            "sources_queried": list(results.keys()),
            "total_findings": len(all_findings),
            "per_source": {k: len(v) for k, v in results.items()},
            "findings": [f.to_dict() for f in all_findings],
            "cross_source_overlaps": self._find_overlaps(results),
            "unique_insights": self._extract_unique(results),
            "nexus_issue_mapping": self._map_to_issues(topic, all_findings),
        }

    def _find_overlaps(self, results: dict[str, list[Finding]]) -> list[dict]:
        """Find shared concepts across sources by title keyword overlap."""
        sources = [s for s in results if results[s]]
        overlaps: list[dict] = []
        for i in range(len(sources)):
            for j in range(i + 1, len(sources)):
                a, b = sources[i], sources[j]
                a_words = {w.lower() for f in results[a] for w in re.findall(r"\w{4,}", f.title)}
                b_words = {w.lower() for f in results[b] for w in re.findall(r"\w{4,}", f.title)}
                shared = a_words & b_words
                if shared:
                    overlaps.append({"sources": [a, b], "shared_terms": sorted(shared)[:10]})
        return overlaps

    def _extract_unique(self, results: dict[str, list[Finding]]) -> dict[str, list[str]]:
        """Per-source titles that don't share 4+ char keywords with any other source."""
        out: dict[str, list[str]] = {}
        all_words: dict[str, set[str]] = {s: {w.lower() for f in results[s] for w in re.findall(r"\w{4,}", f.title)} for s in results}
        for s in results:
            others = set().union(*(all_words[o] for o in results if o != s))
            unique_titles = [f.title for f in results[s] if not (all_words[s] & others)]
            if unique_titles:
                out[s] = unique_titles[:5]
        return out

    def _map_to_issues(self, topic: str, findings: list[Finding]) -> list[dict]:
        """Heuristic mapping of findings to NEXUS second-brain issue areas."""
        issue_keywords = {
            "duplicate_process": ["supervisor", "process", "zombie", "daemon"],
            "unified_routing": ["routing", "router", "cluster", "ISO", "FAISS", "embedding"],
            "efc_feedback": ["EFC", "feedback", "Thompson", "reward", "outcome"],
            "autonomous_calling": ["tool calling", "autonomous", "agent", "LangGraph", "function call"],
            "cold_start": ["cold-start", "exploration", "UCB", "bandit", "exploration bonus"],
            "kg_density": ["knowledge graph", "edge", "Louvain", "community", "dense"],
            "tui_ide": ["TUI", "IDE", "tmux", "Claude Code", "terminal"],
        }
        mappings: list[dict] = []
        t = topic.lower()
        for issue, kws in issue_keywords.items():
            hits = [k for k in kws if k.lower() in t]
            if hits:
                mappings.append({"issue": issue, "matched_keywords": hits})
        return mappings


if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) or "tool routing for LLM agents"
    rp = ResearchPipeline()
    report = rp.research(topic, sources=["local"])  # local-only by default; add arxiv/github when online
    print(json.dumps({k: v for k, v in report.items() if k != "findings"}, indent=2))
    print(f"\n{len(report['findings'])} findings:")
    for f in report["findings"][:10]:
        print(f"  [{f['source']}] {f['title'][:80]}")