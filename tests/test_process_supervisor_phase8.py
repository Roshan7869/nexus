#!/usr/bin/env python3
"""Phase 8 / Gap 2: Process supervision and topology tests."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.process_supervisor import (
    get_process_status,
    get_ops_snapshot,
    get_topology,
    bootstrap_ops,
    probe_process,
    start_program,
    PROCESS_SPECS,
    TOPOLOGY,
    _port_open,
    _parse_supervisor_status,
)


class TestProcessProbe(unittest.TestCase):
    @patch("server.process_supervisor._pgrep", return_value=[1234])
    @patch("server.process_supervisor._port_open", return_value=True)
    def test_running_process(self, _port, _pgrep):
        spec = next(s for s in PROCESS_SPECS if s["name"] == "nexus-dashboard")
        result = probe_process(spec)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["pids"], [1234])
        self.assertEqual(result["managed_by"], "supervisor")

    @patch("server.process_supervisor._pgrep", return_value=[])
    @patch("server.process_supervisor._port_open", return_value=False)
    def test_stopped_process(self, _port, _pgrep):
        spec = next(s for s in PROCESS_SPECS if s["name"] == "nexus-dashboard")
        result = probe_process(spec)
        self.assertEqual(result["status"], "stopped")


class TestTopology(unittest.TestCase):
    def test_topology_contract(self):
        t = get_topology()
        self.assertEqual(t["version"], "gap2.v1")
        self.assertEqual(t["model"], "split_ownership")
        owners = t["owners"]
        self.assertEqual(owners["nexus-http-daemon"]["primary"], "systemd")
        self.assertEqual(owners["nexus-mcp-server"]["primary"], "ide_stdio")
        self.assertEqual(owners["nexus-context-harness"]["primary"], "mcp_bridge")
        self.assertEqual(owners["nexus-dashboard"]["primary"], "supervisor")
        self.assertFalse(owners["nexus-mcp-server"]["required_for_gate"])
        self.assertTrue(owners["nexus-http-daemon"]["required_for_gate"])

    def test_mcp_not_required(self):
        mcp = next(s for s in PROCESS_SPECS if s["name"] == "nexus-mcp-server")
        self.assertFalse(mcp["required"])
        self.assertEqual(mcp["managed_by"], "ide_stdio")
        self.assertEqual(mcp["supervisor_expected"], "stopped_ok")

    def test_http_systemd_owned(self):
        http = next(s for s in PROCESS_SPECS if s["name"] == "nexus-http-daemon")
        self.assertEqual(http["managed_by"], "systemd")
        self.assertEqual(http["supervisor_expected"], "stopped_ok")
        self.assertTrue(http["required"])

    def test_harness_bridge_owned(self):
        h = next(s for s in PROCESS_SPECS if s["name"] == "nexus-context-harness")
        self.assertEqual(h["managed_by"], "mcp_bridge")
        self.assertFalse(h["required"])


class TestSupervisorStatusParse(unittest.TestCase):
    def test_parse_mixed_states(self):
        raw = (
            "nexus-context-harness            STOPPED   Not started\n"
            "nexus-dashboard                  RUNNING   pid 94859, uptime 1:00:00\n"
            "nexus-http-daemon                STOPPED   Not started\n"
            "nexus-mcp-server                 STOPPED   Not started\n"
        )
        programs = _parse_supervisor_status(raw)
        by = {p["name"]: p for p in programs}
        self.assertEqual(by["nexus-dashboard"]["state"], "RUNNING")
        self.assertEqual(by["nexus-dashboard"]["pid"], 94859)
        self.assertEqual(by["nexus-mcp-server"]["state"], "STOPPED")


class TestProcessStatus(unittest.TestCase):
    def _probe_side_effect(self, *args, **kwargs):
        # probe_process is patched as a whole in some tests; here we build status via mocks
        pass

    @patch("server.process_supervisor.supervisord_running", return_value=True)
    @patch("server.process_supervisor.get_supervisor_programs")
    @patch("server.process_supervisor.probe_process")
    def test_gate_pass_without_mcp(self, mock_probe, mock_progs, _sup):
        """Gap 2: MCP down does not fail gate if HTTP + dashboard + supervisord up."""
        mock_progs.return_value = [
            {"name": "nexus-dashboard", "state": "RUNNING", "pid": 1},
            {"name": "nexus-mcp-server", "state": "STOPPED", "pid": None},
            {"name": "nexus-http-daemon", "state": "STOPPED", "pid": None},
            {"name": "nexus-context-harness", "state": "STOPPED", "pid": None},
        ]

        def probe(spec):
            name = spec["name"]
            running = name in ("supervisord", "nexus-http-daemon", "nexus-dashboard")
            return {
                "name": name,
                "status": "running" if running else "stopped",
                "pids": [1] if running else [],
                "port": spec.get("port"),
                "port_open": True if running and spec.get("port") else (
                    False if spec.get("port") else None
                ),
                "managed_by": spec["managed_by"],
                "required": spec["required"],
                "supervisor_expected": spec.get("supervisor_expected"),
                "program": spec.get("program"),
                "note": spec.get("note"),
                "systemd_unit": spec.get("systemd_unit"),
            }

        mock_probe.side_effect = probe
        status = get_process_status()
        self.assertTrue(status["gate_pass"])
        self.assertEqual(status["gap"], "gap2")
        self.assertIn("topology", status)
        self.assertIn("nexus-mcp-server", status["intentional_supervisor_stopped"])
        # MCP not in required count
        self.assertEqual(status["required_total"], 3)  # supervisord, http, dashboard

    @patch("server.process_supervisor.supervisord_running", return_value=False)
    @patch("server.process_supervisor.get_supervisor_programs", return_value=[])
    @patch("server.process_supervisor.probe_process")
    def test_status_shape(self, mock_probe, _progs, _sup):
        mock_probe.side_effect = [
            {
                "name": s["name"],
                "status": "stopped",
                "required": s["required"],
                "pids": [],
                "port": s.get("port"),
                "port_open": False if s.get("port") else None,
                "managed_by": s["managed_by"],
                "supervisor_expected": s.get("supervisor_expected"),
                "program": s.get("program"),
                "note": s.get("note"),
                "systemd_unit": s.get("systemd_unit"),
            }
            for s in PROCESS_SPECS
        ]
        status = get_process_status()
        self.assertEqual(status["issue"], "I-10")
        self.assertIn("health_pct", status)
        self.assertIn("processes", status)
        self.assertFalse(status["gate_pass"])


class TestStartProgramGuard(unittest.TestCase):
    def test_refuses_mcp_without_force(self):
        with patch.dict("os.environ", {}, clear=False):
            # ensure force not set
            import os
            os.environ.pop("NEXUS_FORCE_SUPERVISOR_START", None)
            result = start_program("nexus-mcp-server")
            self.assertFalse(result["ok"])
            self.assertIn("ide_stdio", result["error"])

    def test_refuses_http_without_force(self):
        import os
        os.environ.pop("NEXUS_FORCE_SUPERVISOR_START", None)
        result = start_program("nexus-http-daemon")
        self.assertFalse(result["ok"])
        self.assertIn("systemd", result["error"])


class TestOpsSnapshot(unittest.TestCase):
    @patch("server.process_supervisor.get_process_status")
    @patch("server.kg_service.get_kg_status")
    @patch("server.router_cutover.get_router_status")
    @patch("server.rl_feedback_loop.get_feedback_coverage")
    @patch("server.harness_metrics.build_harness_dashboard")
    def test_ops_keys(self, mock_h, mock_f, mock_r, mock_k, mock_p):
        mock_p.return_value = {"processes": []}
        mock_k.return_value = {"total_edges": 100}
        mock_r.return_value = {"mode": "unified_v5"}
        mock_f.return_value = {"routing_coverage_pct": 50}
        mock_h.return_value = {"summary": {"ftsr": 80}}
        snap = get_ops_snapshot()
        for key in ("processes", "knowledge_graph", "router", "feedback", "harness"):
            self.assertIn(key, snap)
        self.assertEqual(snap["gap2"], "split_ownership")


class TestBootstrap(unittest.TestCase):
    @patch("server.process_supervisor.get_process_status")
    @patch("server.process_supervisor.start_program")
    @patch("server.process_supervisor.ensure_supervisord")
    def test_bootstrap_flow(self, mock_ensure, mock_start, mock_status):
        mock_ensure.return_value = {"action": "start", "running": True}
        mock_start.return_value = {"ok": True}
        mock_status.return_value = {"gate_pass": True, "required_up": 3}
        result = bootstrap_ops(start_dashboard=True)
        self.assertEqual(result["phase"], 8)
        mock_ensure.assert_called_once()
        mock_start.assert_called_once_with("nexus-dashboard")
        self.assertIn("topology_summary", result)


class TestPortOpen(unittest.TestCase):
    def test_localhost_refused_is_false(self):
        self.assertFalse(_port_open(59999))


class TestMCPJSON(unittest.TestCase):
    @patch("server.process_supervisor.probe_process")
    @patch("server.process_supervisor.supervisord_running", return_value=True)
    @patch("server.process_supervisor.get_supervisor_programs", return_value=[])
    def test_serializable(self, _prog, _sup, mock_probe):
        mock_probe.return_value = {
            "name": "x", "status": "running", "pids": [1],
            "port": 8765, "port_open": True, "managed_by": "supervisor",
            "required": True, "supervisor_expected": "running",
            "program": None, "note": None, "systemd_unit": None,
        }
        with patch(
            "server.process_supervisor.PROCESS_SPECS",
            ({"name": "x", "pattern": "x", "port": 8765, "managed_by": "s", "required": True},),
        ):
            payload = get_process_status()
        text = json.dumps(payload, default=str)
        self.assertIn("I-10", text)
        self.assertIn("gap2", text)


class TestSupervisordRunningRc(unittest.TestCase):
    """supervisorctl status returns 3 when programs are STOPPED — still means up."""

    @patch("server.process_supervisor.SUPERVISORCTL")
    @patch("subprocess.run")
    def test_rc3_with_status_output_is_up(self, mock_run, mock_ctl):
        mock_ctl.exists.return_value = True
        # Path.exists is on the Path object — patch the module-level check differently
        from server import process_supervisor as ps

        mock_run.return_value = type(
            "R",
            (),
            {
                "returncode": 3,
                "stdout": "nexus-dashboard RUNNING pid 1\nnexus-mcp-server STOPPED Not started\n",
                "stderr": "",
            },
        )()
        with patch.object(ps.SUPERVISORCTL, "exists", return_value=True):
            ok, out, rc = ps._supervisorctl_reachable()
        self.assertTrue(ok)
        self.assertEqual(rc, 3)
        self.assertIn("RUNNING", out)


if __name__ == "__main__":
    unittest.main()
