"""NEXUS browser capability: drive Obscura (headless Rust browser) over CDP.

Why this module exists
----------------------
NEXUS routes tasks to skills/agents, but "open this site, read it, click that"
had nothing to route TO. Obscura (bundled with Hermes at
``AppData/Local/hermes/tools/obscura``) is a headless Rust browser exposing a
CDP endpoint; Playwright's ``connect_over_cdp`` drives it like any Chromium.

Measured behaviour that shaped this design (probed on 2026-11-18):

* ``--storage-dir`` is a **server-wide persistent cookie jar** (cookies.json).
  Cookies survive a full parent+worker restart. This is what makes a logged-in
  Gmail session possible at all.
* **Page state does NOT survive a CDP disconnect** — a second connection starts
  at ``about:blank``. So this module holds ONE long-lived connection (the MCP
  server process is long-lived) and reconnects lazily if it drops.
* ``--stealth`` gives a consistent fingerprint; the default UA leaks
  ``X11; Linux x86_64`` on Windows, so we override it with a matching
  Windows Chrome UA (version-aligned with the server's Chrome/145 report).

Security rules baked in:

* ``import_session()`` takes a FILE PATH to a cookie export (EditThisCookie or
  plain JSON list) — cookie VALUES are never accepted as tool arguments and
  never logged.
* ``cookies()`` returns names/domains/expiry only, never values, so a logged-in
  session cannot be dumped into a chat context.
* Obscura blocks loopback/RFC1918 page fetches by default (SSRF guard); this
  module does not pass ``--allow-private-network``.

Env knobs:
    OBSCURA_BIN            binary path (default: Hermes tools dir)
    NEXUS_BROWSER_PORT     CDP port (default 9333)
    NEXUS_BROWSER_PROFILE  storage dir (default D:\\nexus-data\\obscura-profile)
    NEXUS_BROWSER_UA       user agent (default: Windows Chrome 145)
    NEXUS_BROWSER_STEALTH  "true"/"false" (default true)
    NEXUS_BROWSER_TIMEOUT  page op timeout ms (default 30000)
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

# ── configuration ──────────────────────────────────────────────────────────────

_TOOLS = Path.home() / "AppData" / "Local" / "hermes" / "tools" / "obscura" / "obscura.exe"

OBSCURA_BIN = os.environ.get("OBSCURA_BIN") or str(_TOOLS)
PORT = int(os.environ.get("NEXUS_BROWSER_PORT", "9333") or 9333)
PROFILE = os.environ.get("NEXUS_BROWSER_PROFILE") or r"D:\nexus-data\obscura-profile"
STEALTH = (os.environ.get("NEXUS_BROWSER_STEALTH", "true").lower() == "true")
TIMEOUT_MS = int(os.environ.get("NEXUS_BROWSER_TIMEOUT", "30000") or 30000)
#: Consistent with the server's self-reported Chrome/145 — fixes the X11/Linux
#: UA leak that a Windows host should never send (Google flags mismatched stacks).
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
USER_AGENT = os.environ.get("NEXUS_BROWSER_UA") or DEFAULT_UA

CDP = f"http://127.0.0.1:{PORT}"
TRACE_FILE = Path(r"D:\nexus-data\browser_traces.jsonl")

_lock = threading.RLock()
_pw = None            # playwright driver
_browser = None        # connected Browser
_page = None           # the long-lived page
_server_proc = None    # Popen of `obscura serve` if WE spawned it


# ── tracing (one JSON line per action — feeds nexus-learn later) ─────────────

def _trace(action: str, ok: bool, ms: float, detail: str = "") -> None:
    try:
        TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
               "ok": bool(ok), "ms": round(ms), "detail": detail[:200]}
        with TRACE_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # tracing must never break a tool call


# ── server lifecycle ──────────────────────────────────────────────────────────

def _server_up(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{CDP}/json/version", timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def ensure_server(start_timeout: float = 20.0) -> dict:
    """Return {running, spawned, port, profile}. Spawn detached if not up.

    The process is created with DETACHED_PROCESS + NEW_PROCESS_GROUP so it
    outlives the MCP server that spawned it. The parent pid is recorded in the
    profile dir; ``stop()`` kills the whole tree (obscura spawns a worker that
    survives a bare parent kill — learned empirically).
    """
    global _server_proc
    if _server_up():
        return {"running": True, "spawned": False, "port": PORT, "profile": PROFILE}
    if not Path(OBSCURA_BIN).is_file():
        raise RuntimeError(f"obscura binary not found: {OBSCURA_BIN}")

    Path(PROFILE).mkdir(parents=True, exist_ok=True)
    cmd = [OBSCURA_BIN, "serve", "--port", str(PORT),
           "--storage-dir", PROFILE, "--user-agent", USER_AGENT]
    if STEALTH:
        cmd.append("--stealth")

    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    _server_proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    (Path(PROFILE) / "server.pid").write_text(str(_server_proc.pid), encoding="utf-8")

    deadline = time.monotonic() + start_timeout
    while time.monotonic() < deadline:
        if _server_up(timeout=1.0):
            return {"running": True, "spawned": True, "port": PORT,
                    "pid": _server_proc.pid, "profile": PROFILE}
        if _server_proc.poll() is not None:
            raise RuntimeError(
                f"obscura serve exited immediately (rc={_server_proc.returncode}); "
                f"check the binary/port: {' '.join(cmd[:4])}...")
        time.sleep(0.2)
    raise RuntimeError(f"obscura serve did not become ready on port {PORT}")


def stop_server() -> dict:
    """Kill the obscura process TREE (parent + workers). Safe to call twice."""
    global _server_proc, _browser, _page
    killed = False
    with _lock:
        try:
            if _browser:
                _browser.close()
        except Exception:
            pass
        _page = _browser = None
        pid = None
        pid_file = Path(PROFILE) / "server.pid"
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = None
        if _server_proc is not None and _server_proc.poll() is None:
            pid = _server_proc.pid
        if pid:
            # /T = tree kill: the worker survives a bare parent kill otherwise.
            r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, text=True)
            killed = bool("SUCCESS" in (r.stdout or ""))
        _server_proc = None
    return {"stopped": killed, "port": PORT}


# ── the long-lived connection ─────────────────────────────────────────────────

def _get_page():
    """Return the singleton page, (re)connecting lazily. Caller must hold _lock."""
    global _pw, _browser, _page
    if _page is not None:
        try:
            _page.url  # cheap liveness probe
            return _page
        except Exception:
            _page = None
            try:
                _browser.close()
            except Exception:
                pass
            _browser = None
    ensure_server()
    if _pw is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
    _browser = _pw.chromium.connect_over_cdp(CDP)
    ctx = _browser.contexts[0] if _browser.contexts else None
    if ctx is None:
        # obscura always creates one context on connect; this is just belt+braces
        ctx = _browser.new_context()
    _page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return _page


def _run(action: str, fn, detail: str = "") -> dict:
    """Execute a page op under the lock, with tracing and one reconnect retry."""
    t0 = time.perf_counter()
    with _lock:
        try:
            page = _get_page()
            result = fn(page)
            ms = (time.perf_counter() - t0) * 1000
            _trace(action, True, ms, detail)
            out = {"ok": True, "action": action, "ms": round(ms)}
            if isinstance(result, dict):
                out.update(result)
            return out
        except Exception as exc:
            # one reconnect attempt: obscura may have recycled a worker
            global _page, _browser
            try:
                if _browser:
                    _browser.close()
            except Exception:
                pass
            _page = _browser = None
            try:
                page = _get_page()
                result = fn(page)
                ms = (time.perf_counter() - t0) * 1000
                _trace(action, True, ms, detail + " (retried)")
                out = {"ok": True, "action": action, "ms": round(ms), "retried": True}
                if isinstance(result, dict):
                    out.update(result)
                return out
            except Exception as exc2:
                ms = (time.perf_counter() - t0) * 1000
                _trace(action, False, ms, f"{type(exc).__name__}: {exc}"[:200])
                return {"ok": False, "action": action, "ms": round(ms),
                        "error": f"{type(exc2).__name__}: {exc2}"[:400]}


# ── operations (public) ────────────────────────────────────────────────────────

def status() -> dict:
    up = _server_up()
    out = {"server_running": up, "port": PORT, "profile": PROFILE,
           "stealth": STEALTH, "user_agent": USER_AGENT[:60] + "…"}
    with _lock:
        out["connection_alive"] = _page is not None
        if _page is not None:
            try:
                out["current_url"] = _page.url
                out["title"] = _page.title()
            except Exception:
                out["connection_alive"] = False
    if up:
        try:
            with urllib.request.urlopen(f"{CDP}/json/version", timeout=2) as r:
                out["engine"] = json.loads(r.read().decode()).get("Browser")
        except (OSError, ValueError):
            pass
    return out


def navigate(url: str) -> dict:
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    def op(page):
        page.goto(url, timeout=TIMEOUT_MS)
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        return {"url": page.url, "title": page.title(),
                "text_chars": len(text), "text_head": text[:400]}
    return _run("navigate", op, url)


def read(selector: str = "", max_chars: int = 8000) -> dict:
    def op(page):
        if selector:
            el = page.query_selector(selector)
            if el is None:
                return {"found": False, "selector": selector}
            return {"found": True, "text": el.inner_text()[:max_chars]}
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        return {"url": page.url, "title": page.title(), "text": text[:max_chars],
                "truncated": len(text) > max_chars}
    return _run("read", op, selector or "full-page")


def click(selector: str) -> dict:
    def op(page):
        el = page.query_selector(selector)
        if el is None:
            return {"found": False}
        el.click(timeout=TIMEOUT_MS)
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        return {"clicked": True, "url": page.url, "title": page.title(),
                "text_head": text[:200]}
    return _run("click", op, selector)


def type_text(selector: str, text: str, submit: bool = False) -> dict:
    def op(page):
        el = page.query_selector(selector)
        if el is None:
            return {"found": False}
        el.fill(text, timeout=TIMEOUT_MS)
        if submit:
            el.press("Enter")
            # Wait for the submission to actually navigate: without this an
            # immediate follow-up read() sees the PRE-SUBMIT page (measured on
            # hn.algolia.com — the JS search navigates a beat after Enter).
            try:
                page.wait_for_load_state("load", timeout=10000)
            except Exception:
                pass
        return {"typed": True, "submitted": bool(submit), "url": page.url,
                "title": page.title()}
    return _run("type", op, selector)


def evaluate(expression: str) -> dict:
    def op(page):
        value = page.evaluate(expression)
        return {"value": value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else str(value)}
    return _run("evaluate", op, expression[:80])


def screenshot(path: str = "") -> dict:
    default_dir = Path(r"D:\nexus-data\artifacts\browser")
    target = Path(path) if path else default_dir / f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png"
    target.parent.mkdir(parents=True, exist_ok=True)

    def op(page):
        page.screenshot(path=str(target), timeout=TIMEOUT_MS)
        return {"path": str(target), "url": page.url}
    return _run("screenshot", op, str(target))


def cookies(domain: str = "") -> dict:
    """List cookie NAMES only — values are session secrets and never leave the browser."""
    def op(page):
        ctx = page.context
        cs = ctx.cookies() if not domain else ctx.cookies(f"https://{domain}")
        return {"cookies": [{"name": c["name"], "domain": c["domain"],
                            "expires": c.get("expires")} for c in cs]}
    return _run("cookies", op, domain or "all")


# ── session import (the verified-Gmail login path) ────────────────────────────

_REQUIRED_FIELDS = ("name", "value", "domain")


def import_session(cookies_file: str) -> dict:
    """Load a cookie export into the browser's persistent jar.

    The file (NOT inline values) is the interface: the user exports cookies from
    their own logged-in browser with a cookie-editor extension (EditThisCookie
    format: list of {name, value, domain, path, expires, secure, httpOnly, …},
    or a bare JSON list of such dicts). Values are validated, never logged, and
    land in the obscura storage dir so they survive restarts.

    For Gmail, the cookies that matter are the accounts.google.com / .google.com
    auth cookies (SID, HSID, SSID, SAPISID, APISID, __Secure-…). After import,
    `browser_navigate("https://mail.google.com")` either shows the inbox or a
    Google security challenge the user clears once in their normal browser.
    """
    src = Path(cookies_file)
    if not src.is_file():
        return {"ok": False, "action": "import_session",
                "error": f"file not found: {src}"}
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"ok": False, "action": "import_session", "error": f"bad JSON: {exc}"}
    if isinstance(data, dict):  # some exporters wrap in {"cookies": [...]}
        data = data.get("cookies") or data.get("export") or []
    if not isinstance(data, list):
        return {"ok": False, "action": "import_session",
                "error": "expected a JSON list of cookie objects"}

    clean = []
    rejected = 0
    for c in data:
        if not isinstance(c, dict) or not all(str(c.get(f, "") or "") for f in _REQUIRED_FIELDS):
            rejected += 1
            continue
        cookie = {
            "name": str(c["name"]), "value": str(c["value"]),
            "domain": str(c["domain"]),
            "path": str(c.get("path") or "/"),
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", True)),
        }
        exp = c.get("expires") or c.get("expirationDate")
        if exp and isinstance(exp, (int, float)) and exp > 0:
            cookie["expires"] = int(exp)
        clean.append(cookie)
    if not clean:
        return {"ok": False, "action": "import_session",
                "error": f"0 valid cookies in {src.name} ({rejected} rejected)"}

    def op(page):
        page.context.add_cookies(clean)
        domains = sorted({c["domain"].lstrip(".") for c in clean})
        return {"imported": len(clean), "rejected": rejected, "domains": domains[:10]}
    out = _run("import_session", op, f"{len(clean)} cookies from {src.name}")
    if out.get("ok"):
        # Shred the export after a successful import — it is a plain-text
        # credential sitting on disk, and the browser jar now holds the session.
        try:
            src.unlink()
            out["export_deleted"] = True
        except OSError:
            out["export_deleted"] = False
    return out
