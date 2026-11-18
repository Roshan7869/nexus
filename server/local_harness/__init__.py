"""Local harness — Cordis-style sessions for IDEs (OpenCode-first)."""
from server.local_harness.context import NexusContext, CoeffectSpec, Inverse
from server.local_harness.session import LocalHarnessSession
from server.local_harness.loader import EntryTree, reconcile
from server.local_harness.engine import (
    AgenticHarness,
    Decision,
    EngineResult,
    LocalHarnessEngine,
    Observation,
    ToolCall,
)
from server.local_harness.fibers import FiberPool, Fiber, FiberReport

__all__ = ["NexusContext", "CoeffectSpec", "Inverse",
           "LocalHarnessSession", "EntryTree", "reconcile",
           "LocalHarnessEngine", "AgenticHarness", "Decision",
           "EngineResult", "Observation", "ToolCall",
           "FiberPool", "Fiber", "FiberReport"]
# NOTE: no imports from server.tool_session_broker / execution_plane here (lazy inside session.py/loader.py)
