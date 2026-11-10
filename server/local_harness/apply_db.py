"""HMR live-apply callback — turns loader diff actions into resource-surface
mutations (Cordis §5.2.1; okf-native-cordis Phase 5). See per-file doc:
docs/plans/second-brain-kg/per-file/server.local_harness.apply_db.py.md

make_apply(conn, realm) -> apply(action, old, new). The CALLER owns the
transaction: apply never commits/rollbacks (atomic reconcile).

Inverse model (delta vs the doc's diff sketch): every forward op captures a
preimage first, and ``rollback:<action>`` RESTORES that preimage instead of
re-dispatching the base action. loader.py:102 swaps (old, new) on rollback, so a
naive re-dispatch reads ids off the wrong side for add/remove (rollback:remove
arrives as (old=None, new=E)) and re-running "remove" would archive again rather
than un-archive. Preimage restore is strictly inverse-consistent, which is the
stated invariant, and makes "DB unchanged after failure" hold even before the
handler's conn.rollback().

Archive, never hard-delete (forward remove disables status only). Rollback of an
add that CREATED a row deletes exactly that row — restoring true prestate; a
pre-existing row is never deleted (it restores to its prior status).
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# resources.resource_type CHECK constraint (migration 001) — Entry.kind must map
# onto it; unknown kinds fall back to 'skill' rather than failing the batch.
_ALLOWED_TYPES = frozenset({
    "skill", "agent", "command", "cli_tool", "mcp", "hook",
    "topic", "design_system", "craft", "doc", "spec",
})

# Read-time intercept registry. loader's intercept policy is "in-place,
# read-time merge, no reload" — process-local is the faithful home; there is no
# durable column for it (resource_policies.preconditions_json is a list-typed
# policy read by broker_runtime.py:352 — merging a dict in would corrupt it).
# The live snapshot is also written into the reconcile audit row so it survives
# the process. DELTA vs doc sketch (which targeted preconditions_json).
_INTERCEPTS: dict[str, dict] = {}


def get_intercept(resource_name: str) -> dict:
    """Read-time merge source for intercept policy meta."""
    return dict(_INTERCEPTS.get(resource_name, {}))


def make_apply(conn, realm: str):
    """Return apply(action, old, new) mutating the resource surface.

    action ∈ {add, remove, reload, rebuild, reassign, intercept,
    rollback:<action>}. MUST NOT commit — caller owns the txn.
    """
    pre: dict[tuple[str, str], dict] = {}

    # ── preimage helpers ────────────────────────────────────────────────────
    def _snapshot(action: str, name: str) -> None:
        if (action, name) in pre:
            return
        row = conn.execute(
            "SELECT status, cluster, resource_type FROM resources WHERE name=?",
            (name,)).fetchone()
        pre[(action, name)] = (
            {"missing": True} if row is None else {
                "missing": False,
                "status": row[0],
                "cluster": row[1],
                "resource_type": row[2],
                "intercept": dict(_INTERCEPTS.get(name, {})),
            })

    def _restore(name: str, snap: dict) -> None:
        if snap.get("missing"):
            # row did not exist before the forward op created it → exact prestate
            conn.execute("DELETE FROM resources WHERE name=?", (name,))
            _INTERCEPTS.pop(name, None)
            return
        conn.execute(
            "UPDATE resources SET status=?, cluster=?, resource_type=?, "
            "updated_at=datetime('now') WHERE name=?",
            (snap["status"], snap["cluster"], snap["resource_type"], name))
        if snap.get("intercept"):
            _INTERCEPTS[name] = snap["intercept"]
        else:
            _INTERCEPTS.pop(name, None)

    # ── forward primitives ──────────────────────────────────────────────────
    def _activate(name: str) -> None:
        conn.execute(
            "UPDATE resources SET status='active', archive_reason=NULL, "
            "archived_at=NULL, updated_at=datetime('now') WHERE name=?", (name,))

    def _archive(name: str, reason: str) -> None:
        conn.execute(
            "UPDATE resources SET status='archived', archive_reason=?, "
            "archived_at=datetime('now'), updated_at=datetime('now') "
            "WHERE name=?", (reason, name))

    def _insert(entry) -> None:
        cfg = entry.config or {}
        rtype = entry.kind if entry.kind in _ALLOWED_TYPES else "skill"
        status = "archived" if entry.disabled else "active"
        conn.execute(
            "INSERT INTO resources (name, resource_type, file_path, description, "
            "status, body_text) VALUES (?,?,?,?,?,?)",
            (entry.id, rtype, cfg.get("file_path", "unknown"),
             cfg.get("description", ""), status, cfg.get("body_text", "")))

    def _rematerialize(entry) -> None:
        """Re-materialize cluster link / sections / kind from entry.config."""
        cfg = entry.config or {}
        rid_row = conn.execute(
            "SELECT id FROM resources WHERE name=?", (entry.id,)).fetchone()
        if rid_row is None:
            return
        rid = rid_row[0]
        if cfg.get("cluster") is not None:
            conn.execute(
                "UPDATE resources SET cluster=?, updated_at=datetime('now') "
                "WHERE id=?", (cfg["cluster"], rid))
        if entry.kind in _ALLOWED_TYPES:
            conn.execute(
                "UPDATE resources SET resource_type=?, updated_at=datetime('now') "
                "WHERE id=?", (entry.kind, rid))
        raw = cfg.get("sections")
        if isinstance(raw, list) and raw:
            pairs: list[tuple[Any, Any]] = []
            for s in raw:
                if isinstance(s, (list, tuple)) and len(s) == 2:
                    pairs.append((s[0], s[1]))
                elif isinstance(s, dict):
                    pairs.append((s.get("heading") or s.get("title"),
                                  s.get("content") or s.get("text") or ""))
            if pairs:
                from server.okf_sections import write_sections  # lazy
                write_sections(conn, rid, pairs)

    def _merge_intercept(entry) -> None:
        merged = dict(_INTERCEPTS.get(entry.id, {}))
        merged.update(entry.intercept or {})
        _INTERCEPTS[entry.id] = merged

    # ── dispatcher ──────────────────────────────────────────────────────────
    def apply(action: str, old, new) -> None:
        if action.startswith("rollback:"):
            base = action.split(":", 1)[1]
            # loader swapped (old,new) at loader.py:102; id is on whichever
            # side still holds an entry (rollback:remove → new, rollback:add → old).
            target = new if new is not None else old
            if target is None:
                return
            snap = pre.pop((base, target.id), None)
            if snap is None:
                logger.warning("rollback:%s for %s with no preimage", base, target.id)
                return
            _restore(target.id, snap)
            return

        if action == "add":
            if new.disabled:
                return  # declared-but-retired: never activates (no mutation)
            _snapshot("add", new.id)
            row = conn.execute(
                "SELECT id FROM resources WHERE name=?", (new.id,)).fetchone()
            if row is None:
                _insert(new)
            else:
                _activate(new.id)
        elif action == "remove":
            _snapshot("remove", old.id)
            _archive(old.id, "hmr:remove")
        elif action == "reload":
            _snapshot("reload", new.id)
            if new.disabled:
                _archive(new.id, "hmr:disabled")   # unload; never un-retire in place
            else:
                _activate(new.id)                   # fresh load on re-enable
                _rematerialize(new)
        elif action == "rebuild":
            _snapshot("rebuild", new.id)
            _rematerialize(new)
        elif action == "reassign":
            _snapshot("reassign", new.id)
            _rematerialize(new)   # no realm column on resources — realm is
            # assigned per-session at open; isolate changes rematerialize config
        elif action == "intercept":
            _snapshot("intercept", new.id)
            _merge_intercept(new)
        else:
            raise ValueError(f"unknown reconcile action: {action!r}")
        # NOTE: no conn.commit() here — caller owns the txn (atomic reconcile)

    return apply


def record_reconcile(conn, project: str, *, added_ids, removed_ids, changed_ids,
                     extra: dict | None = None) -> None:
    """INSERT one audit row per reconcile call into local_harness_reconciles
    (migration 020). Caller owns commit — runs on the same txn so a failed
    batch still records its rollback attempt after conn.rollback()."""
    payload: dict[str, Any] = {
        "added": list(added_ids),
        "removed": list(removed_ids),
        "changed": list(changed_ids),
        "realm": (extra or {}).get("realm", f"local:{project}"),
    }
    if extra:
        payload.update({k: v for k, v in extra.items() if k != "realm"})
    conn.execute(
        "INSERT INTO local_harness_reconciles "
        "(project, added, removed, changed, report_json) VALUES (?,?,?,?,?)",
        (project, len(payload["added"]), len(payload["removed"]),
         len(payload["changed"]), json.dumps(payload, default=str)))
