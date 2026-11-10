"""Traffic classification for routing_log rows.

Why: routing_log accumulates synthetic traffic (eval harnesses, smoke tests,
template probe scripts) alongside genuine user work. Aggregates over the raw
table -- routing accuracy, coach antipatterns, Thompson beliefs, cluster
affinity -- blend the two, so a template probe scored "wrong" is counted as a
routing failure and drags real quality metrics down.

This module assigns a `harness_tag` at write time so those aggregates can be
filtered. It reuses the existing (previously unwritten) `harness_tag` column;
no migration required.

Labels:
    organic   -- genuine interactive work
    eval      -- eval harness (ide='eval')
    test      -- test/smoke/stress project namespace
    synthetic -- template probe task_summary

Pure functions only: no DB access, no imports from server internals, so this
stays trivially unit-testable and safe to call from any write site.
"""

from __future__ import annotations

# Projects that exist only to exercise the router, not to do work.
# NOTE: 'default' is deliberately absent -- it carries 5,638 real routes.
TEST_PROJECTS = frozenset({
    "test", "test_proj", "smoke", "stress", "cold-warm", "auto_invoke",
    "cognee", "omniroute", "zuhriti", "cebo", "context-harness",
})

# Exact task_summary strings emitted by template probe/eval scripts. Sourced
# empirically from db/nexus.db (2,618 rows, 26.4% of all history as of
# 2026-09-26). Exact-match on purpose: substring matching would misclassify
# genuine tasks that merely mention these words.
# Keep this list auditable -- every entry should be justifiable as generated.
TEMPLATE_TASKS = frozenset({
    # phase-probe scripts
    "research: Research Context",
    "understand: Understand Task",
    "verify: Verify Output",
    "implement: Implement Solution",
    "audit: Audit and Learn",
    "design: Design Plan",
    # gallery / typography regression probes
    "fix product image gallery",
    "audit landing page typography",
    # synthetic task fixtures
    "deploy docker",
    "implement user authentication",
    "fix the login bug",
})

# IDE identifiers that are machine-generated rather than interactive.
EVAL_IDES = frozenset({"eval"})

ORGANIC = "organic"
EVAL = "eval"
TEST = "test"
SYNTHETIC = "synthetic"

ALL_LABELS = (ORGANIC, EVAL, TEST, SYNTHETIC)


def classify_traffic(task_summary: str | None, project: str | None = None,
                     ide: str | None = None) -> str:
    """Return the traffic label for one routing_log row.

    Precedence is deliberate: an explicit machine `ide` outranks project
    namespace, which outranks task-template matching. The strongest available
    evidence wins, so a row is only called `synthetic` when nothing better
    explains it.

    Never raises: unknown/None inputs fall through to ORGANIC.
    """
    if (ide or "").strip().lower() in EVAL_IDES:
        return EVAL

    if (project or "").strip().lower() in TEST_PROJECTS:
        return TEST

    if (task_summary or "").strip() in TEMPLATE_TASKS:
        return SYNTHETIC

    return ORGANIC


def is_synthetic(task_summary: str | None, project: str | None = None,
                 ide: str | None = None) -> bool:
    """True when the row is machine-generated and should be excluded from
    human-quality aggregates (accuracy, antipatterns, affinity)."""
    return classify_traffic(task_summary, project, ide) != ORGANIC
