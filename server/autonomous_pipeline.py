"""P4: Autonomous tool calling pipeline.

LangGraph-style middleware for autonomous tool selection + validation.

Pipeline:
    before_model: route query -> retrieve tool set -> inject into model context
    [LLM generates response + tool calls]
    after_model:  parse tool calls -> validate each via HallucinationGate ->
                  reject any tool_id not in the active set (hallucination gate)
    [tool execution]
    reflexion:    optional verbal reflection -> implicit outcome logging

Based on: arXiv 2604.21816 (Tool Attention) + arXiv 2303.11366 (Reflexion).

Design:
  - Retrieval uses the FTS5 fast path (router_v2/fts5.py) so the 36s embedder
    import is NOT triggered on the autonomous path. Dense reranking is opt-in.
  - HallucinationGate (server.hallucination.py) is reused as-is for validation.
  - reflexion is pluggable: pass an LLM callback to enable implicit outcomes;
    default returns 'routed' (no LLM call) so the pipeline works offline.
  - The active tool set is the ground truth for the hallucination gate: any
    tool call whose name is not in the set is rejected with alternatives.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

_SELF = Path(__file__).resolve().parent
if str(_SELF) not in sys.path:
    sys.path.insert(0, str(_SELF))

from router_v2.fts5 import FTS5Search  # noqa: E402  (router_v2/fts5.py)
from hallucination import HallucinationGate  # noqa: E402

DB_PATH = _SELF.parent / "db" / "nexus.db"
ISO_THRESHOLD = 0.28  # arXiv 2604.21816 theta gating
DEFAULT_TOP_K = 10


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    raw: str = ""

    @property
    def tool_id(self) -> str:
        return self.name


@dataclass
class BeforeModelResult:
    active_tool_ids: list[str]
    tool_schemas: dict[str, dict]
    tools_text: str
    candidates: list[dict]


@dataclass
class AfterModelResult:
    validated_calls: list[ToolCall]
    rejected_calls: list[dict]
    hallucination_detected: bool


# Reflexion LLM callback signature: (prompt) -> str
ReflexionLLM = Callable[[str], str]

# Perplexity callback signature: (task, tool_name, tool_result) -> float|None
# Returns the model's perplexity on the task given the tool result, or None
# if unavailable. Used by perplexity_outcome to infer an implicit verdict:
# low perplexity (model confident) -> 'correct', high -> 'wrong'. When no
# callback is wired, perplexity_outcome returns 'routed' (no fake verdict) —
# the honest default. This mirrors the reflexion pluggable pattern: no LLM is
# ever silently faked; a real scorer must be injected.
PerplexityScorer = Callable[[str, str, Any], Optional[float]]

# Default thresholds for mapping perplexity -> outcome. Tunable so callers can
# set per-task thresholds without editing this module.
PERPLEXITY_LOW = 3.0   # <= this -> 'correct' (model was confident)
PERPLEXITY_HIGH = 10.0  # >= this -> 'wrong'  (model was uncertain)


class AutonomousToolCaller:
    """Middleware for autonomous tool calling with a hallucination gate."""

    def __init__(self, db_path: str | Path = DB_PATH, top_k: int = DEFAULT_TOP_K):
        self.db_path = str(db_path)
        self.fts = FTS5Search(self.db_path)
        self.gate = HallucinationGate(self.db_path)
        self.top_k = top_k
        self._reflexion_llm: ReflexionLLM | None = None
        self._perplexity_scorer: PerplexityScorer | None = None

    def set_reflexion_llm(self, llm: ReflexionLLM) -> None:
        """Inject an LLM callback for reflexion-based implicit outcomes."""
        self._reflexion_llm = llm

    def set_perplexity_scorer(self, scorer: PerplexityScorer) -> None:
        """Inject a perplexity scorer for perplexity-based implicit outcomes.

        Without a scorer, :meth:`perplexity_outcome` returns ``'routed'``
        (no fake verdict). The scorer must return a non-negative float or
        None; None means "could not score" and also falls back to 'routed'.
        """
        self._perplexity_scorer = scorer

    # ── before_model ───────────────────────────────────────────────────
    def before_model(self, query: str, agent_state: dict | None = None) -> BeforeModelResult:
        """Route query to a tool set and build context for the model.

        Retrieval is a two-stage pass:
          1. Resource-level keyword match on name+description (robust, returns
             real tool names; chunk-level FTS hits have NULL resource_id).
          2. If stage 1 is empty, fall back to chunk-level FTS hydrated search.
        Both stages score by relevance (keyword hit count + trigger history).
        """
        scored = self._retrieve_resources(query)
        if not scored:
            # fallback: chunk-level FTS (names may be None — last resort)
            hits = self.fts.search_hydrated(query, limit=self.top_k * 3)
            if hits:
                min_s = min(h["score"] for h in hits)
                max_s = max(h["score"] for h in hits)
                span = (max_s - min_s) or 1.0
                for h in hits:
                    rel = 1.0 - (h["score"] - min_s) / span
                    if rel < ISO_THRESHOLD:
                        continue
                    name = h.get("name") or f"chunk:{h.get('chunk_id')}"
                    scored.append({"name": name, "resource_id": h.get("resource_id"),
                                   "resource_type": h.get("resource_type"),
                                   "relevance": rel, "heading": h.get("heading", "")})

        scored.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        scored = scored[: self.top_k]

        active_ids: list[str] = []
        schemas: dict[str, dict] = {}
        lines: list[str] = []
        for h in scored:
            name = h.get("name") or f"resource#{h.get('resource_id')}"
            active_ids.append(name)
            schema = self._load_schema(h.get("resource_id"))
            if schema:
                schemas[name] = schema
            lines.append(f"- {name}: rel={h.get('relevance', 0):.3f} "
                         f"({h.get('resource_type', '?')}) {(h.get('heading') or h.get('description') or '')[:60]}")

        tools_text = "Available tools (NEXUS-routed):\n" + "\n".join(lines) if lines else ""
        return BeforeModelResult(
            active_tool_ids=active_ids,
            tool_schemas=schemas,
            tools_text=tools_text,
            candidates=scored,
        )

    def _retrieve_resources(self, query: str) -> list[dict]:
        """Resource-level keyword retrieval. Returns real tool names.

        Scores each resource by: 0.6 * keyword_hit_rate + 0.4 * trigger_popularity.
        Only active resources are returned (so the hallucination gate can verify).
        """
        # strip FTS-syntax chars + stopwords, keep meaningful tokens
        stop = {"the", "a", "an", "in", "on", "of", "to", "for", "and", "or",
                "is", "are", "with", "this", "that", "it", "by", "at", "be"}
        tokens = [t for t in re.split(r"\s+", re.sub(r"[*:^\"()\-+]", " ", query).lower())
                  if t and t not in stop and len(t) > 1]
        if not tokens:
            return []
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, name, resource_type, description, status, "
                "       times_triggered FROM resources WHERE status='active'"
            ).fetchall()
        finally:
            conn.close()
        scored: list[dict] = []
        max_trig = max((r["times_triggered"] or 0 for r in rows), default=1) or 1
        for r in rows:
            text = ((r["name"] or "") + " " + (r["description"] or "")).lower()
            hits = sum(1 for t in tokens if t in text)
            if hits == 0:
                continue
            hit_rate = hits / len(tokens)
            pop = (r["times_triggered"] or 0) / max_trig
            rel = 0.6 * hit_rate + 0.4 * pop
            scored.append({
                "name": r["name"], "resource_id": r["id"],
                "resource_type": r["resource_type"], "description": r["description"],
                "relevance": rel, "heading": "",
            })
        return scored

    # ── after_model ────────────────────────────────────────────────────
    def after_model(self, response: str, active_tool_ids: list[str],
                    task: str = "") -> AfterModelResult:
        """Parse + validate the model's tool calls against the active set."""
        calls = self._extract_tool_calls(response)
        validated: list[ToolCall] = []
        rejected: list[dict] = []
        active_set = set(active_tool_ids)
        for tc in calls:
            if tc.name in active_set:
                allowed, reason, alts = self.gate.verify(
                    tc.name, score=1.0, task=task,
                )
                if allowed:
                    validated.append(tc)
                else:
                    rejected.append({"call": tc.__dict__, "reason": reason,
                                     "alternatives": alts})
            else:
                # Not in active set -> hallucination. Try the gate for alternatives.
                _allowed, reason, alts = self.gate.verify(tc.name, score=0.0, task=task)
                rejected.append({
                    "call": tc.__dict__,
                    "reason": f"hallucinated tool '{tc.name}' not in active set",
                    "alternatives": alts,
                })
        return AfterModelResult(
            validated_calls=validated,
            rejected_calls=rejected,
            hallucination_detected=len(rejected) > 0,
        )

    # ── reflexion ──────────────────────────────────────────────────────
    def reflexion(self, tool_call: ToolCall, tool_result: Any, query: str) -> dict:
        """Post-execution reflection -> implicit outcome.

        Without an LLM callback, returns outcome='routed' (no implicit verdict).
        With an LLM, generates a verbal reflection and parses helpful/not.
        """
        if self._reflexion_llm is None:
            return {"outcome": "routed", "reflection": ""}
        prompt = (
            f"Task: {query}\n"
            f"Tool called: {tool_call.name}\n"
            f"Tool output: {str(tool_result)[:500]}\n\n"
            f"Reflect: Did this tool help complete the task?\n"
            f'Answer with JSON: {{"helpful": true/false, "reason": "..."}}'
        )
        try:
            raw = self._reflexion_llm(prompt)
            # extract first {...} block
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else {}
            outcome = "correct" if parsed.get("helpful") else "wrong"
            return {"outcome": outcome, "reflection": parsed.get("reason", "")}
        except Exception as e:
            return {"outcome": "routed", "reflection": f"reflexion_error: {e}"}

    # ── perplexity outcome (P8c) ───────────────────────────────────────
    def perplexity_outcome(self, tool_call: ToolCall, tool_result: Any,
                           query: str, *,
                           low: float = PERPLEXITY_LOW,
                           high: float = PERPLEXITY_HIGH) -> dict:
        """Implicit outcome from model perplexity — pluggable, honest stub.

        Idea (perplexity-as-feedback): if the model's perplexity on the task
        given the tool result is low, the tool helped (outcome 'correct'); if
        high, it didn't ('wrong'). This avoids the disabled
        ``_auto_close_routed`` heuristic, which marked stale routes 'correct'
        and inflated Thompson beliefs.

        HONEST DEFAULT: without a perplexity scorer wired via
        :meth:`set_perplexity_scorer`, returns ``outcome='routed'`` (the
        intermediate state, NOT a fake verdict). No LLM is ever silently faked.
        To enable, inject a scorer returning a non-negative float (or None);
        None also falls back to 'routed'.

        Args:
            tool_call/tool_result/query: the turn to score.
            low: perplexity <= this -> 'correct'.
            high: perplexity >= this -> 'wrong'. In-between -> 'partial'.

        Returns:
            ``{outcome, perplexity, source}`` where source is 'scorer',
            'none' (no scorer), 'null' (scorer returned None), or 'error'.
        """
        if self._perplexity_scorer is None:
            return {"outcome": "routed", "perplexity": None, "source": "none"}
        try:
            ppl = self._perplexity_scorer(query, tool_call.name, tool_result)
        except Exception as e:
            return {"outcome": "routed", "perplexity": None, "source": f"error: {e}"}
        if ppl is None:
            return {"outcome": "routed", "perplexity": None, "source": "null"}
        if ppl <= low:
            outcome = "correct"
        elif ppl >= high:
            outcome = "wrong"
        else:
            outcome = "partial"
        return {"outcome": outcome, "perplexity": float(ppl), "source": "scorer"}

    # ── helpers ────────────────────────────────────────────────────────
    def _load_schema(self, resource_id: int | None) -> dict | None:
        if not resource_id:
            return None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT name, resource_type, description, body_text FROM resources WHERE id=?",
                (resource_id,),
            ).fetchone()
            conn.close()
            if not row:
                return None
            return {
                "name": row["name"],
                "type": row["resource_type"],
                "description": (row["description"] or "")[:200],
                "has_body": bool(row["body_text"]),
            }
        except Exception:
            return None

    @staticmethod
    def _extract_tool_calls(response: str) -> list[ToolCall]:
        """Parse tool calls from a model response.

        Recognizes two patterns:
          1. JSON function-call blocks: {"name": "...", "arguments": {...}}
          2. Markdown fenced code blocks tagged tool_call / function
        """
        calls: list[ToolCall] = []
        # Pattern 1: bare JSON objects with a "name" field
        for m in re.finditer(r"\{[^{}]*\"name\"\s*:\s*\"[^\"]+\"[^{}]*\}", response, re.DOTALL):
            try:
                obj = json.loads(m.group(0))
                if "name" in obj:
                    calls.append(ToolCall(
                        name=str(obj["name"]),
                        arguments=obj.get("arguments", {}) or obj.get("parameters", {}) or {},
                        raw=m.group(0),
                    ))
            except json.JSONDecodeError:
                continue
        # Pattern 2: ```tool_call / ```function fenced blocks
        for m in re.finditer(
            r"```(?:tool_call|function|json)\s*\n(.*?)```", response, re.DOTALL
        ):
            block = m.group(1).strip()
            try:
                obj = json.loads(block)
                if isinstance(obj, dict) and "name" in obj:
                    calls.append(ToolCall(
                        name=str(obj["name"]),
                        arguments=obj.get("arguments", {}) or {},
                        raw=block,
                    ))
            except json.JSONDecodeError:
                continue
        # Dedupe by name preserving order
        seen: set[str] = set()
        out: list[ToolCall] = []
        for c in calls:
            if c.name not in seen:
                seen.add(c.name)
                out.append(c)
        return out


def _smoke() -> None:
    """Smoke test: route a query, validate a fake response with one valid + one hallucinated call."""
    caller = AutonomousToolCaller()
    before = caller.before_model("fix a login bug in the API")
    print(f"active tools: {len(before.active_tool_ids)}")
    print(f"tools_text:\n{before.tools_text[:400]}")
    # build a fake model response referencing the first active tool + a fake one
    valid_name = before.active_tool_ids[0] if before.active_tool_ids else "investigate"
    fake_response = (
        f'I will use the following tools.\n'
        f'```tool_call\n{{"name": "{valid_name}", "arguments": {{}}}}\n```\n'
        f'```tool_call\n{{"name": "totally_made_up_tool", "arguments": {{}}}}\n```\n'
    )
    after = caller.after_model(fake_response, before.active_tool_ids, task="fix a login bug")
    print(f"\nvalidated: {[c.name for c in after.validated_calls]}")
    print(f"rejected: {[r['call']['name'] for r in after.rejected_calls]}")
    print(f"hallucination_detected: {after.hallucination_detected}")
    # reflexion without LLM
    ref = caller.reflexion(ToolCall(name=valid_name), {"ok": True}, "fix a login bug")
    print(f"reflexion (no LLM): {ref}")


if __name__ == "__main__":
    _smoke()