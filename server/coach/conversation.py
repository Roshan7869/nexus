"""Phase 6 — Conversational Coaching Interface.

Provides a coaching persona that answers natural-language questions about
AI coding practice.  Attempts to use the NEXUS LLM gateway when available,
falling back to a structured template response.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from server.coach.bridge import get_bridge

logger = logging.getLogger(__name__)

# ── Coaching persona system prompt ────────────────────────────────────

COACH_SYSTEM_PROMPT = """You are an AI Engineering Coach. You analyze the user's AI coding
assistant usage data and provide actionable coaching advice.

Current practice scores:
{practice_scores_text}

Recent anti-patterns:
{antipatterns_text}

User question: {query}

Provide:
1. Direct answer to their question
2. Specific actions they can take (max 3)
3. Which NEXUS skills to use for improvement"""


def build_coaching_context(
    project: str = "default",
) -> dict[str, Any]:
    """Gather current coaching analytics for the persona prompt."""
    bridge = get_bridge()
    scores = bridge.get_practice_scores(project)
    patterns = bridge.get_antipatterns(project)

    score_text = "- No practice scores available yet."
    if scores:
        latest = scores[0]
        score_text = (
            f"- Prompt Quality: {latest.prompt_quality}/100\n"
            f"- Session Hygiene: {latest.session_hygiene}/100\n"
            f"- Code Review: {latest.code_review}/100\n"
            f"- Tool Mastery: {latest.tool_mastery}/100\n"
            f"- Context Management: {latest.context_management}/100\n"
            f"- Overall: {latest.total_score}/100"
        )

    antipattern_text = "- No anti-patterns detected."
    if patterns:
        antipattern_text = "\n".join(
            f"- [{p.severity}] {p.rule_name}: {p.description[:120]}"
            for p in patterns[:5]
        )

    return {
        "practice_scores_text": score_text,
        "antipatterns_text": antipattern_text,
        "scores": [{"date": s.date, "total_score": s.total_score} for s in scores[:10]],
        "antipatterns": [
            {"rule_id": p.rule_id, "severity": p.severity, "description": p.description[:120]}
            for p in patterns[:5]
        ],
    }


def generate_structured_response(
    query: str,
    context: dict[str, Any],
) -> str:
    """Generate a structured coaching response without an LLM call.

    Used as fallback when the Copilot API or NEXUS LLM gateway is
    unavailable.
    """
    scores = context.get("scores", [])
    patterns = context.get("antipatterns", [])

    response_parts: list[str] = ["## Coaching Response", f"Query: {query}\n"]

    # Current scores
    if scores:
        latest = scores[0]
        response_parts.append("### Current Practice Scores")
        response_parts.append(f"Overall: {latest['total_score']}/100")
        response_parts.append(f"Last updated: {latest['date']}")
        response_parts.append("")
    else:
        response_parts.append(
            "No practice data available. Use the Coach VS Code extension "
            "to start tracking your AI coding practices.\n"
        )

    # Recommendations
    recommendations: list[str] = []
    if scores:
        score_map = context.get("practice_scores_text", "")
        if "Prompt Quality" in score_map:
            for line in score_map.split("\n"):
                if ":" in line and "/100" in line:
                    parts = line.split(":")
                    if len(parts) == 2:
                        name = parts[0].strip("- ").strip()
                        try:
                            val = float(parts[1].strip().split("/")[0])
                            if val < 70:
                                recommendations.append(
                                    f"Improve **{name}** ({val}/100) — "
                                    f"practice targeted exercises via the "
                                    f"`coach-learning-path` skill."
                                )
                        except (ValueError, IndexError):
                            pass

    for ap in patterns[:3]:
        recommendations.append(
            f"Address **{ap['description'][:80]}** ({ap['severity']})"
        )

    if recommendations:
        response_parts.append("### Recommendations")
        for i, rec in enumerate(recommendations, 1):
            response_parts.append(f"{i}. {rec}")
        response_parts.append("")

    # Skill suggestions
    response_parts.append("### Relevant Skills")
    response_parts.append(
        "Try the following NEXUS coaching skills:\n"
        "- `coach-dashboard` — View practice score trends\n"
        "- `coach-learning-path` — Generate personalized exercises\n"
        "- `coach-session-analysis` — Deep-dive into session data\n"
    )

    return "\n".join(response_parts)


def coach_chat(
    query: str,
    context_hint: str = "",
    use_llm: bool = False,
) -> dict[str, Any]:
    """Answer a natural-language coaching question.

    Args:
        query: The user's coaching question.
        context_hint: Optional project name or session ID.
        use_llm: If True, attempt to use the NEXUS LLM gateway
                 (Ollama / NVIDIA NIM). Falls back to structured
                 response on failure.

    Returns:
        Dict with ``response``, ``recommendations``, ``mode``
        (``"llm"`` or ``"structured"``), and ``context``.
    """
    ctx = build_coaching_context(project=context_hint or "default")

    if use_llm:
        try:
            llm_response = _call_llm_gateway(query, ctx)
            if llm_response:
                return {
                    "response": llm_response,
                    "recommendations": _extract_recommendations(llm_response),
                    "mode": "llm",
                    "context": ctx,
                }
        except Exception as exc:
            logger.warning("LLM gateway call failed, using structured fallback: %s", exc)

    response_text = generate_structured_response(query, ctx)

    recommendations = []
    for line in response_text.split("\n"):
        if line.strip().startswith(("- ", "1. ", "2. ", "3. ")):
            recommendations.append(line.strip())

    return {
        "response": response_text,
        "recommendations": recommendations[:5],
        "mode": "structured",
        "context": ctx,
    }


# ---------------------------------------------------------------------------
# LLM gateway integration (optional)
# ---------------------------------------------------------------------------


def _call_llm_gateway(
    query: str,
    context: dict[str, Any],
    endpoint: str = "http://localhost:8000/api/llm/chat",
    timeout: int = 15,
) -> str | None:
    """Call the NEXUS unified server's chat completions endpoint.

    Falls back gracefully if the server is not running or unreachable.
    """
    prompt = COACH_SYSTEM_PROMPT.format(
        practice_scores_text=context.get("practice_scores_text", "N/A"),
        antipatterns_text=context.get("antipatterns_text", "N/A"),
        query=query,
    )

    try:
        import urllib.request

        body = json.dumps(
            {
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query},
                ],
                "temperature": 0.7,
                "max_tokens": 1024,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        logger.debug("LLM gateway call failed: %s", exc)
        return None


def _extract_recommendations(text: str) -> list[str]:
    """Extract recommendation lines from an LLM response."""
    lines = []
    in_recs = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("2."):
            in_recs = True
        if in_recs and stripped and (stripped[0].isdigit() or stripped.startswith("- ")):
            lines.append(stripped)
        if in_recs and stripped.startswith("3."):
            lines.append(stripped)
            break
    return lines[:5]
