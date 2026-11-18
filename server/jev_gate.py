"""JEV call gate: budget, cache and label ledger.

Why this module exists
----------------------
`decision_classifier.TypeSafeJevClassifier` can already spend a hosted request. Nothing
stopped it from spending one *per query*, and nothing recorded what it answered — so the
API could be neither costed nor learned from.

This gate sits in front of it and enforces three rules:

1. **Ask once per task.** Labels are keyed on (task_hash, question); a repeat is free.
2. **Never exceed the daily cap.** `NEXUS_JEV_BUDGET` calls/day, counted in `jev_budget`.
3. **Only ask when the local answer is weak.** A confident local decision is recorded as a
   free label (local_label, agreed=NULL) instead of being paid for.

Every JEV answer is written to `jev_labels` with what the local path believed, which is the
supervised corpus the bake-in (P6/P7) learns from.

Modes (`NEXUS_JEV_MODE`)
------------------------
``off``      no calls at all (default until the key is validated)
``shadow``   answer from local, label from JEV on a daemon thread — 0 ms on the caller path
``enforce``  JEV may override **only** when the local path declined
``verify``   sample a small fraction to detect drift after the local model takes over
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from typing import Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS jev_labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_hash     TEXT NOT NULL,
    task_summary  TEXT NOT NULL,
    question      TEXT NOT NULL,
    label         TEXT NOT NULL,
    confidence    REAL,
    local_label   TEXT,
    local_conf    REAL,
    agreed        INTEGER,
    mode          TEXT,
    source        TEXT DEFAULT 'jev',
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(task_hash, question)
);
CREATE INDEX IF NOT EXISTS idx_jev_labels_question ON jev_labels(question, label);
CREATE TABLE IF NOT EXISTS jev_budget (
    day       TEXT PRIMARY KEY,
    calls     INTEGER NOT NULL DEFAULT 0,
    max_calls INTEGER NOT NULL DEFAULT 40
);
"""

MODES = {"off", "shadow", "enforce", "verify"}
_LOCK = threading.Lock()


def mode() -> str:
    m = (os.environ.get("NEXUS_JEV_MODE") or "off").strip().lower()
    return m if m in MODES else "off"


def task_hash(task: str) -> str:
    return hashlib.sha256((task or "").strip().lower().encode("utf-8", "replace")).hexdigest()[:16]


def _db_path() -> str:
    from server.db import get_db_path
    return str(get_db_path())


def ensure_schema(db_path: str | None = None) -> None:
    con = sqlite3.connect(db_path or _db_path(), timeout=30)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def budget_remaining(db_path: str | None = None) -> int:
    cap = int(os.environ.get("NEXUS_JEV_BUDGET", "40") or 40)
    day = time.strftime("%Y-%m-%d")
    con = sqlite3.connect(db_path or _db_path(), timeout=30)
    try:
        con.executescript(SCHEMA)
        row = con.execute("SELECT calls FROM jev_budget WHERE day=?", (day,)).fetchone()
        used = row[0] if row else 0
        return max(0, cap - used)
    finally:
        con.close()


def _consume(db_path: str | None = None) -> bool:
    """Reserve one call. Returns False when the cap is reached."""
    cap = int(os.environ.get("NEXUS_JEV_BUDGET", "40") or 40)
    day = time.strftime("%Y-%m-%d")
    with _LOCK:
        con = sqlite3.connect(db_path or _db_path(), timeout=30)
        try:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT INTO jev_budget(day, calls, max_calls) VALUES (?,1,?) "
                "ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
                (day, cap))
            used = con.execute("SELECT calls FROM jev_budget WHERE day=?", (day,)).fetchone()[0]
            if used > cap:
                con.execute("UPDATE jev_budget SET calls = calls - 1 WHERE day=?", (day,))
                con.commit()
                return False
            con.commit()
            return True
        finally:
            con.close()


def cached(task: str, question: str, db_path: str | None = None) -> str | None:
    con = sqlite3.connect(db_path or _db_path(), timeout=30)
    try:
        con.executescript(SCHEMA)
        row = con.execute(
            "SELECT label FROM jev_labels WHERE task_hash=? AND question=?",
            (task_hash(task), question)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def record(task: str, question: str, label: str | None, confidence: float | None = None,
           local_label: str | None = None, local_conf: float | None = None,
           db_path: str | None = None) -> None:
    """Write one labelled example. `label=None` records the free local-only case."""
    if not label and not local_label:
        return
    agreed = None
    if label and local_label:
        agreed = int(str(label).lower() == str(local_label).lower())
    con = sqlite3.connect(db_path or _db_path(), timeout=30)
    try:
        con.executescript(SCHEMA)
        con.execute(
            """INSERT INTO jev_labels
                   (task_hash, task_summary, question, label, confidence,
                    local_label, local_conf, agreed, mode)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task_hash, question) DO UPDATE SET
                   label=COALESCE(excluded.label, label),
                   confidence=COALESCE(excluded.confidence, confidence),
                   local_label=COALESCE(excluded.local_label, local_label),
                   local_conf=COALESCE(excluded.local_conf, local_conf),
                   agreed=COALESCE(excluded.agreed, agreed),
                   mode=excluded.mode""",
            (task_hash(task), (task or "")[:400], question, label or local_label,
             confidence, local_label, local_conf, agreed, mode()))
        con.commit()
    finally:
        con.close()


def _weak(local_label: str | None, local_conf: float) -> bool:
    """Is the local answer weak enough to be worth a paid second opinion?"""
    if not local_label or str(local_label).lower() in ("general", "unknown", ""):
        return True
    return float(local_conf or 0.0) < float(os.environ.get("NEXUS_JEV_MIN_CONF", "0.55"))


def consult(question: str, task: str, options: Sequence[str],
            local_label: str | None = None, local_conf: float = 0.0,
            instructions: str = "") -> str | None:
    """Return a JEV label for `question`, or None to keep the local answer.

    ``shadow`` never blocks and never changes the caller's result; it only records.
    ``enforce`` returns a label only when the local answer was weak.
    """
    m = mode()
    if m == "off":
        return None

    hit = cached(task, question)
    if hit:
        # Free: already paid for this task+question once.
        record(task, question, hit, local_label=local_label, local_conf=local_conf)
        return hit if (m == "enforce" and _weak(local_label, local_conf)) else None

    if m == "enforce" and not _weak(local_label, local_conf):
        record(task, question, None, local_label=local_label, local_conf=local_conf)
        return None

    if m == "verify" and (int(task_hash(task), 16) % 100) >= int(
            os.environ.get("NEXUS_JEV_VERIFY_PCT", "5")):
        return None

    def _work() -> None:
        # Availability first: an unkeyed install must not consume budget.
        try:
            from server.decision_classifier import get_classifier
            classifier = get_classifier("jev")
            if not classifier.available():
                return
        except Exception:
            return
        if not _consume():
            return
        try:
            ans = classifier.classify(question, task, options, instructions=instructions)
        except Exception:
            return
        if ans.ok:
            record(task, question, ans.value, ans.confidence,
                   local_label=local_label, local_conf=local_conf)

    if m == "shadow":
        # Fire and forget: the caller already has its answer, so a hosted round-trip
        # must never sit on the routing path.
        threading.Thread(target=_work, daemon=True).start()
        return None

    _work()  # enforce/verify: synchronous, caller accepts the latency for correctness
    return cached(task, question)
