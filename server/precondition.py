"""NEXUS v3 State-Aware Precondition Gating — blocks tool use when state preconditions unmet."""
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

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

class StateGates:
    def __init__(self, config_path: str = None):
        self._state: dict[StateVariable, bool] = {
            StateVariable.HAS_DB: True,
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