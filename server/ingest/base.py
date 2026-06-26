"""NEXUS ingestion — base loader + Chunk model.

Defines the Chunk dataclass (mirrors the ``ingestion_chunks`` SQLite table) and
an abstract BaseLoader that the polymorphic loaders (git_repo, local_dir,
http_doc, mcp_server, obsidian_vault, cli_tool) all subclass.

Design notes
------------
- Tokens are *estimated* as ``len(content) // 4`` (no tiktoken dependency). This
  is accurate enough for chunk-size targeting and ranking; precise counts are
  not worth a heavyweight tokenizer import in the hot ingest path.
- Chunking splits on blank-line boundaries first, then hard-cap overflows, so a
  chunk never exceeds ``MAX_TOKENS`` and prefers natural section breaks.
- ``content_hash`` is sha256 of the chunk content (truncated to 16 hex chars) —
  drives incremental ingest (only changed chunks are re-written).

Inputs:  a source path (dir / url / repo / vault)
Outputs: iterator of Chunk dataclasses ready for INSERT into ingestion_chunks
Errors:  subclasses raise FileNotFoundError / RuntimeError; BaseLoader does not
         swallow them.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Literal

# Chunk types. Wider than the original ``section, paragraph, code_block`` enum;
# the ingestion_chunks.chunk_type column is free-form TEXT (no CHECK), so this
# superset is backward-compatible.
ChunkType = Literal["heading", "section", "code", "function", "paragraph", "metadata"]

TARGET_TOKENS = 500   # soft target before splitting
MAX_TOKENS = 2000     # hard cap — never exceeded
CHARS_PER_TOKEN = 4   # estimation ratio
HASH_LEN = 16


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (chars / 4). Good enough for sizing/ranking."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def content_hash(text: str) -> str:
    """Stable content fingerprint for dedup / incremental ingest."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:HASH_LEN]


@dataclass
class Chunk:
    """One addressable unit of a source, mirroring ingestion_chunks.

    ``source_id`` and ``resource_id`` are filled by the ingestor pipeline
    (not the loader) once a source row exists and classification has linked
    the chunk to a resources row.
    """
    content: str
    chunk_type: ChunkType = "section"
    heading: str | None = None
    chunk_index: int = 0
    start_line: int | None = None
    end_line: int | None = None
    source_id: int | None = None
    resource_id: int | None = None
    token_count: int = field(default=0)
    content_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = content_hash(self.content)
        if not self.token_count:
            self.token_count = estimate_tokens(self.content)


class BaseLoader(ABC):
    """Abstract polymorphic loader. Subclasses implement ``ingest()``."""

    #: source_type key registered in LOADER_REGISTRY
    source_type: str = "base"

    #: file extensions to chunk, by language family. Skipped: lockfiles,
    #: node_modules, target/, __pycache__, .venv, .git.
    INCLUDE_EXT = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go",
        ".md", ".markdown", ".rst",
        ".toml", ".yaml", ".yml", ".json",
        ".sql", ".sh", ".bash",
    }

    SKIP_DIRS = {
        "node_modules", "__pycache__", ".venv", "venv", ".git",
        "target", "dist", "build", ".next", ".cache", ".pytest_cache",
    }

    SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                   "Cargo.lock", "go.sum", "poetry.lock", "composer.lock"}

    def __init__(self, source_path: str) -> None:
        self.source_path = source_path

    @abstractmethod
    def ingest(self) -> Iterator[Chunk]:
        """Yield Chunk objects from the source. Subclasses implement."""
        raise NotImplementedError

    # ---- shared helpers -------------------------------------------------

    @staticmethod
    def _should_skip_dir(name: str) -> bool:
        return name in BaseLoader.SKIP_DIRS or name.startswith(".git")

    @staticmethod
    def _should_include_file(name: str, suffix: str) -> bool:
        if name in BaseLoader.SKIP_FILES:
            return False
        return suffix.lower() in BaseLoader.INCLUDE_EXT

    @staticmethod
    def _detect_chunk_type(path_name: str, first_line: str) -> ChunkType:
        """Heuristic: code files → code (or function), markdown → section/heading."""
        suf = path_name.rsplit(".", 1)[-1].lower() if "." in path_name else ""
        if suf in ("py", "ts", "tsx", "js", "jsx", "rs", "go", "sql", "sh"):
            # ``def `` / ``fn `` / ``func `` at line start → function chunk
            if re.match(r"^\s*(def |fn |func |async def |export function )", first_line):
                return "function"
            return "code"
        if suf in ("toml", "yaml", "yml", "json"):
            return "metadata"
        return "section"

    def chunk_text(
        self,
        text: str,
        chunk_type: ChunkType = "section",
        heading: str | None = None,
        start_line: int = 1,
    ) -> list[Chunk]:
        """Split ``text`` into Chunk objects respecting blank-line boundaries.

        Splits on double-newlines, greedily packs paragraphs up to
        TARGET_TOKENS, hard-splits anything exceeding MAX_TOKENS. Line numbers
        are tracked from ``start_line``.
        """
        # Normalise line endings; split into paragraphs on blank lines.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = re.split(r"\n\s*\n", text)
        # Pre-compute the line offset of each paragraph for start/end_line.
        line_offsets, cursor = [], 1
        for para in paragraphs:
            line_offsets.append(cursor)
            cursor += para.count("\n") + 2  # +2 for the blank-line separator

        chunks: list[Chunk] = []
        buf: list[str] = []
        buf_tokens = 0
        buf_start = start_line
        idx = 0

        def flush(end_line: int) -> None:
            nonlocal buf, buf_tokens
            if not buf:
                return
            content = "\n\n".join(buf).strip()
            if content:
                chunks.append(Chunk(
                    content=content,
                    chunk_type=chunk_type,
                    heading=heading,
                    chunk_index=idx,  # renumbered globally by caller
                    start_line=buf_start,
                    end_line=end_line,
                ))
            buf, buf_tokens = [], 0

        for para, loff in zip(paragraphs, line_offsets):
            ptok = estimate_tokens(para)
            if ptok > MAX_TOKENS:
                # Hard-split an oversized paragraph by lines.
                flush(loff - 1)
                self._hard_split(para, chunk_type, heading, loff, chunks)
                continue
            if buf_tokens + ptok > TARGET_TOKENS and buf:
                flush(loff - 1)
                buf_start = loff
            if not buf:
                buf_start = loff
            buf.append(para)
            buf_tokens += ptok
        flush(start_line + text.count("\n"))

        # Renumber chunk_index sequentially.
        for i, c in enumerate(chunks):
            c.chunk_index = i
        return chunks

    def _hard_split(
        self,
        para: str,
        chunk_type: ChunkType,
        heading: str | None,
        start_line: int,
        out: list[Chunk],
    ) -> None:
        """Split an oversized paragraph by lines into MAX_TOKENS-sized chunks."""
        lines = para.split("\n")
        buf: list[str] = []
        tok = 0
        line_no = start_line
        i = 0
        for ln in lines:
            lt = estimate_tokens(ln)
            if tok + lt > MAX_TOKENS and buf:
                out.append(Chunk(
                    content="\n".join(buf), chunk_type=chunk_type, heading=heading,
                    chunk_index=0, start_line=line_no - len(buf), end_line=line_no - 1,
                ))
                buf, tok = [], 0
            buf.append(ln)
            tok += lt
            line_no += 1
        if buf:
            out.append(Chunk(
                content="\n".join(buf), chunk_type=chunk_type, heading=heading,
                chunk_index=0, start_line=line_no - len(buf), end_line=line_no - 1,
            ))