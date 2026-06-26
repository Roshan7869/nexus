"""ObsidianLoader — walk an Obsidian vault and chunk ``*.md`` files.

Walks ``source_path`` recursively for ``.md`` / ``.markdown`` files, honours
``BaseLoader.SKIP_DIRS`` and skips dot-directories. Each file is split into
heading-bounded sections (``#`` ATX headings) and token-packed via
``BaseLoader.chunk_text``.

While ingesting, Obsidian ``[[wikilinks]]`` are parsed and collected so the
ingestor pipeline can later build ``resource_edges``. This loader only
*collects* links; it does not write to the graph.

    >>> from server.ingest.obsidian_vault import ObsidianLoader
    >>> loader = ObsidianLoader("/home/roshan/wiki")
    >>> chunks = list(loader.ingest())
    >>> loader.links[0]            # (source_relpath, target_page, "wikilink")
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

from .base import BaseLoader, Chunk

# Obsidian wikilink: [[Target]] or [[Target|alias]]; target name captured.
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# ATX markdown heading: ``# Heading`` (1-6 hashes) — no ``#`` inline.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def strip_wikilink(text: str) -> list[str]:
    """Return target page names from all ``[[wikilinks]]`` in ``text``.

    Aliases (``[[Target|alias]]``) are dropped; only the target is kept.
    Whitespace around the target is stripped. Duplicates are preserved so the
    caller can weight by frequency if desired.

    >>> strip_wikilink('see [[Foo Bar]] and [[Baz|alias]] here')
    ['Foo Bar', 'Baz']
    """
    if not text:
        return []
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(text)]


class ObsidianLoader(BaseLoader):
    """Recursively chunk an Obsidian vault and collect inter-note wikilinks."""

    source_type = "obsidian_vault"

    #: extensions treated as Obsidian notes.
    NOTE_EXT = (".md", ".markdown")

    def __init__(self, source_path: str) -> None:
        super().__init__(source_path)
        # (source_md_relpath, target_page_name, 'wikilink')
        self.links: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------ API

    def links_for(self) -> list[tuple[str, str, str]]:
        """Return collected wikilink triples (relpath, target, 'wikilink')."""
        return self.links

    @staticmethod
    def strip_wikilink(text: str) -> list[str]:
        """Static alias for :func:`strip_wikilink`."""
        return strip_wikilink(text)

    # ------------------------------------------------------------ ingestion

    def ingest(self) -> Iterator[Chunk]:
        """Yield :class:`Chunk` objects for every note under ``source_path``."""
        root = Path(self.source_path)
        if not root.exists():
            raise FileNotFoundError(f"obsidian vault not found: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root}")

        for path in sorted(self._walk_notes(root)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relpath = os.path.relpath(str(path), str(root))
            self._collect_links(text, relpath)
            yield from self._chunk_file(path, text)

    def _walk_notes(self, root: Path) -> Iterator[Path]:
        """Yield note files, skipping SKIP_DIRS and dot-directories."""
        for dirpath, dirnames, filenames in os.walk(root):
            # prune skipped / dot directories in-place so os.walk descends no further
            dirnames[:] = sorted(
                d for d in dirnames
                if not self._should_skip_dir(d) and not d.startswith(".")
            )
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() not in self.NOTE_EXT:
                    continue
                yield Path(dirpath) / name

    def _chunk_file(self, path: Path, text: str) -> Iterator[Chunk]:
        """Split one note into heading-bounded sections, then token-pack."""
        global_idx = 0
        sections = self._split_markdown(text)
        for heading, body, start_line in sections:
            if not body.strip():
                continue
            # If the section body opens with a ``#`` heading line, prefer the
            # explicit heading text from that line over the inherited one.
            resolved = heading
            if body.lstrip().startswith("#"):
                first_line = body.lstrip().splitlines()[0]
                m = _HEADING_RE.match(first_line)
                if m:
                    resolved = m.group(2).strip() or heading
            for c in self.chunk_text(
                body, chunk_type="section", heading=resolved, start_line=start_line
            ):
                c.chunk_index = global_idx
                global_idx += 1
                yield c

    def _collect_links(self, text: str, relpath: str) -> None:
        """Append (relpath, target, 'wikilink') triples for this note."""
        for target in strip_wikilink(text):
            if target:
                self.links.append((relpath, target, "wikilink"))

    # ----------------------------------------------------- section splitter

    def _split_markdown(self, text: str) -> list[tuple[str | None, str, int]]:
        """Split ``text`` into ``(heading, body, start_line)`` sections.

        Body of a section includes its heading line and runs until the next
        ATX ``#`` heading or EOF. Matches ``LocalDirLoader._split_markdown``
        semantics so this loader is self-contained (no reliance on a sibling
        loader's private API).
        """
        lines = text.split("\n")
        sections: list[tuple[str | None, str, int]] = []
        cur_heading: str | None = None
        cur_start = 1
        buf: list[str] = []
        for i, ln in enumerate(lines, start=1):
            m = _HEADING_RE.match(ln)
            if m:
                if buf:
                    sections.append((cur_heading, "\n".join(buf), cur_start))
                cur_heading = m.group(2).strip()
                cur_start = i
                buf = [ln]
            else:
                buf.append(ln)
        if buf:
            sections.append((cur_heading, "\n".join(buf), cur_start))
        return sections