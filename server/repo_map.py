"""repo_map v0 — a ranked, token-budgeted map of the project's symbols.

Aider-style insight (aider.chat/docs/repomap.html): the ranking matters more
than the parser. v0 uses regex def extraction + cross-file mention counting —
zero dependencies, deterministic, seconds on 2 cores. Upgrade path: tree-sitter
grammars (base package already in venv) when polyglot repos need real ASTs;
the public interface `build()` must not change when that lands.

Returned map is injected into context (project_resume, the future assemble()
seam) so any IDE's agent sees the project's shape without reading every file.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".gitnexus", ".nexus", "inbox", ".done",
}
SCAN_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".sql"}
MAX_FILES = 2000
MAX_FILE_BYTES = 500_000
CHARS_PER_TOKEN = 4
LINE_SLACK = 200  # one partially-fit line allowed over budget

# (pattern, kind, weight) — weights bias classes/functions above bare consts
DEF_PATTERNS = [
    (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"), "def", 2),
    (re.compile(r"^\s*class\s+(\w+)"), "class", 3),
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "fn", 2),
    (re.compile(r"^\s*export\s+default\s+class\s+(\w+)"), "class", 3),
    (re.compile(r"^\s*export\s+(?:const|let|var)\s+(\w+)"), "const", 1),
    (re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:\(|async|function)"), "const", 1),
]
_WORD_RE = re.compile(r"[A-Za-z_]\w*")


def _iter_files(root: Path):
    n = 0
    for path in sorted(root.rglob("*")):
        if n >= MAX_FILES:
            return
        if path.is_dir():
            continue
        if path.suffix.lower() not in SCAN_EXTS:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        n += 1
        yield path


def _extract(path: Path, texts: dict[Path, str], defs: list[dict]):
    try:
        text = path.read_text(errors="replace")[:MAX_FILE_BYTES]
    except OSError:
        return
    texts[path] = text
    for i, line in enumerate(text.splitlines(), 1):
        for pat, kind, weight in DEF_PATTERNS:
            m = pat.match(line)
            if m:
                defs.append({"name": m.group(1), "kind": kind, "weight": weight,
                             "path": path, "line": i})
                break


def _count_identifiers(texts: dict[Path, str]) -> Counter:
    """One pass over the corpus → identifier counts. O(text), not O(defs×text)."""
    counts: Counter = Counter()
    for text in texts.values():
        counts.update(_WORD_RE.findall(text))
    return counts


def _rank_by_file_graph(defs: list[dict], texts: dict[Path, str], counts: Counter) -> None:
    """Aider-style file-graph PageRank: a symbol ranks by its FILE's rank.

    Raw mention counts drown in generic helper names (conn, str, format —
    defined in many files). Instead: edge file→file when A mentions an
    identifier DEFINED in B; PageRank over that graph; symbol score =
    file rank × kind weight. Files imported everywhere become hubs; their
    definitions surface first.
    """
    def_sites: dict[str, set[Path]] = {}
    for d in defs:
        def_sites.setdefault(d["name"], set()).add(d["path"])

    files = sorted(texts)
    idx = {p: i for i, p in enumerate(files)}
    edges: dict[int, Counter] = {i: Counter() for i in range(len(files))}
    for path, text in texts.items():
        src = idx[path]
        for ident in set(_WORD_RE.findall(text)):
            for dst_path in def_sites.get(ident, ()):
                if dst_path != path:
                    edges[src][idx[dst_path]] += 1

    n = len(files)
    rank = [1.0 / n] * n
    out_sum = [sum(c.values()) or 1 for c in edges.values()]
    for _ in range(15):
        new = [0.15 / n] * n
        for s, targets in edges.items():
            share = 0.85 * rank[s] / out_sum[s]
            for dst, w in targets.items():
                new[dst] += share * w
        rank = new

    file_rank = {p: rank[i] for p, i in idx.items()}
    for d in defs:
        d["refs"] = counts[d["name"]]
        d["score"] = file_rank[d["path"]] * d["weight"]


def build(git_root: str, budget_tokens: int = 1000) -> dict:
    """Rank symbols by (kind weight × mentions) and render within budget."""
    root = Path(git_root)
    if not root.is_dir():
        return {"map": "", "defs": 0, "files": 0, "truncated": False}

    texts: dict[Path, str] = {}
    defs: list[dict] = []
    files = set()
    for path in _iter_files(root):
        files.add(path)
        _extract(path, texts, defs)

    if not defs:
        return {"map": "", "defs": 0, "files": len(files), "truncated": False}

    counts = _count_identifiers(texts)
    _rank_by_file_graph(defs, texts, counts)
    defs.sort(key=lambda d: (-d["score"], d["path"].name, d["name"]))

    line_slack = min(LINE_SLACK, budget_tokens * CHARS_PER_TOKEN)

    budget_chars = budget_tokens * CHARS_PER_TOKEN
    lines: list[str] = []
    used = 0
    truncated = False
    last_path = None
    for d in defs:
        rel = str(d["path"].relative_to(root))
        if rel != last_path:
            entry = f"\n{rel}:" if lines else f"{rel}:"
            lines.append(entry)
            used += len(entry)
            last_path = rel
        line = f"  {d['kind']} {d['name']}  (refs {d['refs']})"
        if used + len(line) > budget_chars + line_slack:
            truncated = True
            break
        lines.append(line)
        used += len(line)

    return {
        "map": "\n".join(lines),
        "defs": len(defs),
        "files": len(files),
        "truncated": truncated,
    }
