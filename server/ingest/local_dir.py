"""LocalDirLoader — walk a directory tree and chunk supported files.

Covers 8+ of the 12 named targets (webclaw, scrapecraft, sherlock, vibeyard,
mapcn, design-extract, InsForge, career-ops, agentation, ...).

For each included file:
1. Detect a heading (markdown ``#``, code comment ``//`` / ``#`` / ``--``).
2. Split into sections by heading boundaries (markdown) or top-level defs (code).
3. Delegate to ``BaseLoader.chunk_text`` for token-bounded packing.

Skips node_modules, venv, .git, target/, __pycache__, lockfiles, binaries.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from .base import BaseLoader, Chunk, ChunkType

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")          # markdown
_COMMENT_HEADING_RE = re.compile(r"^\s*(//|#|--)\s*(.+)$")  # code comments


class LocalDirLoader(BaseLoader):
    source_type = "local_dir"

    def __init__(self, source_path: str, *, max_files: int | None = None) -> None:
        super().__init__(source_path)
        self.max_files = max_files

    def ingest(self) -> Iterator[Chunk]:
        root = Path(self.source_path)
        if not root.exists():
            raise FileNotFoundError(f"local dir not found: {root}")
        seen = 0
        for path in sorted(root.rglob("*")):
            if self.max_files is not None and seen >= self.max_files:
                break
            if not path.is_file():
                continue
            # honour skip dirs anywhere in the path
            if any(part in self.SKIP_DIRS or part.startswith(".git")
                   for part in path.parts):
                continue
            if not self._include_path(path):
                continue
            seen += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield from self.chunk_file(path, text)

    # ---- file-level chunking -------------------------------------------

    def chunk_file(self, path: Path, text: str) -> Iterator[Chunk]:
        """Split one file into heading-bounded sections, then token-pack."""
        is_md = path.suffix.lower() in (".md", ".markdown", ".rst")
        if is_md:
            sections = self._split_markdown(text)
        else:
            sections = self._split_code(text, path.suffix.lower())

        global_idx = 0
        for heading, body, start_line in sections:
            if not body.strip():
                continue
            ctype: ChunkType = "heading" if body.lstrip().startswith("#") and is_md else (
                self._detect_chunk_type(path.name, body.splitlines()[0]) if body.splitlines() else "section"
            )
            for c in self.chunk_text(body, chunk_type=ctype, heading=heading, start_line=start_line):
                c.chunk_index = global_idx
                global_idx += 1
                yield c

    def _split_markdown(self, text: str) -> list[tuple[str | None, str, int]]:
        """Yield (heading, body, start_line) for each markdown section.

        Body of a section includes its heading line and runs until the next
        ``#``-heading or EOF.
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

    def _split_code(self, text: str, suffix: str) -> list[tuple[str | None, str, int]]:
        """Split code on top-level def/class/fn/func boundaries.

        Falls back to a single section when no top-level defs are found.
        Comment-heading lines (// Section: ...) start a new named section.
        """
        lines = text.split("\n")
        sections: list[tuple[str | None, str, int]] = []
        top_def = re.compile(r"^(def |class |async def |pub fn |pub struct |fn |func |export (default )?function |const |interface |type )")
        cur_heading: str | None = None
        cur_start = 1
        buf: list[str] = []
        for i, ln in enumerate(lines, start=1):
            is_top = bool(top_def.match(ln))
            cm = _COMMENT_HEADING_RE.match(ln)
            named_comment = cm and not ln.strip().startswith("#!")
            if (is_top or named_comment) and buf:
                sections.append((cur_heading, "\n".join(buf), cur_start))
                if named_comment:
                    cur_heading = cm.group(2).strip() or cur_heading
                elif is_top:
                    # heading = the def/class name
                    cur_heading = ln.strip().split("(")[0].split(":")[0][:80]
                cur_start = i
                buf = [ln]
            else:
                buf.append(ln)
        if buf:
            sections.append((cur_heading, "\n".join(buf), cur_start))
        return sections