"""Importable shim for the hyphenated ``server/nexus-mcp-enhanced.py``.

``pyproject.toml`` declares ``nexus-mcp = "server.nexus_mcp_enhanced:main"``, but:

* the file is named ``nexus-mcp-enhanced.py`` — hyphens are not importable as a
  module path, so ``server.nexus_mcp_enhanced`` did not exist; and
* the target file defines no ``main()`` — it only ran ``mcp.run()`` under
  ``if __name__ == "__main__"``.

Both made ``pip install -e .`` produce a dead ``nexus-mcp`` console script. This
shim loads the real file by path and exposes ``main()``, so the entry point and
``python -m server.nexus_mcp_enhanced`` both work.

Kept intentionally thin: the target module is loaded lazily inside ``main()`` so
importing this shim never requires ``fastmcp`` at import time.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TARGET = Path(__file__).with_name("nexus-mcp-enhanced.py")
_MODULE_NAME = "server._nexus_mcp_enhanced_impl"


def load_impl():
    """Load the hyphenated implementation module (cached in ``sys.modules``)."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    if not _TARGET.is_file():
        raise FileNotFoundError(f"MCP implementation not found: {_TARGET}")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _TARGET)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {_TARGET}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """Console-script / ``python -m`` entry point: run the MCP server on stdio."""
    module = load_impl()
    module.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
