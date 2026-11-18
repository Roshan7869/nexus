# A01 — `server/local_harness/harness.py` (CREATE, Harness registry)

## GitNexus queries
```
context({name: "task_start"}) · query({search_query: "harness registry multi agent adapter"})
impact({target: "task_start", direction: "upstream"})
```
Used: `third_party/OpenResearch/src/local/harness/mod.rs:1-100` (trait + registry pattern), `read tool_session_broker.py:1-60`, M08-doc.

## Research-backed context
- **OpenResearch `harness/mod.rs:1-27` (primary):** *"one `Harness` trait that every coding-agent integration implements, plus the single `registry()` that every consumer iterates… Adding a harness is one new file with one `impl Harness` and one line in `registry()`."* Three capabilities, none mandatory: **detection / chat (`run_turn`) / skill-install**. This is the exact pattern — port the *shape*, not the language.
- **OpenResearch `harness/opencode.rs:1-80`:** OpenCode specifics — lazy child, event-stream → normalized parts, inline permission/question (pause-not-finish), `auth.json` provider detect, `models --verbose` list. Our M8 adapter already encodes the transport facts; A01 gives it the trait home.
- **Cordis §5.1.4 (context access):** harness is an *interchange* — resolve through one mediated type, never ad-hoc imports per IDE.
- **NEXUS RC7** (`designs/NEXUS_SYSTEM_DESIGN_PLAN_v1.md`): "No unified execution contract across IDEs — each CLI re-discovers tools." The registry IS the contract.

## Current state
No `Harness` abstraction. `ide` is a free string threaded through broker/execution_plane; OpenCode support = M8 module + env defaults. Next IDE = new if-branches everywhere.

## Blast radius: NONE (new file; M8 becomes a thin re-export in the same commit)

## Detailed pseudocode
```python
"""Harness registry — one Protocol per agent surface (cf. OpenResearch harness/mod.rs)."""
from __future__ import annotations
from typing import Protocol, runtime_checkable, Any

@dataclass
class WirePart:  # cf. OpenResearch WirePart (text/tool/prompt)
    kind: str               # "text" | "tool" | "prompt"
    text: str | None = None
    tool: str | None = None
    state: dict = field(default_factory=dict)

@dataclass
class HarnessInfo:
    id: str; name: str; installed: bool; models: list[str]

@runtime_checkable
class Harness(Protocol):
    def id(self) -> str: ...
    def name(self) -> str: ...
    def supports_chat(self) -> bool: ...
    def detect(self) -> HarnessInfo | None: ...          # detection capability
    async def run_turn(self, session, message: str) -> list[WirePart]: ...  # chat; None-capable → raise Unsupported
    def skill_shim(self) -> str | None: ...              # skill-install: shim text or None

class OpenCodeHarness:  # moved from M8; first impl
    def id(self): return "opencode"
    ... # detect(): check opencode binary + auth.json providers (mirror harness/opencode.rs:75-80 logic)
    ... # run_turn(): existing session-payload path; streaming later (A2)

REGISTRY: list[Harness] = [OpenCodeHarness()]   # ONE line per future harness (their mod.rs:16-18 rule)

def get(ide: str) -> Harness:
    ide = (ide or "opencode").lower()
    for h in REGISTRY:
        if h.id() == ide: return h
    return REGISTRY[0]  # default OpenCode; never raise on unknown (compat with _sanitize_ide)
```

## Build steps
1. Write file (stdlib + dataclasses only); move M8 bodies here; M8 re-exports (`from server.local_harness.harness import OpenCodeHarness, get …`).
2. Prove the rule: add a 10-line stub harness in TESTS ONLY, assert `get()` picks it with zero dispatcher edits.
3. M5/M6 call `get(ide)` instead of string compares (narrow follow-up commit).

## Tests
`test_registry_default`, `test_one_file_add_no_dispatcher_edit`, `test_unknown_ide_falls_back`, `test_wirepart_shape`.
