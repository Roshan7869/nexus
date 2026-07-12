"""Phase 9 — Multi-source research synthesis service.

Wraps ``research_pipeline.ResearchPipeline`` with site-registry project
context, optional ingestion into NEXUS resources, and project_contexts persistence.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from server.research_pipeline import ResearchPipeline, SOURCES

NEXUS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = NEXUS_ROOT / "db" / "nexus.db"

# Gate: ≥2 sources return findings (second-brain P9)
MIN_SOURCES_GATE = 2

NEXUS_GAP_KEYWORDS: dict[str, list[str]] = {
    "I-04_kg_collapse": ["knowledge graph", "edge", "louvain", "community", "dense", "zero-edge"],
    "I-05_router_v5": ["routing", "router", "cluster", "unified", "rerank", "HGMF"],
    "I-10_ops_dashboard": ["supervisor", "dashboard", "process", "daemon", "health"],
    "efc_feedback": ["EFC", "feedback", "thompson", "outcome", "reward"],
    "autonomous_calling": ["tool calling", "autonomous", "agent", "function call"],
    "cold_start": ["cold-start", "UCB", "bandit", "exploration"],
    "harness_metrics": ["harness", "FTSR", "MRC", "TLT", "CDR"],
}


def list_research_sources() -> list[dict[str, Any]]:
    """Available pipeline sources + site-registry publishers/projects."""
    from server.site_registry import list_publishers, list_projects

    pipeline = [
        {"id": name, "type": "pipeline", "description": cls.__name__}
        for name, cls in SOURCES.items()
    ]
    publishers = [{"id": p["id"], "type": "publisher", "channel": p.get("channel")}
                  for p in list_publishers()]
    projects = [{"id": p["id"], "type": "project", "site_count": p.get("site_count")}
                for p in list_projects()]
    return pipeline + publishers + projects


def get_research_status() -> dict[str, Any]:
    sources = list(SOURCES.keys())
    from server.site_registry import list_publishers, list_projects
    return {
        "phase": 9,
        "pipeline_sources": sources,
        "publisher_count": len(list_publishers()),
        "project_count": len(list_projects()),
        "min_sources_gate": MIN_SOURCES_GATE,
        "ingest_available": True,
    }


def _project_context(project: str) -> dict[str, Any]:
    from server.site_registry import get_project_spec, get_project_urls, match_publishers

    spec = get_project_spec(project)
    urls = get_project_urls(project)
    keywords = spec.get("keywords") or []
    return {
        "project": project,
        "label": spec.get("label", project),
        "urls": urls[:10],
        "keywords": keywords,
        "matched_publishers": match_publishers(" ".join(keywords)),
    }


def _finding_to_paper(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": finding.get("title") or "Untitled",
        "abstract": finding.get("summary") or "",
        "url": finding.get("url") or "",
        "source": finding.get("source") or "research",
    }


def _map_gaps(topic: str, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blob = topic.lower() + " " + " ".join(
        (f.get("title") or "") + " " + (f.get("summary") or "")
        for f in findings
    ).lower()
    mappings: list[dict[str, Any]] = []
    for gap_id, keywords in NEXUS_GAP_KEYWORDS.items():
        hits = [k for k in keywords if k.lower() in blob]
        if hits:
            mappings.append({"gap": gap_id, "matched_keywords": hits, "confidence": len(hits) / len(keywords)})
    return sorted(mappings, key=lambda x: -x["confidence"])


def _persist_summary(
    project: str,
    topic: str,
    report: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        summary = {
            "topic": topic,
            "total_findings": report.get("total_findings"),
            "per_source": report.get("per_source"),
            "gap_mapping": report.get("nexus_gap_mapping"),
            "top_titles": [f.get("title") for f in (report.get("findings") or [])[:8]],
        }
        conn.execute(
            """
            INSERT INTO project_contexts (project, context_type, title, content, tags_json)
            VALUES (?, 'research', ?, ?, ?)
            """,
            (
                project,
                f"Research: {topic[:120]}",
                json.dumps(summary, indent=2),
                json.dumps(["phase9", "research_synthesis"]),
            ),
        )
        conn.commit()
        return {"persisted": True, "project": project}
    except sqlite3.OperationalError as exc:
        return {"persisted": False, "error": str(exc)}
    finally:
        if own:
            conn.close()


def run_research_synthesis(
    topic: str,
    *,
    sources: list[str] | None = None,
    project: str = "default",
    ingest_top: int = 0,
    persist: bool = False,
) -> dict[str, Any]:
    """Run multi-source research and optionally ingest/persist results."""
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    active_sources = sources or list(SOURCES.keys())
    pipeline = ResearchPipeline()
    report = pipeline.research(topic, sources=active_sources)
    report["project_context"] = _project_context(project)
    report["nexus_gap_mapping"] = _map_gaps(topic, report.get("findings") or [])
    report["nexus_issue_mapping"] = (
        report.get("nexus_issue_mapping", []) + report["nexus_gap_mapping"]
    )

    sources_with_hits = sum(1 for v in (report.get("per_source") or {}).values() if v > 0)
    report["gate_pass"] = sources_with_hits >= MIN_SOURCES_GATE
    report["sources_with_findings"] = sources_with_hits

    ingest_result = None
    if ingest_top > 0:
        from server.research_ingest import ingest_papers
        papers = [_finding_to_paper(f) for f in (report.get("findings") or [])[:ingest_top]]
        ingest_result = ingest_papers(papers, project=project)

    persist_result = None
    if persist:
        persist_result = _persist_summary(project, topic, report)

    report.update({
        "phase": 9,
        "project": project,
        "started_at": started,
        "ingest": ingest_result,
        "persist": persist_result,
    })
    return report