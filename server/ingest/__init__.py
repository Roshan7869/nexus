"""NEXUS ingestion — loader registry.

Maps a ``source_type`` to a loader class. Loaders are imported lazily so that
importing ``server.ingest`` costs nothing (no git/network/embedder touched).

    from server.ingest import get_loader
    loader = get_loader("local_dir", "/home/roshan/webclaw")
    chunks = list(loader.ingest())

Registered source_types: git_repo, local_dir, http_doc, mcp_server,
obsidian_vault, cli_tool.
"""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseLoader

# Lazy factory map: source_type -> (callable returning loader class)
# Imported on demand to keep the package import-time cheap.
_FACTORIES: dict[str, Callable[[], type["BaseLoader"]]] = {
    "local_dir": lambda: __import__("server.ingest.local_dir", fromlist=["LocalDirLoader"]).LocalDirLoader,
    "git_repo": lambda: __import__("server.ingest.git_repo", fromlist=["GitRepoLoader"]).GitRepoLoader,
    "http_doc": lambda: __import__("server.ingest.http_doc", fromlist=["HttpDocLoader"]).HttpDocLoader,
    "mcp_server": lambda: __import__("server.ingest.mcp_server", fromlist=["McpServerLoader"]).McpServerLoader,
    "obsidian_vault": lambda: __import__("server.ingest.obsidian_vault", fromlist=["ObsidianLoader"]).ObsidianLoader,
    "cli_tool": lambda: __import__("server.ingest.cli_tool", fromlist=["CliToolLoader"]).CliToolLoader,
}

#: the 12 named targets -> source_type mapping (high-level plan §4.4)
TARGET_MAP: dict[str, str] = {
    "open-design": "local_dir",
    "webclaw": "local_dir",
    "scrapecraft": "local_dir",
    "Scrapling": "local_dir",
    "sherlock": "local_dir",
    "vibeyard": "local_dir",
    "mapcn": "local_dir",
    "design-extract": "local_dir",
    "InsForge": "local_dir",
    "career-ops": "local_dir",
    "agentation": "local_dir",
    "gcode-harness": "git_repo",
    "llm-wiki": "obsidian_vault",
    "Obsidian": "obsidian_vault",
    "wiki": "obsidian_vault",
}


def get_loader(source_type: str, source_path: str) -> "BaseLoader":
    """Instantiate the loader for ``source_type`` pointed at ``source_path``."""
    if source_type not in _FACTORIES:
        raise ValueError(f"unknown source_type {source_type!r}; "
                         f"known: {sorted(_FACTORIES)}")
    cls = _FACTORIES[source_type]()
    return cls(source_path)


def registered_types() -> list[str]:
    return sorted(_FACTORIES)


__all__ = ["get_loader", "registered_types", "TARGET_MAP", "BaseLoader"]