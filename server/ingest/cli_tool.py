"""CliToolLoader — chunk the help output of a CLI binary.

Many of NEXUS's targets (git, gh, npm, ruff, ...) expose their surface as
``--help`` text. This loader captures that text and chunks it as ``metadata``
so a binary's options/flags/commands are searchable alongside code and docs.

Resolution order on :meth:`ingest`:
1. Run ``<bin> --help`` and capture stdout (timeout 10s).
2. If that fails (non-zero return or empty stdout), fall back to
   ``man <bin>`` piped through ``col -b`` (timeout 10s).
3. If the binary itself is missing, ``subprocess`` raises ``FileNotFoundError``
   which is propagated unchanged — callers can catch it to skip the binary.

The combined text is then split into token-bounded ``metadata`` chunks via
:meth:`BaseLoader.chunk_text`.
"""
from __future__ import annotations

import subprocess
from typing import Iterator

from .base import BaseLoader, Chunk

# Seconds before a help command is killed. Help output is small; 10s is
# generous but bounded.
_TIMEOUT_SECONDS = 10


class CliToolLoader(BaseLoader):
    """Loader that chunks the ``--help`` (or ``man``) output of a binary."""

    source_type = "cli_tool"

    def __init__(self, source_path: str) -> None:
        super().__init__(source_path)
        # ``source_path`` is the binary name (e.g. "git").
        self.bin: str = source_path

    # -- helpers -----------------------------------------------------------

    def _run(self, args: list[str]) -> tuple[int, str]:
        """Run ``args`` and return (returncode, stdout).

        ``FileNotFoundError`` (binary not on PATH) propagates to the caller.
        Timeouts surface as a non-zero return with empty stdout so the caller
        can try the next fallback.
        """
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return 1, ""
        return result.returncode, (result.stdout or "")

    def _fetch_help(self) -> str:
        """Resolve the binary's help text via --help, falling back to man."""
        code, out = self._run([self.bin, "--help"])
        if code == 0 and out.strip():
            return out

        # Fall back to ``man <bin> | col -b``: render the man page and strip
        # backspace formatting so the result is plain text.
        try:
            man_proc = subprocess.Popen(
                ["man", self.bin],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            col_proc = subprocess.Popen(
                ["col", "-b"],
                stdin=man_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            # Allow man_proc.stdout to receive SIGPIPE from col_proc.
            if man_proc.stdout is not None:
                man_proc.stdout.close()
            try:
                out_bytes, _ = col_proc.communicate(timeout=_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                col_proc.kill()
                raise
            man_proc.wait(timeout=_TIMEOUT_SECONDS)
            text = out_bytes if isinstance(out_bytes, str) else (out_bytes or "")
            if text.strip():
                return text
        except FileNotFoundError:
            # man or col missing — fall through to whatever --help gave us.
            pass

        # Last resort: whatever --help produced, even if it looked empty.
        return out

    # -- ingestion ---------------------------------------------------------

    def ingest(self) -> Iterator[Chunk]:
        """Capture the binary's help text and yield ``metadata`` chunks."""
        text = self._fetch_help()
        if not text.strip():
            # Nothing useful captured — still yield nothing rather than crash.
            return
        for chunk in self.chunk_text(
            text,
            chunk_type="metadata",
            heading=self.bin,
        ):
            yield chunk


__all__ = ["CliToolLoader"]