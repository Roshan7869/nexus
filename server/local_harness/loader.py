"""EntryTree + reconcile — declarative loader with keyed diff (Cordis §5.2.1, Thm80/73).

Callers inject `apply` callbacks; this module never touches the DB directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Entry:
    id: str                 # reconciliation key (stable)
    kind: str               # "skill" | "bundle" | "group" | "include"
    config: dict = field(default_factory=dict)
    disabled: bool = False  # τ retirement flag
    isolate: str = "local"  # realm scope
    intercept: dict = field(default_factory=dict)


@dataclass
class EntryTree:
    entries: dict[str, Entry] = field(default_factory=dict)


@dataclass
class ReconcileReport:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    rebuilt: list[str] = field(default_factory=list)
    reloaded: list[str] = field(default_factory=list)
    untouched: list[str] = field(default_factory=list)
    rolled_back: bool = False


def diff_trees(old: EntryTree, new: EntryTree) -> tuple[set[str], set[str], set[str]]:
    """Return (added_ids, removed_ids, maybe_changed_ids)."""
    old_ids, new_ids = set(old.entries), set(new.entries)
    return (new_ids - old_ids, old_ids - new_ids, old_ids & new_ids)


def _material_config_diff(a: dict, b: dict) -> bool:
    return a != b


def reconcile(old: EntryTree, new: EntryTree,
              apply: Callable[[str, Entry | None, Entry | None], None]) -> ReconcileReport:
    """Least-disruptive keyed reconcile. Transactional: on error, best-effort
    reverse of applied ops is attempted and rolled_back=True is reported.

    RCA-F7e(loader.py:49): this single-txn + LIFO-reverse pattern is the PRECEDENT to generalize to
        KG fact writes (a GraphDelta abstraction). KG multi-edge updates currently lack this atomic
        boundary. See docs/rca/F7e-graphdelta-writes.md

    Per-field policy:
      id/kind changed      -> rebuild (dispose old fiber + fresh insert)
      isolate changed      -> realm reassign + targeted notify
      intercept changed    -> in-place (read-time merge, no reload)
      config changed       -> hand to component; reload only on material diff
      disabled toggled     -> unload (True) / fresh load (False); NEVER un-retire in place
    """
    report = ReconcileReport()
    added, removed, maybe = diff_trees(old, new)
    applied: list[tuple[str, Entry | None, Entry | None]] = []
    try:
        for eid in sorted(removed):
            apply("remove", old.entries[eid], None)
            applied.append(("remove", old.entries[eid], None))
            report.removed.append(eid)
        for eid in sorted(added):
            apply("add", None, new.entries[eid])
            applied.append(("add", None, new.entries[eid]))
            report.added.append(eid)
        for eid in sorted(maybe):
            o, n = old.entries[eid], new.entries[eid]
            if o.disabled != n.disabled:
                # disabled toggle: unload / fresh load (never un-retire in place)
                apply("reload", o, n)
                applied.append(("reload", o, n))
                report.reloaded.append(eid)
            elif o.kind != n.kind:
                apply("rebuild", o, n)
                applied.append(("rebuild", o, n))
                report.rebuilt.append(eid)
            elif o.isolate != n.isolate:
                apply("reassign", o, n)
                applied.append(("reassign", o, n))
                report.reloaded.append(eid)
            elif o.intercept != n.intercept:
                apply("intercept", o, n)  # in-place, read-time
                applied.append(("intercept", o, n))
                report.reloaded.append(eid)
            elif _material_config_diff(o.config, n.config):
                apply("reload", o, n)
                applied.append(("reload", o, n))
                report.reloaded.append(eid)
            else:
                report.untouched.append(eid)
    except Exception:
        logger.exception("reconcile failed; attempting reverse rollback")
        for action, o, n in reversed(applied):
            try:
                apply(f"rollback:{action}", n, o)
            except Exception:
                logger.exception("rollback step failed")
        report.rolled_back = True
        raise
    return report
