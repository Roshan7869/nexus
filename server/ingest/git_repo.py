"""GitRepoLoader — clone a git remote (or reuse a local repo) and chunk it.

Polymorphic loader for ``source_type = "git_repo"``. Given a git URL
(``https://...``, ``git@...``, ``file:///...``) or a path to a local
working copy that already contains a ``.git`` directory, the loader
materialises the tree into a temporary directory and delegates the
walk-and-chunk work to :class:`LocalDirLoader`.

Design notes
------------
- Clones with ``--depth 1`` to keep bandwidth and disk usage minimal; we
  only need the working tree for chunking, not history.
- A local path containing a ``.git`` entry is treated as an already-cloned
  repo and walked in place — no copy, no clone.
- The temporary clone lives for the duration of ``ingest()`` only; the
  directory is removed when the generator is exhausted or closed.
- Failures from ``git`` surface as :class:`RuntimeError` carrying the
  captured ``stderr`` so callers can report the underlying reason.

Inputs:  ``source_path`` — a git URL or an existing local repo path.
Outputs: iterator of :class:`Chunk` ready for INSERT into ingestion_chunks.
Errors:  :class:`RuntimeError` on a non-zero ``git clone`` exit code.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

from .base import BaseLoader, Chunk
from .local_dir import LocalDirLoader


class GitRepoLoader(BaseLoader):
    """Clone-then-chunk loader for git repositories."""

    source_type = "git_repo"

    def __init__(self, source_path: str) -> None:
        """Store the repo URL or local path to ingest.

        ``source_path`` may be a remote git URL (``https://...``,
        ``git@host:...``, ``file:///...``) or a local filesystem path
        pointing at an already-cloned working copy (must contain a
        ``.git`` entry).
        """
        super().__init__(source_path)

    # ---- public API ----------------------------------------------------

    def ingest(self) -> Iterator[Chunk]:
        """Yield chunks from the repo, cloning into a temp dir if needed.

        If ``source_path`` is a local directory containing ``.git``, the
        tree is walked in place. Otherwise the repo is cloned with
        ``--depth 1`` into a :class:`tempfile.TemporaryDirectory` whose
        lifetime is bound to this generator.
        """
        local_root = self._resolve_local_root()
        if local_root is not None:
            yield from LocalDirLoader(str(local_root)).ingest()
            return

        with tempfile.TemporaryDirectory(prefix="nexus_git_") as tmp:
            dest = self._clone(self.source_path, tmp)
            yield from LocalDirLoader(dest).ingest()

    # ---- helpers -------------------------------------------------------

    def _resolve_local_root(self) -> Path | None:
        """Return the on-disk repo root when ``source_path`` is already local.

        Returns ``None`` when ``source_path`` is a remote URL (or simply not
        an existing directory with a ``.git`` entry), signalling the caller
        to perform a clone.
        """
        path = Path(self.source_path)
        if path.exists() and (path / ".git").exists():
            return path
        return None

    @staticmethod
    def _clone(source_path: str, dest_dir: str) -> str:
        """Shallow-clone ``source_path`` into ``dest_dir`` and return the path.

        Uses ``git clone --depth 1`` to fetch only the latest tree. The
        destination directory must already exist (typically a
        :class:`tempfile.TemporaryDirectory`); git refuses to clone into a
        non-empty dir, so a fresh temp dir is expected.

        Args:
            source_path: Remote git URL or local clone source.
            dest_dir: Existing empty directory to clone into.

        Returns:
            The destination path (same as ``dest_dir``).

        Raises:
            RuntimeError: if ``git`` exits non-zero; the message carries
                the captured ``stderr`` for diagnostics.
        """
        dest = os.path.join(dest_dir, "repo")
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--", source_path, dest],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"git clone failed (rc={proc.returncode}) for "
                f"{source_path!r}: {err}"
            )
        return dest