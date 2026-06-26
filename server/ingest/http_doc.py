"""HttpDocLoader — fetch an HTTP/HTTPS/file URL and chunk its text.

The loader is intentionally dependency-free: it uses ``urllib`` for transport
and a small ``html.parser.HTMLParser`` subclass for tag stripping, so it works
in any Python environment without ``requests`` or ``beautifulsoup4``.

Behaviour:
- ``http://`` / ``https://`` URLs are fetched with a 15s timeout.
- ``file://`` URLs are read directly from disk.
- If the response looks like HTML (contains ``<html``, ``<body`` or ``<p>``),
  tags are stripped and block-level start tags insert a newline so the
  resulting text has natural paragraph/section breaks; blank lines are
  collapsed.
- If ``<h2>`` headers were seen, the stripped text is split into one chunk
  per ``h2``-bounded section (chunk_type ``section``, heading = the h2 text).
  Otherwise the whole text is paragraph-packed into ``section`` chunks.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterator
from urllib.parse import urlparse
from urllib.request import urlopen

from .base import BaseLoader, Chunk

# Block-level tags that should start a new line in the stripped text.
_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "p", "li", "br", "div", "tr"}

_FETCH_TIMEOUT = 15  # seconds


class _TextExtractor(HTMLParser):
    """HTMLParser subclass that emits text and breaks lines at block tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        # Captured h2 heading texts, in document order.
        self.h2_headings: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "h2":
            self.parts.append("\n__H2__")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag == "h2":
            self.parts.append("__H2__\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        self.parts.append(data)

    # ---- output ----------------------------------------------------------

    def text(self) -> str:
        """Return the fully stripped, blank-line-collapsed text."""
        joined = "".join(self.parts)
        # Normalise whitespace within lines, then collapse blank lines.
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n[ \t]*", "\n", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()

    def sections(self) -> list[tuple[str, str]]:
        """Split the stripped text on h2 markers.

        Returns a list of ``(heading, body)`` tuples. Text before the first
        h2 (if any) is returned with an empty heading.
        """
        joined = "".join(self.parts)
        # Re-collapse on the h2 markers we inserted.
        raw = re.split(r"__H2__\n?", joined)
        # raw is: [pre, h2text1, body1, h2text2, body2, ...]
        results: list[tuple[str, str]] = []
        pre = raw[0] if raw else ""
        pre = self._collapse(pre)
        if pre.strip():
            results.append(("", pre))
        # Pairs after the first element: (heading, body)
        i = 1
        while i + 1 < len(raw):
            heading = raw[i].strip()
            body = self._collapse(raw[i + 1])
            results.append((heading, body))
            i += 2
        return results

    @staticmethod
    def _collapse(text: str) -> str:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class HttpDocLoader(BaseLoader):
    """Loader for an HTTP/HTTPS/file URL rendered as text chunks."""

    source_type = "http_doc"

    def __init__(self, source_path: str) -> None:
        super().__init__(source_path)
        self.url: str = source_path

    # -- transport --------------------------------------------------------

    def _fetch(self) -> str:
        """Fetch the URL and decode its bytes as UTF-8 (errors replaced)."""
        scheme = urlparse(self.url).scheme.lower()
        if scheme == "file":
            # urlopen handles file:// on all platforms.
            with urlopen(self.url) as resp:
                data = resp.read()
        else:
            with urlopen(self.url, timeout=_FETCH_TIMEOUT) as resp:
                data = resp.read()
        return data.decode("utf-8", errors="replace")

    # -- ingestion ---------------------------------------------------------

    def ingest(self) -> Iterator[Chunk]:
        """Fetch the document, strip HTML if needed, and yield section chunks."""
        content = self._fetch()
        looks_like_html = any(
            marker in content.lower() for marker in ("<html", "<body", "<p>")
        )
        if looks_like_html:
            parser = _TextExtractor()
            parser.feed(content)
            parser.close()
            if parser.h2_headings or "__H2__" in "".join(parser.parts):
                # Section-aware chunking: one chunk per h2-bounded region.
                seen_heading = False
                for heading, body in parser.sections():
                    if not heading:
                        # Preamble — only emit if non-empty.
                        if body:
                            for c in self.chunk_text(body, chunk_type="section"):
                                yield c
                        continue
                    seen_heading = True
                    if not body:
                        continue
                    for c in self.chunk_text(body, chunk_type="section", heading=heading):
                        yield c
                if seen_heading:
                    return
            # No h2 sections emitted — paragraph-pack the whole text.
            text = parser.text()
            if text.strip():
                for c in self.chunk_text(text, chunk_type="section"):
                    yield c
            return

        # Non-HTML: chunk the raw (already text) body.
        if content.strip():
            for c in self.chunk_text(content, chunk_type="section"):
                yield c


__all__ = ["HttpDocLoader"]