"""McpServerLoader — represent an MCP server's tool surface as chunks.

Each tool exposed by an MCP server is captured as a single ``metadata``
chunk: the tool name becomes the heading and the body combines the
description and JSON-encoded input schema. This lets the orchestrator
index a server's tool surface as ordinary searchable content.

Two construction paths:
- ``McpServerLoader(source_path)`` — ``source_path`` is a human label
  for the server; ``ingest()`` yields nothing until tools are supplied
  via :meth:`set_tools`.
- ``McpServerLoader.from_tools_json(name, tools_json)`` — convenience
  constructor that accepts a JSON string *or* a Python list of tool
  dicts (``{name, description, inputSchema}``).
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from .base import BaseLoader, Chunk


class McpServerLoader(BaseLoader):
    """Loader representing a MCP server's tool catalogue."""

    source_type = "mcp_server"

    def __init__(self, source_path: str) -> None:
        super().__init__(source_path)
        # ``source_path`` doubles as the server's human label/name.
        self.name: str = source_path
        # Stored tool descriptors — list[dict[str, Any]].
        self.tools: list[dict[str, Any]] = []

    # -- alternate construction --------------------------------------------

    @classmethod
    def from_tools_json(cls, name: str, tools_json: Any) -> "McpServerLoader":
        """Build a loader from a JSON string or a Python list of tool dicts.

        Parameters
        ----------
        name:
            Human label for the MCP server.
        tools_json:
            Either a JSON string or a Python list of objects shaped like
            ``{"name": str, "description": str, "inputSchema": dict}``.

        Returns
        -------
        McpServerLoader
            A loader populated with the parsed tools, ready for ``ingest()``.
        """
        loader = cls(name)
        loader.set_tools(tools_json)
        return loader

    def set_tools(self, tools_json: Any) -> None:
        """Store tool descriptors from a JSON string or Python list."""
        if isinstance(tools_json, str):
            text = tools_json.strip()
            if not text:
                self.tools = []
                return
            parsed = json.loads(text)
        else:
            parsed = tools_json
        if not isinstance(parsed, list):
            raise TypeError(
                f"MCP tools payload must be a list, got {type(parsed).__name__}"
            )
        normalised: list[dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                raise TypeError(
                    f"tool descriptor must be a dict, got {type(entry).__name__}"
                )
            normalised.append({
                "name": str(entry.get("name", "")).strip(),
                "description": str(entry.get("description", "")).strip(),
                "inputSchema": entry.get("inputSchema", {}),
            })
        self.tools = normalised

    # -- inspection --------------------------------------------------------

    def list_tools_names(self) -> list[str]:
        """Return the names of all stored tools, in registration order."""
        return [t["name"] for t in self.tools if t.get("name")]

    # -- ingestion ---------------------------------------------------------

    def ingest(self) -> Iterator[Chunk]:
        """Yield one ``metadata`` chunk per stored tool."""
        for tool in self.tools:
            name = tool.get("name", "")
            description = tool.get("description", "")
            schema = tool.get("inputSchema", {})
            content = (
                f"{name}\n\n"
                f"{description}\n\n"
                f"Input schema: {json.dumps(schema, default=str)}"
            )
            chunks = self.chunk_text(
                content,
                chunk_type="metadata",
                heading=name or None,
            )
            for chunk in chunks:
                yield chunk


__all__ = ["McpServerLoader"]