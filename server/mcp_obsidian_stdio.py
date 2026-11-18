#!/usr/bin/env python3
"""Obsidian MCP server (stdio) for NEXUS + DSH.

Why this exists
---------------
The `obsidian-local-rest-api` plugin *is* an MCP server (v5.5.2, `/mcp`), but it only
speaks streamable-HTTP over **HTTPS with a self-signed certificate**. A generic MCP
client that verifies TLS refuses it, and disabling TLS verification process-wide is not
acceptable. This server wraps the plugin's stable **REST** surface in a local stdio MCP
server, so any MCP client works with no certificate compromise.

Tools: obsidian_status, obsidian_list, obsidian_read, obsidian_write, obsidian_append,
obsidian_search.

Run:  python -m server.mcp_obsidian_stdio
Env:  OBSIDIAN_API_URL (default https://127.0.0.1:27124)
      OBSIDIAN_API_KEY  (falls back to ~/.config/nexus/obsidian.json, then the vault's
                         plugin data.json when OBSIDIAN_VAULT is set)
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # noqa: E402

from server.integrations import obsidian_rest as O  # noqa: E402

mcp = FastMCP("Obsidian Vault")


@mcp.tool()
def obsidian_status() -> str:
    """Check the Obsidian vault connection: plugin name, version, vault root files."""
    try:
        h = O.health()
        files = O.list_root()
        return (f"status={h['status']} plugin={h['plugin']} version={h['version']} "
                f"root_files={len(files)} sample={files[:10]}")
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


@mcp.tool()
def obsidian_list(folder: str = "") -> str:
    """List files in the vault (folder optional, e.g. 'NEXUS/Skills')."""
    try:
        return str(O.list_root()) if not folder else str(O.search(folder))
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


@mcp.tool()
def obsidian_read(path: str) -> str:
    """Read a note by vault-relative path, e.g. 'NEXUS/Skills/_index.md'."""
    try:
        return O.read_note(path)
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


@mcp.tool()
def obsidian_write(path: str, content: str) -> str:
    """Create or overwrite a note. Path is vault-relative; .md is conventional."""
    try:
        return str(O.write_note(path, content))
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


@mcp.tool()
def obsidian_append(path: str, content: str) -> str:
    """Append content to a note (creates it when absent)."""
    try:
        return str(O.append_note(path, content))
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


@mcp.tool()
def obsidian_search(query: str, limit: int = 10) -> str:
    """Search the vault and return matching note paths with scores."""
    try:
        hits = O.search(query, limit=limit)
        return str(hits) if hits else f"no matches for {query!r}"
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
