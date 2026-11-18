"""Context Harnessing Engine — unified orchestrator.

Routes queries to best backend(s), fuses results, stores context.
Backends: NEXUS (resource DB), Internet (agent-reach channels), Memory (gcode).
TOIN integration bridges tool outputs → cache → LLM context window.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from server.context_harness.models import HarnessQuery, HarnessResult, HarnessResponse
from server.context_harness.nexus_bridge import search_resources, search_learnings
from server.context_hygiene import provenance_stamp
from server.context_harness.agent_reach import doctor, route_to_channel
from server.context_harness.gcode_backend import search as memory_search, store as memory_store
from server.context_harness.fusion import fuse
from server.context_harness.toin import TOIN, OutputRouter, FormattedEntry, ToolOutputSource, ToolOutput
from server.context_harness.content_router import ContentRouter, format_context_window, ContentRequest
from server.context_harness.router import HubAndSpokeRouter, get_router
from server.context_harness.cache import get_l0_cache, get_l1_cache, get_l2_cache, get_l3_cache


class ContextHarnessEngine:
    """Orchestrate context retrieval across all backends.

    Integrates TOIN (Tool Output Integration Node) for ingesting tool
    outputs, routing them through the cache hierarchy, and formatting
    context windows for LLM consumption.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._toin = TOIN()
        self._output_router = OutputRouter()
        self._router: HubAndSpokeRouter = get_router(db_path)
        self._content_router = ContentRouter(db_path)
        self._l0 = get_l0_cache()
        self._l1 = get_l1_cache()
        self._l2 = get_l2_cache()
        self._l3 = get_l3_cache()

    # ── TOIN Integration ─────────────────────────────────────────────────────

    def ingest_tool_output(
        self,
        source_type: str,
        source_name: str,
        raw_text: str,
        structured: dict | list | None = None,
        mime_type: str = "text/plain",
    ) -> str:
        """Ingest a raw tool output and return the cached entry ID."""
        import time as _time

        source = ToolOutputSource(
            source_type=source_type,
            source_name=source_name,
            invocation_id=str(uuid.uuid4()),
            started_at=_time.time(),
            duration_ms=0.0,
        )
        entry = self._toin.ingest(
            source=source,
            raw_text=raw_text,
            structured=structured,
            mime_type=mime_type,
        )
        if self._output_router.should_cache(entry):
            level = self._output_router.route_to_level(entry)
            if level == "L2":
                self._l2.set(source_type, entry.entry_id, {
                    "entry_id": entry.entry_id,
                    "body_text": entry.normalized_text,
                })
            self._l3.set(entry.entry_id, [{
                "entry_id": entry.entry_id,
                "score": entry.priority / 100.0,
            }])
        return entry.entry_id

    def ingest_mcp_output(self, mcp_server: str, tool_name: str, result: dict) -> str:
        """Convenience: ingest an MCP tool result as a tool output.

        Sets ``source_type='mcp'``, serialises *result* as JSON,
        and delegates to ``ingest_tool_output``.
        """
        import json as _json

        return self.ingest_tool_output(
            source_type="mcp",
            source_name=f"{mcp_server}.{tool_name}",
            raw_text=_json.dumps(result, indent=2, default=str, ensure_ascii=False),
            structured=result,
            mime_type="application/json",
        )

    def get_cached_output(self, entry_id: str) -> FormattedEntry | None:
        """Retrieve a cached ``FormattedEntry`` by entry ID.

        Searches L3 query cache first (fastest), then falls back to
        L2 debug cache entries.
        """
        cached = self._l3.get(entry_id)
        if cached and len(cached) > 0:
            meta = cached[0]
            if "entry_id" in meta:
                return FormattedEntry(
                    entry_id=meta["entry_id"],
                    tool_output=ToolOutput(
                        source=ToolOutputSource(
                            source_type="cached",
                            source_name="cache_hit",
                            invocation_id="",
                            started_at=0.0,
                            duration_ms=0.0,
                        ),
                        raw_text="",
                        structured=None,
                        mime_type="text/plain",
                        token_count=0,
                        status="success",
                        error_message=None,
                    ),
                    normalized_text="",
                    normalized_tokens=0,
                    compression_savings=0,
                    section_title="cached entry",
                    priority=int(meta.get("score", 0.5) * 100),
                    tags=[],
                )
        return None

    def batch_ingest(self, outputs: list[dict]) -> list[str]:
        """Batch-ingest multiple tool outputs.

        Each dict in *outputs* must have the keys:
        ``source_type``, ``source_name``, ``raw_text``.
        Optional keys: ``structured``, ``mime_type``.

        Returns a list of entry IDs in the same order.
        """
        ids: list[str] = []
        for o in outputs:
            eid = self.ingest_tool_output(
                source_type=o["source_type"],
                source_name=o["source_name"],
                raw_text=o["raw_text"],
                structured=o.get("structured"),
                mime_type=o.get("mime_type", "text/plain"),
            )
            ids.append(eid)
        return ids

    def format_context(
        self,
        resource_ids: list[int],
        query: str,
        max_tokens: int = 8192,
    ) -> str:
        """End-to-end pipeline: route → content router → formatted context window.

        1. Route *resource_ids* through ``HubAndSpokeRouter`` (cache-aware).
        2. Load content via ``ContentRouter`` with compression.
        3. Format result as an LLM-friendly string via ``format_context_window``.
        """
        from server.context_harness.router import RouteContext

        ctx = RouteContext(query_text=query, top_k=len(resource_ids))
        route_result = self._router.route(ctx)
        ids_to_load = route_result.resource_ids or resource_ids

        request = ContentRequest(
            resource_ids=ids_to_load[:max_tokens],
            query=query,
            project_type=route_result.project_type,
            max_tokens=max_tokens,
            compress=True,
            include_snippets=False,
        )
        content_window = self._content_router.load_content(request)
        return format_context_window(content_window.windows, format="llm")

    def get_engine_stats(self) -> dict[str, Any]:
        """Aggregate statistics from all subsystems.

        Returns dict with keys:
        - router_cache_hit_rates — per-level hit rates from HubAndSpokeRouter
        - content_router — latency p95, sections processed, avg compression
        - toin — (placeholder for future TOIN metrics)
        """
        return {
            "router_cache_hit_rates": self._router.cache_hit_rate(),
            "content_router": self._content_router.get_stats(),
            "toin": {"entries_ingested": 0},
        }

    def harness(self, query: HarnessQuery) -> HarnessResponse:
        q = query.query
        sources = query.sources.lower().split(",")
        max_results = query.max_results
        source_used = []
        nexus_results = []
        internet_results = []
        memory_results = []
        import time as _t
        _started = _t.time()

        # 1. NEXUS resource search
        if "all" in sources or "nexus" in sources:
            resources = search_resources(q, max_results)
            for r in resources:
                stamp = provenance_stamp(
                    r.get("repo_name"), r.get("absorbed_at_commit"),
                    r.get("absorbed_at_date"))
                base = f"[{r['resource_type']}] {r['name']}: {r['description']}"
                content = f"{base} ({stamp})" if stamp else base
                nexus_results.append(HarnessResult(
                    source="nexus", content=content,
                    score=r["times_correct"] / max(r["times_triggered"], 1),
                    metadata={"id": r["id"], "cluster": r["cluster"], "type": r["resource_type"]},
                ))
            learnings = search_learnings(q, max_results)
            for n in learnings:
                memory_results.append(HarnessResult(
                    source="learning-note", content=n["note"],
                    score=0.5, metadata={"id": n["id"]},
                ))
            if resources or learnings:
                source_used.append("nexus")

        # 2. Internet platform routing (agent-reach)
        if "all" in sources or "internet" in sources:
            ch = route_to_channel(q)
            if ch:
                status = "available" if ch["probe"]() else "unavailable"
                internet_results.append(HarnessResult(
                    source="agent-reach", content=f"Best platform: {ch['name']} ({ch['description']})",
                    score=1.0 if status == "available" else 0.3,
                    metadata={"platform": ch["name"], "tier": ch["tier"], "status": status},
                ))
            platforms = doctor()
            for name, info in platforms.items():
                if info["status"] == "ok" and name in q.lower():
                    internet_results.append(HarnessResult(
                        source="agent-reach", content=f"{info['name']} — active via {info['active_backend']}",
                        score=0.8,
                        metadata={"platform": name, "backend": info["active_backend"]},
                    ))
            if internet_results:
                source_used.append("internet")

        # 3. Memory search (gcode / learning notes)
        if "all" in sources or "memory" in sources:
            mems = memory_search(q, max_results)
            for m in mems:
                memory_results.append(HarnessResult(
                    source=m.get("source", "memory"),
                    content=m.get("content", ""),
                    score=m.get("score", 0.5),
                    metadata=m.get("metadata", {}),
                ))
            if mems:
                source_used.append("memory")

        fused = fuse(nexus_results, internet_results, memory_results, max_results)
        _elapsed_ms = (_t.time() - _started) * 1000.0
        self._record_harness_outcome(q, source_used, fused, _elapsed_ms)
        return HarnessResponse(
            query=q,
            results=fused,
            source_used=sorted(set(source_used)),
            total_count=len(fused),
        )

    def _record_harness_outcome(
        self,
        query: str,
        sources_used: list[str],
        fused_results: list,
        elapsed_ms: float,
        *,
        project: str = "context-harness",
        channel_plan: dict | None = None,
    ) -> None:
        """Bridge harness routing decisions into NEXUS learning loop.

        Calls record_outcome() with a `harness:<source>` skill_name so
        Thompson Sampling and cluster affinity learning pick up these
        decisions. Closes GAP-I1/GAP-A4 (harness was a learning black hole).
        """
        try:
            from server.learning import record_outcome
        except Exception:
            return  # Don't break the harness if learning is unavailable

        if not fused_results:
            return  # No signal from a no-result query

        try:
            source_label = "+".join(sources_used) if sources_used else "unknown"
            skill_name = f"harness:{source_label}"
            outcome = "correct"
            summary = f"harness query: {query[:200]}"
            if channel_plan:
                import json as _json
                summary += f" | plan: {_json.dumps(channel_plan, default=str)[:200]}"
            record_outcome(
                skill_name=skill_name,
                outcome=outcome,
                task_summary=summary,
                project=project,
                ide="claude-code",
                tokens_used=0,
                quality=0.8 if outcome == "correct" else 0.3,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"harness outcome bridge failed: {e}")

    def search(self, query: str, source: str = "all", max_results: int = 10) -> list[HarnessResult]:
        q = HarnessQuery(query=query, sources=source, max_results=max_results)
        resp = self.harness(q)
        return resp.results

    def store(
        self,
        key: str,
        value: str,
        source: str = "memory",
        tags: list[str] | None = None,
        *,
        project: str = "default",
        task_type: str = "general",
    ) -> dict:
        scoped_tags = list(tags or [])
        scoped_tags.extend([f"project:{project}", f"task_type:{task_type}"])
        if source == "learning":
            from server.context_harness.nexus_bridge import store_learning
            rid = store_learning(key, value, scoped_tags, project=project, task_type=task_type)
            return {"status": "stored", "backend": "learning", "id": rid, "project": project}
        result = memory_store(key, value, scoped_tags)
        result["project"] = project
        result["task_type"] = task_type
        try:
            self._l3.set(key, [{"content": value[:500], "project": project, "task_type": task_type}],
                         project=project, task_type=task_type)
        except TypeError:
            self._l3.set(key, [{"content": value[:500]}])
        return result
