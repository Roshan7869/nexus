"""Phase 8 — Process supervision and ops dashboard (I-10).

Gap 2 topology (RC2): ownership is split by design — not a single parent tree.

  systemd user unit  →  nexus-http-daemon (:8080)     long-lived API / execution plane
  IDE / MCP host     →  nexus-mcp-enhanced (stdio)    per-session tools + harness bridge
  supervisord        →  nexus-dashboard (:8765)       ops UI only by default
  harness tools      →  in-process via harness_mcp_bridge (no separate stdio required)

Supervisor programs nexus-mcp-server / nexus-http-daemon / nexus-context-harness
keep autostart=false so they do not fight IDE/systemd ownership. STOPPED under
supervisorctl is expected, not a hard failure.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

NEXUS_ROOT = Path(__file__).resolve().parent.parent
SUPERVISORD_CONF = NEXUS_ROOT / "supervisord.conf"
SUPERVISORD_PID = NEXUS_ROOT / "run" / "supervisord.pid"
SUPERVISOR_SOCK = NEXUS_ROOT / "run" / "supervisor.sock"
VENV_PYTHON = NEXUS_ROOT / "venv" / "bin" / "python3"
SUPERVISORCTL = NEXUS_ROOT / "venv" / "bin" / "supervisorctl"
SUPERVISORD = NEXUS_ROOT / "venv" / "bin" / "supervisord"
DB_PATH = Path(os.path.expandvars(os.environ.get("NEXUS_DB_PATH", str(NEXUS_ROOT / "db" / "nexus.db"))))
SYSTEMD_HTTP_UNIT = "nexus-daemon.service"

# supervisorctl status exits non-zero when any program is not RUNNING (often 3).
# That still means the supervisor daemon answered — do not treat as "down".
_SUPERVISORCTL_UP_CODES = frozenset({0, 1, 2, 3, 4})

# Canonical topology (Gap 2). Keys match PROCESS_SPECS names + harness note.
TOPOLOGY: dict[str, Any] = {
    "version": "gap2.v1",
    "model": "split_ownership",
    "summary": (
        "HTTP=systemd, MCP=IDE stdio, harness=MCP bridge in-process, "
        "dashboard=supervisord. Supervisor STOPPED for MCP/HTTP/harness is intentional."
    ),
    "owners": {
        "nexus-http-daemon": {
            "primary": "systemd",
            "unit": SYSTEMD_HTTP_UNIT,
            "port": 8000,
            "supervisor_autostart": False,
            "required_for_gate": True,
        },
        "nexus-mcp-server": {
            "primary": "ide_stdio",
            "config": ".mcp.json → server/nexus-mcp-enhanced.py",
            "supervisor_autostart": False,
            "required_for_gate": False,
            "note": "stdio owned by Claude Code / OpenCode / host; do not dual-start under supervisor",
        },
        "nexus-context-harness": {
            "primary": "mcp_bridge",
            "module": "server/harness_mcp_bridge.py",
            "supervisor_autostart": False,
            "required_for_gate": False,
            "note": "Tools registered on main MCP; separate stdio process is optional dual path",
        },
        "nexus-dashboard": {
            "primary": "supervisor",
            "program": "nexus-dashboard",
            "port": 8765,
            "supervisor_autostart": True,
            "required_for_gate": True,
        },
        "supervisord": {
            "primary": "self",
            "required_for_gate": True,
        },
        "nexus-unified": {
            "primary": "external",
            "port": 8000,
            "required_for_gate": False,
            "note": "LLM gateway — optional, not resource-brain path",
        },
    },
}

PROCESS_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "supervisord",
        "port": 9001,
        "pattern": "supervisord -c",
        "managed_by": "self",
        "required": True,
        "supervisor_expected": None,  # the daemon itself
    },
    {
        "name": "nexus-http-daemon",
        "port": 8000,
        "pattern": "nexus_http_daemon",
        "managed_by": "systemd",
        "program": "nexus-http-daemon",
        "required": True,
        # Supervisor entry exists but autostart=false — STOPPED is OK.
        "supervisor_expected": "stopped_ok",
        "systemd_unit": SYSTEMD_HTTP_UNIT,
    },
    {
        "name": "nexus-mcp-server",
        "port": None,
        "pattern": "nexus-mcp-enhanced",
        "managed_by": "ide_stdio",
        "program": "nexus-mcp-server",
        "required": False,  # session-scoped; not a long-lived gate requirement
        "supervisor_expected": "stopped_ok",
    },
    {
        "name": "nexus-context-harness",
        "port": None,
        "pattern": "context_harness/server.py",
        "managed_by": "mcp_bridge",
        "program": "nexus-context-harness",
        "required": False,
        "supervisor_expected": "stopped_ok",
        "note": "in-process via harness_mcp_bridge on main MCP",
    },
    {
        "name": "nexus-dashboard",
        "port": 8765,
        "pattern": "dashboard/server.py",
        "managed_by": "supervisor",
        "program": "nexus-dashboard",
        "required": True,
        "supervisor_expected": "running",
    },
    {
        "name": "nexus-unified",
        "port": 8000,
        "pattern": "server/unified.py",
        "managed_by": "external",
        "required": False,
        "supervisor_expected": None,
    },
)


def get_topology() -> dict[str, Any]:
    """Static Gap 2 process topology (ownership contract)."""
    return dict(TOPOLOGY)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pgrep(pattern: str) -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return []
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _supervisorctl_reachable() -> tuple[bool, str, int]:
    """Return (reachable, stdout, returncode). Non-zero status exit can still mean up."""
    if not SUPERVISORCTL.exists():
        return False, "", -1
    try:
        result = subprocess.run(
            [str(SUPERVISORCTL), "-c", str(SUPERVISORD_CONF), "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Connected if we got program lines or known "unix://" errors are absent
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode in _SUPERVISORCTL_UP_CODES and (
            out or "no such file" not in err.lower()
        ):
            # Empty + connection refused → not up
            if not out and ("refused" in err.lower() or "no such file" in err.lower()):
                return False, out, result.returncode
            if out or result.returncode == 0:
                return True, out, result.returncode
        if out and any(
            tok in out for tok in ("RUNNING", "STOPPED", "STARTING", "FATAL", "EXITED")
        ):
            return True, out, result.returncode
        return False, out or err, result.returncode
    except Exception as exc:
        return False, str(exc), -1


def supervisord_running() -> bool:
    reachable, _, _ = _supervisorctl_reachable()
    if reachable:
        return True
    if SUPERVISORD_PID.exists():
        try:
            pid = int(SUPERVISORD_PID.read_text().strip())
            if _pid_alive(pid):
                return True
        except Exception:
            pass
    if SUPERVISOR_SOCK.exists() and _port_open(9001):
        return True
    return bool(_pgrep("supervisord -c"))


def _supervisorctl(*args: str) -> dict[str, Any]:
    cmd = [str(SUPERVISORCTL), "-c", str(SUPERVISORD_CONF), *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        # status with STOPPED programs returns non-zero — still a successful query
        query_ok = result.returncode == 0 or (
            args and args[0] == "status" and result.returncode in _SUPERVISORCTL_UP_CODES
            and (result.stdout or "").strip()
        )
        return {
            "ok": query_ok,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": -1}


def _parse_supervisor_status(stdout: str) -> list[dict[str, Any]]:
    programs = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        state = parts[1]
        pid = None
        if len(parts) >= 4 and parts[2] == "pid":
            try:
                pid = int(parts[3].rstrip(","))
            except ValueError:
                pid = None
        programs.append({"name": name, "state": state, "pid": pid})
    return programs


def get_supervisor_programs() -> list[dict[str, Any]]:
    if not supervisord_running():
        return []
    raw = _supervisorctl("status")
    if not raw["stdout"]:
        return []
    return _parse_supervisor_status(raw["stdout"])


def _is_user_systemd_unit_active(unit_name: str) -> bool:
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", unit_name],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return res.stdout.strip() == "active"
    except Exception:
        return False


def probe_process(spec: dict[str, Any]) -> dict[str, Any]:
    pids = _pgrep(spec["pattern"])
    # Avoid counting supervisorctl/docs: pattern already scoped
    port = spec.get("port")
    port_up = _port_open(port) if port else None
    systemd_active = False
    if spec.get("managed_by") == "systemd":
        unit = spec.get("systemd_unit") or SYSTEMD_HTTP_UNIT
        systemd_active = _is_user_systemd_unit_active(unit)
    running = bool(pids) or (port_up is True) or systemd_active
    status = "running" if running else "stopped"
    if running and port is not None and port_up is False:
        status = "degraded"
    return {
        "name": spec["name"],
        "status": status,
        "pids": pids,
        "port": port,
        "port_open": port_up,
        "managed_by": spec["managed_by"],
        "required": spec["required"],
        "supervisor_expected": spec.get("supervisor_expected"),
        "program": spec.get("program"),
        "note": spec.get("note"),
        "systemd_unit": spec.get("systemd_unit"),
        "systemd_active": systemd_active,
    }


def _annotate_supervisor_alignment(
    processes: list[dict[str, Any]],
    programs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name = {p["name"]: p for p in programs}
    for proc in processes:
        prog_name = proc.get("program")
        expected = proc.get("supervisor_expected")
        if not prog_name or expected is None:
            proc["supervisor_state"] = None
            proc["supervisor_aligned"] = True
            continue
        sup = by_name.get(prog_name)
        state = (sup or {}).get("state")
        proc["supervisor_state"] = state
        if expected == "stopped_ok":
            # STOPPED under supervisor is correct; RUNNING under supervisor is dual-parent risk
            proc["supervisor_aligned"] = state in (None, "STOPPED", "EXITED") or (
                state == "RUNNING" and proc["status"] == "running"
            )
            if state == "RUNNING" and proc["managed_by"] in ("ide_stdio", "systemd", "mcp_bridge"):
                proc["supervisor_note"] = (
                    "running under supervisor while primary owner is "
                    f"{proc['managed_by']} — dual-parent risk"
                )
            elif state in ("STOPPED", "EXITED", None):
                proc["supervisor_note"] = "STOPPED under supervisor is intentional"
        elif expected == "running":
            proc["supervisor_aligned"] = state == "RUNNING"
            if state != "RUNNING":
                proc["supervisor_note"] = f"expected RUNNING under supervisor, got {state}"
        else:
            proc["supervisor_aligned"] = True
    return processes


def get_process_status() -> dict[str, Any]:
    processes = [probe_process(spec) for spec in PROCESS_SPECS]
    programs = get_supervisor_programs()
    processes = _annotate_supervisor_alignment(processes, programs)

    required = [p for p in processes if p["required"]]
    required_up = sum(1 for p in required if p["status"] == "running")
    soft = [p for p in processes if not p["required"]]
    soft_up = sum(1 for p in soft if p["status"] == "running")

    # Gap 2 gate: long-lived required owners only (HTTP + dashboard + supervisord).
    # MCP/harness are session/bridge scoped and do not fail the ops gate when down.
    gate_pass = required_up == len(required) and supervisord_running()

    intentional_stopped = [
        p["name"]
        for p in processes
        if p.get("supervisor_expected") == "stopped_ok"
        and p.get("supervisor_state") in ("STOPPED", "EXITED", None)
    ]

    return {
        "issue": "I-10",
        "gap": "gap2",
        "topology": get_topology(),
        "supervisord_running": supervisord_running(),
        "supervisor_programs": programs,
        "processes": processes,
        "required_up": required_up,
        "required_total": len(required),
        "soft_up": soft_up,
        "soft_total": len(soft),
        "health_pct": round(required_up / max(len(required), 1) * 100, 1),
        "gate_pass": gate_pass,
        "intentional_supervisor_stopped": intentional_stopped,
        "notes": [
            "MCP STOPPED under supervisor is intentional (IDE owns stdio).",
            "HTTP STOPPED under supervisor is intentional (systemd owns :8080).",
            "context-harness STOPPED under supervisor is intentional (MCP bridge).",
            "Do not flip MCP/HTTP autostart=true while IDE/systemd still own them.",
        ],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _cleanup_stale_supervisor() -> None:
    if SUPERVISORD_PID.exists():
        try:
            pid = int(SUPERVISORD_PID.read_text().strip())
            if not _pid_alive(pid):
                SUPERVISORD_PID.unlink(missing_ok=True)
        except Exception:
            SUPERVISORD_PID.unlink(missing_ok=True)
    if SUPERVISOR_SOCK.exists() and not supervisord_running():
        try:
            SUPERVISOR_SOCK.unlink()
        except OSError:
            pass


def ensure_supervisord() -> dict[str, Any]:
    """Start supervisord if not running (dashboard autostarts; MCP/HTTP stay off)."""
    if supervisord_running():
        return {"action": "noop", "running": True, "message": "supervisord already up"}
    _cleanup_stale_supervisor()
    if not SUPERVISORD.exists():
        return {"action": "error", "running": False, "message": "supervisord binary missing"}
    result = subprocess.run(
        [str(SUPERVISORD), "-c", str(SUPERVISORD_CONF)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    up = False
    for _ in range(6):
        time.sleep(0.5)
        if supervisord_running():
            up = True
            break
    return {
        "action": "start",
        "running": up,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def start_program(program: str) -> dict[str, Any]:
    # Refuse to dual-start IDE/systemd owned programs unless forced.
    protected = {
        "nexus-mcp-server": "ide_stdio",
        "nexus-http-daemon": "systemd",
        "nexus-context-harness": "mcp_bridge",
    }
    force = os.environ.get("NEXUS_FORCE_SUPERVISOR_START", "").lower() in ("1", "true", "yes")
    if program in protected and not force:
        return {
            "ok": False,
            "program": program,
            "error": (
                f"{program} is owned by {protected[program]}; "
                "refusing supervisor start (set NEXUS_FORCE_SUPERVISOR_START=1 to override)"
            ),
        }
    if not supervisord_running():
        boot = ensure_supervisord()
        if not boot.get("running"):
            return {"ok": False, "program": program, "error": "supervisord failed to start"}
    return {"ok": True, "program": program, **_supervisorctl("start", program)}


def get_ops_snapshot() -> dict[str, Any]:
    """Unified ops view: processes + KG + router + harness + DB metrics."""
    snapshot: dict[str, Any] = {
        "phase": 8,
        "issue": "I-10",
        "gap2": "split_ownership",
        "processes": get_process_status(),
    }
    try:
        from server.kg_service import get_kg_status
        snapshot["knowledge_graph"] = get_kg_status()
    except Exception as exc:
        snapshot["knowledge_graph"] = {"error": str(exc)}
    try:
        from server.router_cutover import get_router_status
        snapshot["router"] = get_router_status()
    except Exception as exc:
        snapshot["router"] = {"error": str(exc)}
    try:
        from server.rl_feedback_loop import get_feedback_coverage
        snapshot["feedback"] = get_feedback_coverage()
    except Exception as exc:
        snapshot["feedback"] = {"error": str(exc)}
    try:
        from server.harness_metrics import build_harness_dashboard
        snapshot["harness"] = build_harness_dashboard("nexus")
    except Exception as exc:
        snapshot["harness"] = {"error": str(exc)}
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
        closed = conn.execute(
            "SELECT COUNT(*) FROM routing_log WHERE outcome IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        snapshot["routing"] = {
            "total_logs": total,
            "closed": closed,
            "close_rate_pct": round(closed / max(total, 1) * 100, 1),
        }
    except Exception as exc:
        snapshot["routing"] = {"error": str(exc)}
    return snapshot


def bootstrap_ops(*, start_dashboard: bool = True) -> dict[str, Any]:
    """Phase 8 bootstrap: supervisord + dashboard without touching IDE MCP / systemd HTTP."""
    steps: list[dict[str, Any]] = []
    sup = ensure_supervisord()
    steps.append({"step": "supervisord", **sup})
    if start_dashboard and sup.get("running"):
        dash = start_program("nexus-dashboard")
        steps.append({"step": "nexus-dashboard", **dash})
        time.sleep(1.0)
    status = get_process_status()
    return {
        "phase": 8,
        "issue": "I-10",
        "gap2": "split_ownership",
        "steps": steps,
        "process_status": status,
        "dashboard_url": "http://127.0.0.1:8765",
        "gate_pass": status.get("gate_pass", False),
        "topology_summary": TOPOLOGY["summary"],
    }
