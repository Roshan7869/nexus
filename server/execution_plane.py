"""P4 Execution plane — stable session contract for AgentRouter / Hermes / CodeNomad.

Contract (version 1):
  POST /v1/session/open   {task, project, budget, ide}
    → {session_id, tools[], harness_channels[], harness_tools[], model_hint, ...}
  POST /v1/session/{id}/report  {outcome, quality?, notes?}
    → {status, outcome, belief_delta, ...}
  GET  /v1/health
    → {version, git_sha, router_mode, db_ok, flags}

This module is the single implementation; HTTP + MCP + CLI clients wrap it.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NEXUS_ROOT = Path(__file__).resolve().parent.parent
API_VERSION = "1"
CONTRACT = "nexus.execution_plane.v1"


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(NEXUS_ROOT),
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _sanitize_ide(ide: str | None) -> str:
    allowed = {
        "opencode", "claude-code", "unknown", "hermes", "codex",
        "mcp", "auto_invoke", "http",
    }
    raw = (ide or "http").strip().lower()
    aliases = {
        "claude": "claude-code",
        "claude_code": "claude-code",
        "agent-space": "http",
        "agentspace": "http",
        "codenomad": "opencode",
        "open-code": "opencode",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in allowed else "unknown"


def _belief_snapshot(skill_names: list[str]) -> dict[str, dict[str, float]]:
    """Aggregated alpha/beta for resource-level Thompson beliefs (provider=nexus).

    Sums across task_type rows so deltas are visible regardless of which
    task_type the latest update hit.
    """
    out: dict[str, dict[str, float]] = {}
    if not skill_names:
        return out
    try:
        from server.db import db_conn
    except Exception:
        return out
    try:
        with db_conn() as conn:
            for name in skill_names[:12]:
                key = f"resource:{name}"
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(alpha),0), COALESCE(SUM(beta),0),
                           COALESCE(SUM(total_requests),0)
                    FROM model_beliefs
                    WHERE provider='nexus' AND model=?
                    """,
                    (key,),
                ).fetchone()
                if row and (row[0] or row[1] or row[2]):
                    out[name] = {
                        "alpha": float(row[0] or 0),
                        "beta": float(row[1] or 0),
                        "total_requests": float(row[2] or 0),
                    }
                else:
                    out[name] = {"alpha": 0.0, "beta": 0.0, "total_requests": 0.0}
    except Exception as e:
        logger.debug("belief snapshot failed: %s", e)
    return out


def _belief_delta(
    before: dict[str, dict[str, float]],
    after: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    deltas = []
    keys = set(before) | set(after)
    for k in keys:
        b = before.get(k, {})
        a = after.get(k, {})
        if not a and not b:
            continue
        da = (a.get("alpha", 1.0) - b.get("alpha", 1.0))
        db = (a.get("beta", 1.0) - b.get("beta", 1.0))
        if abs(da) > 1e-9 or abs(db) > 1e-9:
            deltas.append({
                "skill": k,
                "alpha_before": b.get("alpha"),
                "alpha_after": a.get("alpha"),
                "beta_before": b.get("beta"),
                "beta_after": a.get("beta"),
                "delta_alpha": round(da, 4),
                "delta_beta": round(db, 4),
            })
    return deltas


# ── A07: session artifact manifest (evidence-in-context) ──────────────
# The manifest (paths + hashes) is the Cordis test-word list for a session:
# same manifest ⇒ same evidence. Backfill-safe: missing dir → None / [].
ARTIFACTS_ROOT = Path(
    os.environ.get("NEXUS_ARTIFACTS_DIR", str(NEXUS_ROOT / "artifacts"))
)


def artifact_dir(session_id: str) -> Path:
    d = ARTIFACTS_ROOT / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def artifact_manifest(session_id: str) -> tuple[str | None, list[dict[str, Any]]]:
    d = ARTIFACTS_ROOT / session_id
    if not d.is_dir():
        return None, []
    import hashlib

    items = []
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        items.append({"path": str(f), "sha256": digest, "bytes": f.stat().st_size})
    return str(d), items


def health_v1() -> dict[str, Any]:
    """GET /v1/health payload."""
    from server.router_cutover import get_router_status

    db_ok = True
    try:
        from server.db import db_conn
        with db_conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False

    rstatus = get_router_status()
    return {
        "status": "ok" if db_ok else "degraded",
        "version": API_VERSION,
        "contract": CONTRACT,
        "git_sha": _git_sha(),
        "router_mode": rstatus.get("mode"),
        "router_flag": rstatus.get("use_unified_router"),
        "db_ok": db_ok,
        "flags": {
            "USE_UNIFIED_ROUTER": os.environ.get("USE_UNIFIED_ROUTER", rstatus.get("default_flag")),
            "NEXUS_CLUSTER_ROUTING": os.environ.get("NEXUS_CLUSTER_ROUTING"),
            "NEXUS_THOMPSON_ROUTING": os.environ.get("NEXUS_THOMPSON_ROUTING"),
            "NEXUS_REFLECTION": os.environ.get("NEXUS_REFLECTION"),
            "NEXUS_HARNESS_EAGER": os.environ.get("NEXUS_HARNESS_EAGER", "false"),
        },
        "endpoints": {
            "session_open": "POST /v1/session/open",
            "session_report": "POST /v1/session/{session_id}/report",
            "session_get": "GET /v1/session/{session_id}",
            "health": "GET /v1/health",
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def open_session(
    task: str,
    project: str = "default",
    *,
    budget: int = 8,
    ide: str = "http",
    use_embedding: bool = True,
    ide_session_id: str | None = None,
    local_session=None,  # M6: prebuilt LocalHarnessSession; None = legacy path
) -> dict[str, Any]:
    """Open an execution session: route + load tools + harness plan."""
    task = (task or "").strip()
    if not task:
        raise ValueError("task is required")
    project = (project or "default").strip()
    ide = _sanitize_ide(ide)
    budget = max(1, min(int(budget or 8), 12))

    # 1) Production cutover route (pure session log outcome=routed)
    from server.router_cutover import route_for_task
    route = route_for_task(
        task, project, max_results=budget,
        open_session=True, ide=ide,
    )

    # 2) Ephemeral tool session (learning on report only)
    from server.tool_session_broker import get_tool_session_broker
    broker = get_tool_session_broker()
    if local_session is None:
        # Prefer tools from route skills names
        session = broker.start_session(
            project=project,
            query=task,
            ide=ide,
            use_embedding=use_embedding,
            max_tools=budget,
            ide_session_id=ide_session_id,
        )
    else:
        # M6: prebuilt LocalHarnessSession (already started + effect-tracked)
        session = local_session.inner

    # Prefer cutover ranking order when present
    skill_names = [
        s.get("name") for s in (route.get("skills") or [])
        if isinstance(s, dict) and s.get("name")
    ]
    if skill_names:
        session.tools_loaded = skill_names[:budget]
        # Do not persist here: _update_persisted_session stamps ended_at, which
        # marks an open session as closed (FIX-DESIGN S1-d). Only close sets it.
        try:
            broker._active[session.session_id] = session
        except Exception:
            pass

    harness_tools = route.get("harness_tools") or session.harness_tools
    if route.get("harness_tools"):
        session.harness_tools = list(route["harness_tools"])[:16]

    payload = {
        "contract": CONTRACT,
        "version": API_VERSION,
        "session_id": session.session_id,
        "project": project,
        "task": task,
        "ide": ide,
        "status": "active",
        "tools": session.tools_loaded,
        "skills": route.get("skills") or [],
        "budget": budget,
        "model_hint": route.get("model") or session.model,
        "cluster": route.get("cluster") or session.cluster,
        "task_type": route.get("task_type") or session.task_type,
        "harness_channels": route.get("harness_channels") or [],
        "harness_tools": harness_tools,
        "toolsets": route.get("toolsets") or [],
        "harness_intent": route.get("harness_intent"),
        "harness_plan": route.get("harness_plan"),
        "research_artifacts": route.get("research_artifacts") or [],
        "context_block": session.context_block,
        "router_mode": route.get("router_mode"),
        "router_flag": route.get("router_flag"),
        "routing_log_id": route.get("session_log_id"),
        "mcp_proxy": broker.get_mcp_proxy_config(session.session_id),
        "instructions": (
            "Use only tools listed in this session. "
            "When finished call POST /v1/session/{session_id}/report "
            "with outcome in {correct,wrong,partial,skip}."
        ),
    }
    return payload


def _record_credit_pair(session, *, dispreferred: str, preferred: str) -> None:
    """Persist one contrastive pair: rank-1 was wrong, `preferred` was right.

    Written to the existing `reflection_fixes` table (no migration): `resource_id`
    is the dispreferred skill, `alternative_resource_id` the preferred one. This is
    the RLCD *data shape* only; nothing trains from it until the pair gate opens.
    """
    import sqlite3

    from server.db import get_db_path

    con = sqlite3.connect(str(get_db_path()), timeout=30)
    try:
        con.row_factory = sqlite3.Row
        ids: dict[str, int] = {}
        for name in (dispreferred, preferred):
            row = con.execute("SELECT id FROM resources WHERE name=?", (name,)).fetchone()
            if row is not None:
                ids[name] = int(row["id"])
        if len(ids) < 2 or ids[dispreferred] == ids[preferred]:
            return
        con.execute(
            """INSERT INTO reflection_fixes
                   (resource_id, alternative_resource_id, failure_summary,
                    fix_description, times_applied, times_successful)
               VALUES (?,?,?,?,0,0)""",
            (ids[dispreferred], ids[preferred],
             (getattr(session, "query", "") or "")[:200],
             f"explicit credit disagreed with rank-1 ({dispreferred} -> {preferred})"),
        )
        con.commit()
    finally:
        con.close()


def report_session(
    session_id: str,
    outcome: str,
    *,
    notes: str = "",
    quality: float = 1.0,
    skill_name: str | None = None,
    ide_session_id: str | None = None,
) -> dict[str, Any]:
    """Close session with terminal outcome and return belief deltas."""
    from server.tool_session_broker import get_tool_session_broker
    from server.learning import record_outcome, TERMINAL_OUTCOMES

    broker = get_tool_session_broker()
    session = broker.get_session(session_id)
    if session is None:
        raise KeyError(f"unknown session: {session_id}")

    o = (outcome or "").strip().lower()
    if o in {"success", "passed", "done", "complete", "completed"}:
        o = "correct"
    if o in {"failed", "fail", "error"}:
        o = "wrong"
    if o in {"mixed"}:
        o = "partial"
    if o not in TERMINAL_OUTCOMES and o != "skip":
        # allow skip as terminal no-op for learning
        if o not in {"skip"}:
            raise ValueError(
                f"outcome must be terminal: correct|wrong|partial|skip (got {outcome})"
            )

    tools = list(session.tools_loaded or [])
    if skill_name and skill_name not in tools:
        tools = [skill_name] + tools

    before = _belief_snapshot(tools)

    # P4 — RLCD contrastive-pair collection (data shape only).
    # When the caller names the skill that actually did the work and it disagrees
    # with rank #1, that disagreement IS a preference pair: (preferred=credited,
    # dispreferred=rank-1). Stored in the existing `reflection_fixes` table via
    # `alternative_resource_id`, so no schema change is needed.
    # No learning happens here — `server/router_v2/rlcd_ranker.py` enforces an
    # explicit >=100-pair gate before any distillation may run.
    if skill_name and tools and tools[0] != skill_name:
        try:
            _record_credit_pair(session, dispreferred=tools[0], preferred=skill_name)
        except Exception as exc:  # never let pair collection break a session close
            logger.debug("credit-pair not recorded: %s", exc)

    # Primary skill learning.
    #
    # CREDIT POLICY. Previously this was `primary = skill_name or tools[0]`, which
    # silently credited rank #1 whenever the caller omitted `skill_name`. Measured
    # consequence: `gsd-doc-writer` ("Writes and updates project documentation")
    # absorbed the reward for "write end-to-end tests", "debug a failing login
    # session" and "review a design document", driving its Alpha from 2.0 to 3.55 —
    # the loop was learning a bias, not discrimination.
    #
    # Default is now `strict`: credit only when the skill is unambiguous.
    #   - explicit skill_name                        -> credit it
    #   - exactly one learnable tool loaded          -> credit it
    #   - several tools and no explicit skill_name   -> credit NOTHING, record the
    #     outcome as `derived` telemetry so the row still exists for analysis.
    # Set NEXUS_CREDIT_POLICY=top1 to restore the old guessing behaviour.
    _credit_policy = (os.environ.get("NEXUS_CREDIT_POLICY") or "strict").strip().lower()
    primary: str | None = skill_name or None
    if primary is None:
        if len(tools) == 1:
            primary = tools[0]
        elif _credit_policy == "top1" and tools:
            primary = tools[0]

    learning_results = []
    learnable = frozenset({"correct", "wrong", "partial"})
    if primary is None and o in learnable:
        learning_results.append({
            "status": "skipped",
            "reason": f"ambiguous_primary_skill: {len(tools)} tools loaded, none credited",
            "credit_policy": _credit_policy,
            "tools": tools[:8],
        })

    if primary and o in learnable:
        # Gap 3: pass full tool bundle so co_triggered edges learn multi-skill sessions
        co = [t for t in tools if t and t != primary]
        learning_results.append(
            record_outcome(
                skill_name=primary,
                outcome=o,
                task_summary=session.query[:200],
                project=session.project,
                ide=session.ide,
                quality=None if quality is None else float(quality),
                co_skills=co,
                outcome_source="explicit" if skill_name else "derived",
            )
        )

    # Broker dump archives context; learning already applied on primary above.
    end = broker.end_session(
        session_id,
        outcome=o if o != "skip" else "partial",
        summary=notes or "",
        ide_session_id=ide_session_id,
        record_learning=False,
    )

    after = _belief_snapshot(tools)
    deltas = _belief_delta(before, after)

    # A07: evidence manifest (additive keys only; backfill-safe for old sessions).
    adir = ARTIFACTS_ROOT / session_id
    if adir.is_dir():
        import json as _json
        (adir / "summary.json").write_text(_json.dumps(
            {"outcome": o, "notes": notes, "quality": quality}))
    artifacts_dir, manifest = artifact_manifest(session_id)

    return {
        "contract": CONTRACT,
        "version": API_VERSION,
        "session_id": session_id,
        "status": end.get("status", "dumped"),
        "outcome": o,
        "primary_skill": primary,
        "tools": tools,
        "learning": learning_results,
        "belief_delta": deltas,
        "belief_updated": len(deltas) > 0,
        "notes": notes,
        "quality": quality,
        "artifacts_dir": artifacts_dir,
        "artifact_manifest": manifest,
    }


def get_session(session_id: str) -> dict[str, Any]:
    from server.tool_session_broker import get_tool_session_broker

    broker = get_tool_session_broker()
    session = broker.get_session(session_id)
    if session is None:
        raise KeyError(f"unknown session: {session_id}")
    data = session.to_dict()
    data["contract"] = CONTRACT
    data["mcp_proxy"] = broker.get_mcp_proxy_config(session_id)
    return data
