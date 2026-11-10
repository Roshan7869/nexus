"""Gcode Harness backend — memory operations via Unix socket or CLI.

Provides an interface to gcode-harness memory graph operations.
Currently uses NEXUS learning_notes as fallback storage when
gcode binary is unavailable. Swap to live socket/CLI when
gcode-harness is built.

Gcode socket protocol: NDJSON over Unix socket at ~/.gcode/runtime/gcode.sock
Message type 'memory' to store/retrieve from the petgraph-based memory graph.
"""
import json
import socket
import subprocess
from pathlib import Path
from typing import Optional

GCODE_SOCKET = Path.home() / ".gcode/runtime/gcode.sock"
GCODE_BINARY = None  # Set when built: Path.home() / "projects/gcode-harness/target/release/gcode"


def _gcode_available() -> bool:
    return GCODE_BINARY is not None and GCODE_BINARY.exists()


def _socket_send(msg: dict) -> Optional[dict]:
    """Send a JSON message over Unix socket and read response."""
    if not GCODE_SOCKET.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(str(GCODE_SOCKET))
        sock.sendall((json.dumps(msg) + "\n").encode())
        data = sock.recv(65535)
        sock.close()
        if data:
            return json.loads(data.decode().strip().split("\n")[0])
    except (ConnectionRefusedError, FileNotFoundError, TimeoutError, OSError):
        pass
    return None


def _cli_exec(args: list[str]) -> Optional[str]:
    """Run gcode CLI command and capture output."""
    if not _gcode_available():
        return None
    try:
        result = subprocess.run(
            [str(GCODE_BINARY), *args],
            shell=False,
            timeout=10,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def search(query: str, max_results: int = 10) -> list[dict]:
    """Search gcode memory graph. Falls back to NEXUS learnings."""
    if _gcode_available():
        out = _cli_exec(["memory", "search", "--semantic", query])
        if out:
            return [{"source": "gcode-memory", "content": out, "score": 1.0, "metadata": {}}]

    from server.context_harness.nexus_bridge import search_learnings
    notes = search_learnings(query, limit=max_results)
    return [
        {"source": "learning-note", "content": n["note"], "score": 1.0, "metadata": {"id": n["id"]}}
        for n in notes
    ]


def store(key: str, value: str, tags: list[str] | None = None) -> dict:
    """Store context in gcode memory graph. Falls back to NEXUS learning_notes."""
    if _gcode_available():
        # No write path to gcode exists yet (import needs a stdin pipe we do not
        # open), so nothing is written here. Never report "stored" for this branch.
        return {
            "status": "error",
            "backend": "gcode-memory",
            "key": key,
            "error": "gcode write path not implemented; nothing was stored",
        }

    from server.context_harness.nexus_bridge import store_learning
    rid = store_learning(key, value, tags)
    return {"status": "stored", "backend": "learning-note", "id": rid, "key": key}


def stats() -> dict:
    """Get gcode memory statistics. Falls back to NEXUS counts."""
    if _gcode_available():
        out = _cli_exec(["memory", "stats"])
        if out:
            return {"source": "gcode-memory", "raw": out}

    from server.context_harness.nexus_bridge import get_conn
    conn = get_conn()
    try:
        count = conn.execute("SELECT count(*) FROM learning_notes").fetchone()[0]
        return {"source": "learning-notes", "total": count}
    finally:
        conn.close()
