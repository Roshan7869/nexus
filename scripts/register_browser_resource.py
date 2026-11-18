"""Register obscura-browser as a NEXUS resource so routing surfaces it for browsing tasks."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.db import get_db_path  # noqa: E402

NAME = "obscura-browser"
TYPE = "cli_tool"
REPO = "nexus"
PATH = "server/browser_tool.py"
DESC = ("Agentic web browsing via Obscura: headless Rust browser over CDP with a "
        "persistent cookie jar. Navigate, read, click, type, screenshot, evaluate JS. "
        "Supports importing a logged-in session (e.g. verified Gmail) from a cookie "
        "export; the session survives browser restarts. Exposed as MCP tools "
        "browser_navigate / browser_read / browser_click / browser_type / "
        "browser_screenshot / browser_evaluate / browser_import_session.")

BODY = """# obscura-browser

Agentic browsing for NEXUS, powered by Obscura (headless Rust browser, CDP, stealth).

## What it is

`server/browser_tool.py` launches `obscura serve` on a fixed port (9333) with a
persistent storage dir (`D:/nexus-data/obscura-profile`) and drives it over CDP via
Playwright `connect_over_cdp`. One long-lived page connection is held by the NEXUS MCP
server; if the browser dies, the next tool call re-spawns it automatically (~4-5 s).

## MCP tools

| Tool | Use |
|---|---|
| `browser_status` | Is the browser up? Current URL/title? |
| `browser_navigate(url)` | Go to a URL; returns title + text preview |
| `browser_read(selector="", max_chars=8000)` | Page text, or one element's text |
| `browser_click(selector)` | Click by CSS selector |
| `browser_type(selector, text, submit=False)` | Fill a field; optionally press Enter |
| `browser_evaluate(js)` | Run JavaScript in the page, return the value |
| `browser_screenshot(path="")` | PNG to D:/nexus-data/artifacts/browser/ |
| `browser_cookies(domain="")` | Cookie NAMES only (values never leave the browser) |
| `browser_import_session(cookies_file)` | Import a logged-in session from a cookie export |
| `browser_stop` | Kill the browser process tree |

## Logged-in sessions (verified Gmail)

Obscura is headless, so the login itself happens in YOUR normal browser:

1. Log into the account in Chrome/Edge as usual.
2. Install a cookie-editor extension (e.g. EditThisCookie) and export cookies for the
   site as JSON.
3. `browser_import_session("C:/path/to/export.json")` — validated, loaded into the
   persistent jar, and the export file is deleted after success.
4. `browser_navigate("https://mail.google.com")` — the session rides the cookie jar.

Caveats: the export file is a plain-text credential until imported — do not leave it
lying around (the tool shreds it). Google may issue a one-time "verify it's you"
challenge; clear it in your normal browser and re-export if needed. Sessions expire;
re-export when tools start seeing the login page again.

## Environment

| Var | Default |
|---|---|
| `OBSCURA_BIN` | Hermes tools dir obscura.exe |
| `NEXUS_BROWSER_PORT` | 9333 |
| `NEXUS_BROWSER_PROFILE` | D:/nexus-data/obscura-profile |
| `NEXUS_BROWSER_UA` | Windows Chrome 145 (fixes the X11/Linux UA leak) |
| `NEXUS_BROWSER_STEALTH` | true |

## Notes

- Cookie jar is server-wide and persists across restarts (cookies.json in the profile
  dir). Page navigation state does NOT persist across CDP disconnects — hence the
  single long-lived connection.
- Obscura blocks loopback/RFC1918 page fetches by default (SSRF guard). Testing local
  pages requires launching `obscura serve` manually with `--allow-private-network`.
- Every tool call writes one JSON line to `D:/nexus-data/browser_traces.jsonl`
  (action, ok, ms) for the learning loop.
"""


def main() -> int:
    con = sqlite3.connect(get_db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    existing = con.execute(
        "SELECT id FROM resources WHERE name=? AND resource_type=?",
        (NAME, TYPE)).fetchone()
    tokens = max(1, len(BODY.split()))
    if existing:
        con.execute(
            "UPDATE resources SET description=?, body_text=?, token_count=?, "
            "file_path=?, updated_at=datetime('now') WHERE id=?",
            (DESC, BODY, tokens, PATH, existing["id"]))
        rid = existing["id"]
        action = "updated"
    else:
        cur = con.execute(
            """INSERT INTO resources
                   (name, resource_type, repo_name, file_path, description, body_text,
                    status, tier, token_count, times_triggered, times_correct, times_wrong)
               VALUES (?,?,?,?,?,?, 'active', 1, ?, 0, 0, 0)""",
            (NAME, TYPE, REPO, PATH, DESC, BODY, tokens))
        rid = cur.lastrowid
        action = "inserted"

    # authoritative cluster membership (general = cross-cutting tool)
    con.execute(
        "INSERT OR IGNORE INTO cluster_membership (resource_id, cluster_name, affinity) "
        "VALUES (?, 'general', 0.6)", (rid,))
    con.commit()

    # verify
    row = con.execute("SELECT name, resource_type, cluster FROM resources WHERE id=?",
                      (rid,)).fetchone()
    total = con.execute("SELECT count(*) FROM resources").fetchone()[0]
    con.close()
    print(f"{action} resource id={rid}: {row['name']} ({row['resource_type']})")
    print(f"total resources: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
