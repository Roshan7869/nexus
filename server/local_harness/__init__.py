"""Local harness — Cordis-style sessions for IDEs (OpenCode-first)."""
from server.local_harness.context import NexusContext, CoeffectSpec, Inverse
from server.local_harness.session import LocalHarnessSession
from server.local_harness.loader import EntryTree, reconcile

__all__ = ["NexusContext", "CoeffectSpec", "Inverse",
           "LocalHarnessSession", "EntryTree", "reconcile"]
# NOTE: no imports from server.tool_session_broker / execution_plane here (lazy inside session.py/loader.py)
