"""NEXUS Forge — project → lead + cluster-peer team in tmux (interactive TUIs).

Decisions settled by grilling (2026-08-17): lead+peers, interactive TUI seats
(all opencode), shared project dir with disjoint territories, briefs delivered
as FILES (send-keys only types the pointer line). The lead seat is the honesty
authority — no completion without a lead-visible artifact.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/roshan/nexus")

from server.forge import plan_project, CORE_PEERS, LEAD_CLUSTER, MAX_SEATS

BRIEF = "build an ecommerce site with product catalog, cart, and checkout"


class TestPlanProject:
    def test_lead_always_present(self, tmp_path):
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        seats = {s["cluster"]: s for s in plan["seats"]}
        assert seats[LEAD_CLUSTER]["role"] == "lead"
        assert seats[LEAD_CLUSTER]["territory"] == "."  # whole repo review rights

    def test_core_peers_present(self, tmp_path):
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        clusters = {s["cluster"] for s in plan["seats"]}
        assert CORE_PEERS <= clusters

    def test_peer_territories_disjoint(self, tmp_path):
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        peer_trts = [s["territory"] for s in plan["seats"] if s["role"] == "peer"]
        assert len(peer_trts) == len(set(peer_trts))
        assert all(t not in (".", "") for t in peer_trts)

    def test_briefs_written_with_protocol(self, tmp_path):
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        for seat in plan["seats"]:
            p = Path(seat["brief_path"])
            assert p.is_file(), f"missing brief for {seat['cluster']}"
            text = p.read_text()
            assert BRIEF[:20] in text
            assert seat["territory"] in text
            for marker in ("project_attach", "find_skills", "project_note"):
                assert marker in text, f"{marker} missing from {seat['cluster']} brief"

    def test_keyword_additions(self, tmp_path):
        # one addition at a time: lead+4 core = 5 seats, cap 6 allows one more
        p1 = plan_project("design the system architecture for an app",
                          project_dir=str(tmp_path))
        assert "architect" in {s["cluster"] for s in p1["seats"]}
        p2 = plan_project("write the wiki content and knowledge articles",
                          project_dir=str(tmp_path))
        assert "knowledge_wiki" in {s["cluster"] for s in p2["seats"]}

    def test_extra_clusters_override(self, tmp_path):
        plan = plan_project(BRIEF, project_dir=str(tmp_path),
                            extra_clusters=["general"])
        assert "general" in {s["cluster"] for s in plan["seats"]}

    def test_seat_cap_enforced(self, tmp_path):
        plan = plan_project(
            "architecture design documentation database security deploy ui "
            "analyze plan review ecommerce",
            project_dir=str(tmp_path), extra_clusters=["general", "architect"])
        assert len(plan["seats"]) <= MAX_SEATS
        # lead + core peers can never be dropped by the cap
        clusters = {s["cluster"] for s in plan["seats"]}
        assert LEAD_CLUSTER in clusters and CORE_PEERS <= clusters


class TestSpawnStatusStop:
    def _no_tmux(self, monkeypatch):
        import server.forge as forge
        monkeypatch.setattr(forge, "_tmux", lambda *a, **k: (0, "", ""))
        monkeypatch.setattr(forge, "BOOT_WAIT_S", 0)
        monkeypatch.setattr(forge, "BOOT_RETRY_WAIT_S", 0)

    def test_spawn_uses_launch_and_pointer_line(self, tmp_path, monkeypatch):
        import server.forge as forge
        self._no_tmux(monkeypatch)
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        sent = []
        monkeypatch.setattr(forge, "_launch_seat",
                            lambda seat, project_dir, project: (
                                f"nexus-forge-{project}-{seat['cluster']}", 4242))
        monkeypatch.setattr(forge, "_send_pointer", lambda line, sess: sent.append((sess, line)))
        out = forge.spawn_team(plan, str(tmp_path), project="t1")
        assert len(out["seats"]) == len(plan["seats"])
        assert all(r["delivered"] for r in out["seats"])
        for sess, line in sent:
            assert "Read .nexus/briefs/" in line

    def test_spawn_survives_seat_failure(self, tmp_path, monkeypatch):
        import server.forge as forge
        self._no_tmux(monkeypatch)
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        calls = {"n": 0}

        def flaky(seat, project_dir, project):
            calls["n"] += 1
            if calls["n"] == 1:
                return "", None  # first seat fails to launch
            return f"nexus-forge-t2-{seat['cluster']}", 1

        monkeypatch.setattr(forge, "_launch_seat", flaky)
        monkeypatch.setattr(forge, "_send_pointer", lambda line, sess: None)
        out = forge.spawn_team(plan, str(tmp_path), project="t2")
        assert out["seats"][0]["delivered"] is False
        assert sum(1 for r in out["seats"][1:] if r["delivered"]) == len(out["seats"]) - 1

    def test_status_discovers_live_sessions_across_processes(self, monkeypatch):
        import server.forge as forge
        monkeypatch.setattr(forge, "_TEAMS", {})  # no registry — discovery only
        monkeypatch.setattr(
            forge, "_discover_sessions",
            lambda project: [f"nexus-forge-{project}--frontend_ui",
                             f"nexus-forge-{project}--backend_api"])
        monkeypatch.setattr(forge, "_tmux",
                            lambda *a, **k: (0, "opencode v1.18\n▌Read .nexus/briefs/x.md", ""))
        status = forge.team_status("tp")
        assert {s["cluster"] for s in status["seats"]} == {"frontend_ui", "backend_api"}
        assert all(s["alive"] for s in status["seats"])

    def test_status_and_stop_targeted(self, tmp_path, monkeypatch):
        import server.forge as forge
        self._no_tmux(monkeypatch)
        plan = plan_project(BRIEF, project_dir=str(tmp_path))
        monkeypatch.setattr(forge, "_launch_seat",
                            lambda seat, pd, project: (f"nexus-forge-t3-{seat['cluster']}", 7))
        monkeypatch.setattr(forge, "_send_pointer", lambda line, sess: None)
        forge.spawn_team(plan, str(tmp_path), project="t3")

        monkeypatch.setattr(forge, "_tmux", lambda *a, **k: (
            0, "alive\n2026-08-17T10:00:00Z\npane line 1\npane line 2\n", ""))
        status = forge.team_status("t3")
        assert len(status["seats"]) == len(plan["seats"])
        assert all(s["alive"] for s in status["seats"])

        killed = []
        monkeypatch.setattr(forge, "_kill_session", lambda sess: killed.append(sess) or 0)
        stopped = forge.stop_team("t3")
        assert stopped["killed"] == len(killed)
        assert len(killed) == len(plan["seats"])
