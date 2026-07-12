"""Agent Reach channel registry — 13 platforms with backend routing rules.

Each platform has:
- name: unique identifier
- description: human-readable
- tier: 0 (zero-config), 1 (login/key), 2 (complex setup)
- backends: ordered list of backend names
- status: probed via doctor --json equivalent
- probe_fn: static check for available CLI tools
"""
import shutil
import subprocess
from typing import Optional

CHANNELS: list[dict] = [
    {
        "name": "web",
        "description": "Web page reader",
        "tier": 0,
        "backends": ["jina_reader"],
        "probe": lambda: shutil.which("curl") is not None,
    },
    {
        "name": "youtube",
        "description": "YouTube subtitles and metadata",
        "tier": 0,
        "backends": ["yt-dlp"],
        "probe": lambda: shutil.which("yt-dlp") is not None,
    },
    {
        "name": "rss",
        "description": "RSS/Atom feed reader",
        "tier": 0,
        "backends": ["feedparser"],
        "probe": lambda: _import_check("feedparser"),
    },
    {
        "name": "exa_search",
        "description": "Exa AI web and code search",
        "tier": 0,
        "backends": ["mcporter"],
        "probe": lambda: shutil.which("mcporter") is not None,
    },
    {
        "name": "github",
        "description": "GitHub repository search and management",
        "tier": 0,
        "backends": ["gh"],
        "probe": lambda: shutil.which("gh") is not None,
    },
    {
        "name": "v2ex",
        "description": "V2EX community API",
        "tier": 0,
        "backends": ["http_api"],
        "probe": lambda: shutil.which("curl") is not None,
    },
    {
        "name": "twitter",
        "description": "Twitter/X social media",
        "tier": 1,
        "backends": ["twitter-cli", "OpenCLI"],
        "probe": lambda: shutil.which("twitter") is not None,
    },
    {
        "name": "bilibili",
        "description": "Bilibili video platform",
        "tier": 1,
        "backends": ["bili-cli", "OpenCLI"],
        "probe": lambda: shutil.which("bili") is not None,
    },
    {
        "name": "xiaohongshu",
        "description": "XiaoHongShu (RED) social platform",
        "tier": 1,
        "backends": ["OpenCLI", "xiaohongshu-mcp"],
        "probe": lambda: shutil.which("opencli") is not None,
    },
    {
        "name": "reddit",
        "description": "Reddit communities",
        "tier": 1,
        "backends": ["OpenCLI", "rdt-cli"],
        "probe": lambda: shutil.which("rdt") is not None or shutil.which("opencli") is not None,
    },
    {
        "name": "linkedin",
        "description": "LinkedIn professional network",
        "tier": 2,
        "backends": ["linkedin-scraper-mcp", "jina_reader"],
        "probe": lambda: False,
    },
    {
        "name": "xiaoyuzhou",
        "description": "Xiaoyuzhou FM podcast transcription",
        "tier": 1,
        "backends": ["groq-whisper"],
        "probe": lambda: shutil.which("ffmpeg") is not None,
    },
    {
        "name": "xueqiu",
        "description": "Xueqiu (Snowball) stock market social",
        "tier": 1,
        "backends": ["cookie_api"],
        "probe": lambda: shutil.which("curl") is not None,
    },
]


def _import_check(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def doctor() -> dict[str, dict]:
    """Probe all channels and return status map (like agent-reach doctor --json)."""
    result = {}
    for ch in CHANNELS:
        available = ch["probe"]()
        result[ch["name"]] = {
            "status": "ok" if available else "off",
            "name": ch["description"],
            "tier": ch["tier"],
            "backends": ch["backends"],
            "active_backend": ch["backends"][0] if available else None,
        }
    return result


def route_to_channel(query: str) -> Optional[dict]:
    """Route a natural language query to the best-matching channel."""
    query_l = query.lower()
    scores = []
    for ch in CHANNELS:
        score = 0
        name_l = ch["name"].lower()
        if name_l in query_l:
            score += 3
        desc_l = ch["description"].lower()
        desc_words = set(desc_l.split())
        query_words = set(query_l.split())
        overlap = len(desc_words & query_words)
        score += overlap
        if ch["tier"] == 0:
            score += 1
        if ch["probe"]():
            score += 2
        scores.append((score, ch))
    scores.sort(key=lambda x: -x[0])
    if scores and scores[0][0] > 0:
        return scores[0][1]
    return None
