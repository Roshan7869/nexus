"""NEXUS v3 State-Aware Precondition Gating — blocks tool use when state preconditions unmet."""
import json
import logging
from pathlib import Path
from typing import Callable, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

class StateVariable(Enum):
    HAS_DB = "has_db"
    HAS_AUTH = "has_auth"
    HAS_FILE = "has_file"
    HAS_BROWSER = "has_browser"
    HAS_MCP = "has_mcp"
    HAS_INTERNET = "has_internet"
    HAS_API_KEY = "has_api_key"
    HAS_GIT = "has_git"
    HAS_DOCKER = "has_docker"
    PROJECT_INITIALIZED = "project_initialized"
    HAS_TESTS = "has_tests"

@dataclass
class Precondition:
    requires: list[StateVariable]
    description: str
    block_message: str

TOOL_PRECONDITIONS: dict[str, Precondition] = {
    "query_db": Precondition(
        requires=[StateVariable.HAS_DB],
        description="DB path and schema must be known",
        block_message="Cannot query DB: no database path detected. Run /init or connect a DB first."
    ),
    "mcp_invoke": Precondition(
        requires=[StateVariable.HAS_MCP],
        description="MCP server must be running",
        block_message="Cannot invoke MCP: no MCP server available."
    ),
    "browser_action": Precondition(
        requires=[StateVariable.HAS_BROWSER],
        description="Browser session must be open",
        block_message="Cannot perform browser action: no active browser session."
    ),
    "git_ops": Precondition(
        requires=[StateVariable.HAS_GIT],
        description="Git repo must be initialized",
        block_message="Cannot perform git operations: not in a git repository."
    ),
    "docker_ops": Precondition(
        requires=[StateVariable.HAS_DOCKER],
        description="Docker must be available",
        block_message="Cannot perform docker operations: Docker not available."
    ),
    "api_call": Precondition(
        requires=[StateVariable.HAS_API_KEY, StateVariable.HAS_INTERNET],
        description="API key and internet required",
        block_message="Cannot make API call: missing API key or internet connection."
    ),
    "file_write": Precondition(
        requires=[StateVariable.PROJECT_INITIALIZED],
        description="Project must be initialized",
        block_message="Cannot write files: project not initialized. Run /init first."
    ),
}

@dataclass
class CoeffectSubscription:
    """M9: reactive subscription parallel to Precondition (never replacing it)."""
    key: str                  # "db" | "faiss" | "embedder" | ... ; "*" = all keys
    realm: str = "global:default"
    on_change: Callable[[str], None] | None = None


class StateGates:
    def __init__(self, config_path: str = None):
        self._subs: dict[str, list[CoeffectSubscription]] = {}  # M9: reactive subs
        self._state: dict[StateVariable, bool] = {            StateVariable.HAS_DB: True,
            StateVariable.HAS_AUTH: False,
            StateVariable.HAS_FILE: True,
            StateVariable.HAS_BROWSER: False,
            StateVariable.HAS_MCP: False,
            StateVariable.HAS_INTERNET: True,
            StateVariable.HAS_API_KEY: False,
            StateVariable.HAS_GIT: True,
            StateVariable.HAS_DOCKER: False,
            StateVariable.PROJECT_INITIALIZED: True,
            StateVariable.HAS_TESTS: False,
        }
        # Override from config if available
        if config_path is None:
            config_path = str(Path.home() / 'nexus/config/default.json')
        try:
            with open(config_path) as f:
                cfg = json.load(f)
                gates = cfg.get("precondition_gates", {})
                for k, v in gates.items():
                    try:
                        sv = StateVariable(k)
                        self._state[sv] = bool(v)
                    except ValueError:
                        pass
        except Exception:
            pass

    def set(self, var: StateVariable, value: bool):
        self._state[var] = value

    def get(self, var: StateVariable) -> bool:
        return self._state.get(var, False)

    def check(self, tool_name: str) -> tuple[bool, Optional[str]]:
        if tool_name not in TOOL_PRECONDITIONS:
            return True, None
        pre = TOOL_PRECONDITIONS[tool_name]
        missing = [s.value for s in pre.requires if not self.get(s)]
        if missing:
            return False, pre.block_message
        return True, None

    def snapshot(self) -> dict[str, bool]:
        return {s.value: v for s, v in self._state.items()}

    # ── M9: reactive coeffects (additive — check() semantics untouched) ──
    def subscribe(self, key: str, realm: str = "global:default",
                  on_change: Callable[[str], None] | None = None) -> None:
        """Subscribe to transitions on a coeffect key (Cordis spatial, Thm70).

        Subscribers are notified (not polled) when set_and_notify flips state.
        """
        subs = self._subs.setdefault(key, [])
        subs.append(CoeffectSubscription(key=key, realm=realm, on_change=on_change))

    def set_and_notify(self, var: StateVariable, value: bool,
                       realm: str = "global:default") -> dict[str, str]:
        """Set state and push notify trichotomy to matching subscribers.

        Returns {key: activating|deactivating|neutral}. Subscriber errors are
        isolated (logged, never raised into the gate).
        """
        from server.local_harness.context import classify_transition  # lazy: no cycle
        before = self.snapshot()
        self.set(var, value)
        after = self.snapshot()
        events = classify_transition(before, after)
        for key, notify in events.items():
            for sub in self._subs.get(key, []) + self._subs.get("*", []):
                if sub.realm in ("global:default", realm, "*") and sub.on_change:
                    try:
                        sub.on_change(notify)
                    except Exception as exc:
                        logger.debug("coeffect subscriber skipped for %s: %s", key, exc)
        return events

GLOBAL_GATES = StateGates()

def can_invoke(tool_name: str) -> tuple[bool, Optional[str]]:
    return GLOBAL_GATES.check(tool_name)

def enforce_state(state: dict[str, bool]):
    for k, v in state.items():
        try:
            sv = StateVariable(k)
            GLOBAL_GATES.set(sv, v)
        except ValueError:
            pass


# ── A4: plan-gate read-only classifier (OpenResearch plan_gate analog) ──
# A command is read-only when it shows no write intent: no write verbs, no
# redirect/pipe-to-write, no mutating flags. Conservative: unknown ⇒ False
# (requires the write coeffect). Used to derive ctx.require() sets.
_READONLY_VERBS = ("get", "list", "show", "search", "read", "view", "describe",
                   "status", "log", "diff", "check", "inspect", "query")
_WRITE_SIGNS = (">", ">>", "|", "--write", "--force", "-f ", "rm ", "del ",
                "mkdir", "mv ", "cp ", "write", "create", "delete", "update",
                "insert", "drop", "apply", "commit", "push")


def command_is_readonly(cmd: str) -> bool:
    """True only when cmd carries no write signal. Unknown ⇒ False."""
    text = (cmd or "").strip().lower()
    if not text:
        return True
    if any(s in text for s in _WRITE_SIGNS):
        return False
    first = text.split()[0]
    first = text.split()[0]
    # No blanket tool allowlist: bare "git"/"orx" may still mutate via
    # subcommands (push, exp run). Conservative: unknown ⇒ False.
    return first in _READONLY_VERBS