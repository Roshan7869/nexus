"""CoachBridge — reads Coach cache JSON and upserts to NEXUS tables.

The bridge reads from ``~/.copilot-analytics-cache/`` (the Coach extension's
on-disk cache) and populates the ``coach_*`` SQLite tables.  If no cache
exists, every method returns empty defaults.

Usage::

    bridge = get_bridge()
    bridge.sync()                     # full sync from cache
    scores = bridge.get_practice_scores("my-project")
    patterns = bridge.get_antipatterns(severity="high")
    status = bridge.get_bridge_status()
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.coach.schema import DBTables, ensure_coach_schema, get_connection

# ---------------------------------------------------------------------------
# Paths searched for Coach cache data (in priority order)
# ---------------------------------------------------------------------------
CACHE_PATHS: list[Path] = [
    Path.home() / ".copilot-analytics-cache",
    Path("/tmp/coach-cache"),
    Path("/tmp/.coach-cache"),
]

CACHE_FILES: dict[str, str] = {
    "parsed": "parsed.json",
    "meta": "meta.json",
    "sidebar": "sidebar-stats.json",
}

# ---------------------------------------------------------------------------
# Dataclass for structured bridge output
# ---------------------------------------------------------------------------


@dataclass
class BridgeStatus:
    """Health and metadata of the Coach → NEXUS bridge."""

    cache_found: bool = False
    cache_path: str = ""
    cache_version: int = 0
    last_sync: str = ""
    session_count: int = 0
    workspace_count: int = 0
    score_count: int = 0
    antipattern_count: int = 0
    error: str = ""


@dataclass
class PracticeScores:
    """Practice scores for a single project on a single date."""

    project: str = "default"
    date: str = ""
    prompt_quality: float = 0.0
    session_hygiene: float = 0.0
    code_review: float = 0.0
    tool_mastery: float = 0.0
    context_management: float = 0.0
    total_score: float = 0.0


@dataclass
class AntiPattern:
    """An anti-pattern detection from Coach rules."""

    rule_id: str = ""
    rule_name: str = ""
    group_name: str = ""
    severity: str = "low"
    count: int = 0
    total: int = 0
    ratio: float = 0.0
    description: str = ""
    suggestion: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)
    weekly_hist: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionSummary:
    """Aggregated summary of a single coding session."""

    session_id: str = ""
    harness: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    request_count: int = 0
    model_usage: dict[str, Any] = field(default_factory=dict)
    first_activity: int | None = None
    last_activity: int | None = None
    end_reason: str = "unknown"


# ---------------------------------------------------------------------------
# Cache discovery
# ---------------------------------------------------------------------------


def find_coach_cache() -> tuple[Path | None, dict[str, Path]]:
    """Locate the Coach cache directory and its known JSON files.

    Returns:
        ``(cache_dir, {kind: path})`` where *kind* is one of
        ``"parsed"``, ``"meta"``, ``"sidebar"``.  If no cache is found
        returns ``(None, {})``.
    """
    for candidate in CACHE_PATHS:
        if candidate.is_dir():
            found: dict[str, Path] = {}
            for kind, filename in CACHE_FILES.items():
                p = candidate / filename
                if p.is_file():
                    found[kind] = p
            if found:
                return candidate, found
    return None, {}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    """Safely load a JSON file, returning ``{}`` on failure."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        return dict(json.loads(raw))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _compute_scores_from_sessions(
    sessions: list[dict[str, Any]],
) -> PracticeScores:
    """Compute approximate practice scores from raw session data.

    Uses heuristic signals extracted from session requests since the
    full Coach rule engine is not available in Python.
    """
    scores = PracticeScores(date=_today_iso())

    total_requests = 0
    total_compactions = 0
    total_cancels = 0
    total_tools_set: set[str] = set()
    total_message_len = 0
    total_response_len = 0
    total_loc = 0
    long_prompts = 0
    sessions_with_tool_use = 0
    sessions_with_review = 0
    total_models: set[str] = set()

    for sess in sessions:
        requests = sess.get("requests") or []
        total_requests += len(requests)

        models_in_sess: set[str] = set()
        tools_in_sess: set[str] = set()

        for req in requests:
            msg = req.get("messageText", "")
            msg_len = req.get("messageLength", 0) or len(msg)
            resp_len = req.get("responseLength", 0) or len(
                req.get("responseText", "")
            )
            total_message_len += msg_len
            total_response_len += resp_len

            if msg_len > 200:
                long_prompts += 1

            if req.get("isCanceled"):
                total_cancels += 1

            tools = req.get("toolsUsed") or []
            total_tools_set.update(tools)
            tools_in_sess.update(tools)

            edits = req.get("editedFiles") or []
            if len(edits) >= 2:
                sessions_with_review += 1

            code_blocks = req.get("aiCode") or req.get("userCode") or []
            for block in code_blocks:
                total_loc += block.get("loc", 0) if isinstance(block, dict) else 0

            model_id = req.get("modelId", "")
            if model_id:
                models_in_sess.add(model_id)

            comp = req.get("compaction")
            if comp:
                total_compactions += 1

        total_models.update(models_in_sess)
        if tools_in_sess:
            sessions_with_tool_use += 1

    # --- Compute dimension scores (0-100) ---
    n = max(total_requests, 1)

    # Prompt quality: ratio of detailed prompts + avg message length signal
    detail_ratio = long_prompts / n
    scores.prompt_quality = min(100.0, round(detail_ratio * 100 + 10, 1))

    # Session hygiene: low cancel rate + compaction awareness
    cancel_rate = total_cancels / n
    hygiene_base = max(0.0, 100 - cancel_rate * 200)
    comp_bonus = min(10, total_compactions * 2)
    scores.session_hygiene = min(100.0, round(hygiene_base + comp_bonus, 1))

    # Code review: edits across multiple files as signal
    review_ratio = sessions_with_review / max(len(sessions), 1)
    scores.code_review = min(100.0, round(review_ratio * 100 + 20, 1))

    # Tool mastery: tool variety vs session count
    tool_diversity = len(total_tools_set) / max(len(sessions), 1) * 25
    tool_use_ratio = sessions_with_tool_use / max(len(sessions), 1)
    scores.tool_mastery = min(
        100.0,
        round(min(tool_diversity, 60) + tool_use_ratio * 40, 1),
    )

    # Context management: compaction events, session structure
    comp_ratio = total_compactions / n
    scores.context_management = min(100.0, round(comp_ratio * 100 + 50, 1))

    scores.total_score = round(
        (
            scores.prompt_quality
            + scores.session_hygiene
            + scores.code_review
            + scores.tool_mastery
            + scores.context_management
        )
        / 5,
        1,
    )

    return scores


def _detect_antipatterns(
    sessions: list[dict[str, Any]],
) -> list[AntiPattern]:
    """Detect common anti-patterns from raw session data.

    This is a lightweight approximation of the Coach rule engine.
    """
    patterns: list[AntiPattern] = []
    total_requests = sum(
        len(s.get("requests") or []) for s in sessions
    )
    total_sessions = max(len(sessions), 1)

    # --- Lazy prompting (short / single-word prompts) ---
    short_prompt_count = 0
    for sess in sessions:
        for req in sess.get("requests") or []:
            msg = req.get("messageText", "")
            if msg and len(msg.strip()) < 30:
                short_prompt_count += 1
    if short_prompt_count > 0:
        patterns.append(
            AntiPattern(
                rule_id="lazy-prompting",
                rule_name="Lazy Prompting",
                group_name="prompt-quality",
                severity="medium" if short_prompt_count > 5 else "low",
                count=short_prompt_count,
                total=total_requests,
                ratio=round(short_prompt_count / max(total_requests, 1), 3),
                description="Short or vague prompts that lack context and specificity.",
                suggestion="Include specific context, error messages, and expected behavior in each prompt.",
            )
        )

    # --- High cancel rate ---
    cancel_count = 0
    for sess in sessions:
        for req in sess.get("requests") or []:
            if req.get("isCanceled"):
                cancel_count += 1
    if cancel_count > 0 and cancel_count / max(total_requests, 1) > 0.1:
        patterns.append(
            AntiPattern(
                rule_id="high-cancel-rate",
                rule_name="High Cancel Rate",
                group_name="session-hygiene",
                severity="high" if cancel_count / max(total_requests, 1) > 0.3 else "medium",
                count=cancel_count,
                total=total_requests,
                ratio=round(cancel_count / max(total_requests, 1), 3),
                description="Frequent cancellation of AI responses disrupts workflow and wastes tokens.",
                suggestion="Let the AI complete its response before judging; refine the prompt instead of cancelling.",
            )
        )

    # --- Low tool variety ---
    all_tools: set[str] = set()
    for sess in sessions:
        for req in sess.get("requests") or []:
            all_tools.update(req.get("toolsUsed") or [])
    if len(all_tools) <= 2 and total_sessions >= 3:
        patterns.append(
            AntiPattern(
                rule_id="low-tool-variety",
                rule_name="Low Tool Variety",
                group_name="tool-mastery",
                severity="medium",
                count=len(all_tools),
                total=total_sessions,
                ratio=round(len(all_tools) / total_sessions, 3),
                description=f"Only {len(all_tools)} unique tools used across {total_sessions} sessions.",
                suggestion="Explore CLI tools, file operations, search, and custom MCP tools for better results.",
            )
        )

    # --- No code review pattern ---
    review_count = 0
    for sess in sessions:
        for req in sess.get("requests") or []:
            edits = req.get("editedFiles") or []
            if len(edits) >= 2:
                review_count += 1
    if review_count == 0 and total_sessions >= 2:
        patterns.append(
            AntiPattern(
                rule_id="no-code-review",
                rule_name="No Code Review Pattern",
                group_name="code-review",
                severity="medium",
                count=0,
                total=total_sessions,
                ratio=0.0,
                description="No multi-file code review patterns detected in sessions.",
                suggestion="Ask the AI to review, diff, and reason about changes across files before applying.",
            )
        )

    # --- No compaction events (context management) ---
    comp_count = 0
    for sess in sessions:
        for req in sess.get("requests") or []:
            if req.get("compaction"):
                comp_count += 1
    if comp_count == 0 and total_requests > 10:
        patterns.append(
            AntiPattern(
                rule_id="no-context-compaction",
                rule_name="No Context Compaction",
                group_name="context-management",
                severity="low",
                count=0,
                total=total_requests,
                ratio=0.0,
                description="No context compaction events detected across many requests.",
                suggestion="Use strategic compaction to manage context windows and reduce token waste.",
            )
        )

    return patterns


# ---------------------------------------------------------------------------
# Bridge class
# ---------------------------------------------------------------------------


class CoachBridge:
    """Bridge between Coach cache JSON and NEXUS SQLite tables.

    All methods gracefully return empty results when no cache data is
    available (Coach extension not installed, or no sessions recorded).
    """

    def __init__(self) -> None:
        self._cache_dir: Path | None = None
        self._cache_files: dict[str, Path] = {}
        self._raw: dict[str, Any] = {}
        self._sessions: list[dict[str, Any]] = []
        self._workspaces: list[dict[str, Any]] = []
        self._meta: dict[str, Any] = {}
        self._sidebar: dict[str, Any] = {}
        self._loaded = False
        self._load_error: str | None = None

    # ------------------------------------------------------------------
    # Cache loading
    # ------------------------------------------------------------------

    def _load_cache(self) -> bool:
        """Load Coach cache from disk.

        Returns True if cache was loaded, False otherwise (sets
        ``_load_error`` on failure).
        """
        if self._loaded:
            return bool(self._raw)

        cache_dir, files = find_coach_cache()
        if cache_dir is None or not files:
            self._load_error = "No Coach cache found on disk"
            self._loaded = True
            return False

        self._cache_dir = cache_dir
        self._cache_files = files

        if "meta" in files:
            self._meta = _load_json(files["meta"])
        if "sidebar" in files:
            self._sidebar = _load_json(files["sidebar"])
        if "parsed" in files:
            parsed = _load_json(files["parsed"])
            self._raw = parsed
            self._sessions = parsed.get("sessions") or []
            workspace_tuples = parsed.get("workspaces") or []
            self._workspaces = [ws for _, ws in workspace_tuples] if isinstance(workspace_tuples, list) else []

        self._loaded = True
        return True

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_practice_scores(
        self,
        project: str = "default",
        days: int = 30,
    ) -> list[PracticeScores]:
        """Return practice scores for a project, most recent first.

        If no cache is available, computes approximate scores from
        whatever data was previously synced into the database.
        """
        results: list[PracticeScores] = []
        try:
            conn = get_connection()
            ensure_coach_schema(conn)
            rows = conn.execute(
                """SELECT project, date, prompt_quality, session_hygiene,
                          code_review, tool_mastery, context_management,
                          total_score
                   FROM coach_practice_scores
                   WHERE project = ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (project, days),
            ).fetchall()
            for row in rows:
                results.append(
                    PracticeScores(
                        project=row["project"],
                        date=row["date"],
                        prompt_quality=row["prompt_quality"],
                        session_hygiene=row["session_hygiene"],
                        code_review=row["code_review"],
                        tool_mastery=row["tool_mastery"],
                        context_management=row["context_management"],
                        total_score=row["total_score"],
                    )
                )
            conn.close()
        except Exception:
            pass
        return results

    def get_antipatterns(
        self,
        project: str = "default",
        severity: str | None = None,
        since: str | None = None,
    ) -> list[AntiPattern]:
        """Return anti-pattern detections, optionally filtered."""
        results: list[AntiPattern] = []
        try:
            conn = get_connection()
            ensure_coach_schema(conn)
            clauses = ["project = ?"]
            params: list[Any] = [project]
            if severity:
                clauses.append("severity = ?")
                params.append(severity)
            if since:
                clauses.append("last_detected >= ?")
                params.append(since)
            rows = conn.execute(
                f"""SELECT rule_id, rule_name, group_name, severity,
                           count, total, ratio, description, suggestion,
                           details_json, weekly_hist_json, last_detected
                    FROM coach_antipatterns
                    WHERE {' AND '.join(clauses)}
                    ORDER BY severity DESC, count DESC""",
                params,
            ).fetchall()
            for row in rows:
                results.append(
                    AntiPattern(
                        rule_id=row["rule_id"],
                        rule_name=row["rule_name"],
                        group_name=row["group_name"],
                        severity=row["severity"],
                        count=row["count"],
                        total=row["total"],
                        ratio=row["ratio"],
                        description=row["description"],
                        suggestion=row["suggestion"],
                        details=json.loads(row["details_json"] or "[]"),
                        weekly_hist=json.loads(row["weekly_hist_json"] or "[]"),
                    )
                )
            conn.close()
        except Exception:
            pass
        return results

    def get_bridge_status(self) -> BridgeStatus:
        """Return bridge health and cache metadata."""
        self._load_cache()
        status = BridgeStatus()
        status.cache_found = self._cache_dir is not None
        status.cache_path = str(self._cache_dir or "")
        status.cache_version = self._meta.get("version", 0)
        status.session_count = len(self._sessions)
        status.workspace_count = len(self._workspaces)
        if self._load_error:
            status.error = self._load_error

        try:
            conn = get_connection()
            ensure_coach_schema(conn)
            row = conn.execute(
                "SELECT last_sync_at FROM coach_cache_meta ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                status.last_sync = row["last_sync_at"] or ""
            sc = conn.execute("SELECT COUNT(*) FROM coach_practice_scores").fetchone()[0]
            ac = conn.execute("SELECT COUNT(*) FROM coach_antipatterns").fetchone()[0]
            status.score_count = sc
            status.antipattern_count = ac
            conn.close()
        except Exception:
            pass

        return status

    def sync(self) -> BridgeStatus:
        """Run a full sync from Coach cache to NEXUS tables.

        Loads cache JSON, computes analytics, and upserts into SQLite.
        """
        self._loaded = False
        self._load_error = None
        cache_ok = self._load_cache()

        status = BridgeStatus()
        status.cache_found = self._cache_dir is not None
        status.cache_path = str(self._cache_dir or "")
        status.cache_version = self._meta.get("version", 0)
        status.session_count = len(self._sessions)
        status.workspace_count = len(self._workspaces)

        try:
            conn = get_connection()
            ensure_coach_schema(conn)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            if cache_ok and self._sessions:
                self._upsert_sessions(conn)
                self._upsert_daily_activity(conn)
                scores = self._upsert_practice_scores(conn)
                antipatterns = self._upsert_antipatterns(conn)
                status.score_count = scores
                status.antipattern_count = antipatterns
            else:
                conn.execute(
                    "INSERT INTO coach_cache_meta (version, last_sync_at, session_count, workspace_count, error) VALUES (?, ?, ?, ?, ?)",
                    (status.cache_version, now, status.session_count, status.workspace_count, status.error),
                )

            conn.execute(
                "INSERT INTO coach_cache_meta (version, cache_path, last_sync_at, session_count, workspace_count, error) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    status.cache_version,
                    status.cache_path,
                    now,
                    status.session_count,
                    status.workspace_count,
                    status.error or "",
                ),
            )

            conn.commit()
            conn.close()
        except Exception as exc:
            status.error = str(exc)

        return status

    # ------------------------------------------------------------------
    # Internal upsert helpers
    # ------------------------------------------------------------------

    def _upsert_sessions(self, conn: sqlite3.Connection) -> int:
        count = 0
        for sess in self._sessions:
            sid = sess.get("sessionId", "")
            if not sid:
                continue
            harness = sess.get("harness", "")
            ws_id = sess.get("workspaceId", "")
            ws_name = sess.get("workspaceName", "")
            req_count = sess.get("requestCount", 0) or len(sess.get("requests") or [])
            model_usage = sess.get("modelUsage") or {}
            first_act = sess.get("creationDate")
            last_act = sess.get("lastMessageDate")
            end_reason = sess.get("endReason", "unknown")
            date_str = _today_iso()
            if last_act:
                try:
                    date_str = datetime.fromtimestamp(last_act / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                except (OSError, ValueError):
                    pass

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO coach_sessions
                       (session_id, harness, workspace_id, workspace_name,
                        date, request_count, model_usage_json,
                        first_activity, last_activity, end_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sid,
                        harness,
                        ws_id,
                        ws_name,
                        date_str,
                        req_count,
                        json.dumps(model_usage),
                        first_act,
                        last_act,
                        end_reason,
                    ),
                )
                count += 1
            except Exception:
                pass
        return count

    def _upsert_daily_activity(self, conn: sqlite3.Connection) -> int:
        from collections import Counter, defaultdict

        date_counts: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "sessions": set(), "models": set(), "tools": set()}
        )

        for sess in self._sessions:
            sid = sess.get("sessionId", "")
            last_act = sess.get("lastMessageDate")
            if not last_act:
                continue
            try:
                d = datetime.fromtimestamp(last_act / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (OSError, ValueError):
                continue

            dc = date_counts[d]
            dc["requests"] += len(sess.get("requests") or [])
            dc["sessions"].add(sid)
            for req in sess.get("requests") or []:
                mid = req.get("modelId", "")
                if mid:
                    dc["models"].add(mid)
                for t in req.get("toolsUsed") or []:
                    dc["tools"].add(t)

        inserted = 0
        for date_str, dc in sorted(date_counts.items()):
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO coach_daily_activity
                       (project, date, request_count, session_count,
                        unique_models, unique_tools)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        "default",
                        date_str,
                        dc["requests"],
                        len(dc["sessions"]),
                        json.dumps(list(dc["models"])),
                        json.dumps(list(dc["tools"])),
                    ),
                )
                inserted += 1
            except Exception:
                pass
        return inserted

    def _upsert_practice_scores(self, conn: sqlite3.Connection) -> int:
        if not self._sessions:
            return 0
        scores = _compute_scores_from_sessions(self._sessions)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO coach_practice_scores
                   (project, date, prompt_quality, session_hygiene,
                    code_review, tool_mastery, context_management,
                    total_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "default",
                    scores.date,
                    scores.prompt_quality,
                    scores.session_hygiene,
                    scores.code_review,
                    scores.tool_mastery,
                    scores.context_management,
                    scores.total_score,
                ),
            )
            return 1
        except Exception:
            return 0

    def _upsert_antipatterns(self, conn: sqlite3.Connection) -> int:
        if not self._sessions:
            return 0
        patterns = _detect_antipatterns(self._sessions)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        inserted = 0
        for ap in patterns:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO coach_antipatterns
                       (project, rule_id, rule_name, group_name, severity,
                        count, total, ratio, description, suggestion,
                        details_json, weekly_hist_json, last_detected,
                        updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "default",
                        ap.rule_id,
                        ap.rule_name,
                        ap.group_name,
                        ap.severity,
                        ap.count,
                        ap.total,
                        ap.ratio,
                        ap.description,
                        ap.suggestion,
                        json.dumps(ap.details),
                        json.dumps(ap.weekly_hist),
                        now_iso,
                        now_iso,
                    ),
                )
                inserted += 1
            except Exception:
                pass
        return inserted


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bridge: CoachBridge | None = None


def get_bridge() -> CoachBridge:
    """Return the singleton CoachBridge instance, auto-feeding from DB if needed."""
    global _bridge
    if _bridge is None:
        _bridge = CoachBridge()
        # Auto-feed coach tables from NEXUS operational data if cache is missing
        try:
            cache_path, _ = find_coach_cache()
            if not cache_path:
                from server.coach.feeder import feed_all
                feed_all(project="default")
        except Exception:
            pass  # feeder failures shouldn't crash the bridge
    return _bridge
