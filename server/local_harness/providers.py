"""M9 coeffect liveness — best-effort provider health snapshot (Cordis spatial half).

Single public function: liveness_snapshot(realm) -> {coeffect_key: alive_bool}.
Signal source for LocalHarnessSession.refresh() — it diffs this snapshot against
committed bindings to drive ACTIVE⇄INACTIVE transitions (session.py refresh).

Contract: never raises; missing/broken subsystems degrade to False. Probes import
server internals lazily inside each try so a dead dependency cannot break the
harness that is asking whether it is alive. Stateless, no lifecycle.
"""
from __future__ import annotations

import socket
import subprocess


def _git_ok(timeout: float = 0.5) -> bool:
    """git usable in the current working tree (coding coeffect)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, timeout=timeout,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _net_ok(timeout: float = 0.3) -> bool:
    """Outbound TCP reachable (internet coeffect)."""
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=timeout):
            return True
    except Exception:
        return False


def site_key(url: str) -> str:
    """Stable coeffect key for a site URL (P1.2): ``site:<sha1[:12]>``."""
    import hashlib
    return "site:" + hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:12]


def liveness_snapshot(realm: str, sites: dict[str, bool] | None = None) -> dict[str, bool]:
    """Best-effort {coeffect_key: alive} for the coeffect keys. Never raises.

    Args:
        realm: session realm (``local:<session_id>``); reserved for per-realm
            probing (e.g. bound git_root) — today all probes are process-wide.
        sites: session-owned ``site:<id>`` states (P1.2). Caller-supplied,
            since site liveness is per session, not process-wide.

    Returns: keys ``db``, ``faiss``, ``git``, ``internet``, ``embedder``,
    plus any ``site:`` keys passed in ``sites``.
    """
    live: dict[str, bool] = {}

    # db: can we open a connection + run a trivial statement?
    try:
        from server.db import get_connection
        conn = get_connection()
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        live["db"] = True
    except Exception:
        live["db"] = False

    # faiss: the legacy cluster-scoped vector path (ClusterNumpySearch, verdict A
    # kept it on the hot path). Probe with a real read, not just an import.
    try:
        from server.cluster_faiss import ClusterNumpySearch
        ClusterNumpySearch().get_cluster_resource_ids(["general"], limit=1)
        live["faiss"] = True
    except Exception:
        live["faiss"] = False

    # git / internet: stdlib probes (see helpers).
    live["git"] = _git_ok()
    live["internet"] = _net_ok()

    # embedder: module + deps importable (no model load — cold load is 36s and
    # a liveness probe must stay sub-second; the model itself fails soft inside
    # retrieve_chunks, which falls back to FTS-only).
    try:
        from server.router_v2 import lazy_embedder  # noqa: F401
        live["embedder"] = True
    except Exception:
        live["embedder"] = False

    for key, alive in (sites or {}).items():
        if str(key).startswith("site:"):
            live[str(key)] = bool(alive)

    return live
