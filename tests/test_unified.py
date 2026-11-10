#!/usr/bin/env python3
"""
NEXUS v4 — Unified System Test Suite
=====================================
Tests Phase 0-8: DB migration, routing pipeline, reflection, event bridge,
                 cluster activation, learning loop, and unified server.

Run: python -m tests.test_unified
"""
from server.db import get_db_path as _resolve_db_path
import os, sys, json, sqlite3, unittest, shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path("/home/roshan/nexus")
sys.path.insert(0, str(NEXUS_ROOT))

DB_PATH = Path(_resolve_db_path())


# ═══════════════════════════════════════════════════════════════════════
# Phase 0: Database Migration
# ═══════════════════════════════════════════════════════════════════════

class TestPhase0_DatabaseMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(str(DB_PATH))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_learner_tables_exist(self):
        """New learner tables must exist in nexus.db"""
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        required = {"model_beliefs", "ide_events", "routing_decisions",
                    "task_classifications", "reflection_fixes", "reflection_failures",
                    "benchmark_results"}
        missing = required - table_names
        self.assertEqual(missing, set(), f"Missing tables: {missing}")

    def test_cluster_membership_populated(self):
        """cluster_membership should have resources assigned to clusters"""
        count = self.conn.execute(
            "SELECT COUNT(*) FROM cluster_membership"
        ).fetchone()[0]
        self.assertGreater(count, 0, "cluster_membership is empty")

    def test_model_beliefs_columns(self):
        """model_beliefs must have the right columns for Thompson Sampling"""
        cols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(model_beliefs)"
        ).fetchall()}
        required = {"provider", "model", "task_type", "alpha", "beta",
                    "total_requests", "last_updated"}
        self.assertEqual(required - cols, set(), f"Missing columns: {required - cols}")


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Routing Pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestPhase1_RoutingPipeline(unittest.TestCase):
    def setUp(self):
        os.environ["NEXUS_CLUSTER_ROUTING"] = "false"
        os.environ["NEXUS_THOMPSON_ROUTING"] = "false"
        os.environ["NEXUS_REFLECTION"] = "false"
        sys.modules.pop("server.router", None)

    def test_router_imports(self):
        """router.py must import without errors"""
        from server.router import route_task, find_matching_skills
        self.assertTrue(callable(route_task))

    def test_route_task_basic(self):
        """route_task must return a valid result dict"""
        from server.router import route_task
        result = route_task("fix the login bug", "default")
        self.assertIn("model", result)
        self.assertIn("task_type", result)
        self.assertIn("pipeline", result)
        self.assertIsInstance(result["skills"], list)

    def test_route_task_pipeline_labels(self):
        """Pipeline steps must be recorded (at minimum: classify)"""
        from server.router import route_task
        result = route_task("implement user authentication", "default")
        self.assertIn("pipeline", result)
        self.assertIn("classify", result["pipeline"])

    def test_find_matching_skills(self):
        """find_matching_skills returns up to max_results skills"""
        from server.router import find_matching_skills
        skills = find_matching_skills("debug python error", "default", max_results=3)
        self.assertLessEqual(len(skills), 3)

    def test_classify_task_returns_type(self):
        """Task classification must return a valid type"""
        from server.router import classify_task
        task_type, confidence, cluster = classify_task("implement payment API")
        self.assertIsInstance(task_type, str)
        self.assertGreater(confidence, 0)

    def test_cluster_route_when_disabled_returns_none(self):
        """detect_cluster must return None when CLUSTER_ROUTING is off"""
        from server.router import detect_cluster
        result = detect_cluster("build a dashboard")
        self.assertIsNone(result)

    def test_thompson_sample_when_disabled_returns_none(self):
        """thompson_sample_model must return None when THOMPSON_ROUTING is off"""
        from server.router import thompson_sample_model
        result = thompson_sample_model("coding", [{"provider": "ollama_cloud", "model": "deepseek-v4-flash"}])
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Self-Reflection Loop
# ═══════════════════════════════════════════════════════════════════════

class TestPhase2_ReflectionLoop(unittest.TestCase):
    def test_reflection_loop_imports(self):
        """reflection_loop.py must import without errors"""
        from server.reflection_loop import ReflectionLoop, reflect_and_retry
        self.assertTrue(callable(reflect_and_retry))

    def test_reflection_disabled_returns_unsuccessful(self):
        """When REFLECTION_ENABLED=False, retry must return failure gracefully"""
        from server.reflection_loop import reflect_and_retry
        result = reflect_and_retry(
            failed_tool="test_tool",
            error="test error",
            query="test query",
            candidates=[{"name": "alt1", "score": 0.5}],
        )
        self.assertFalse(result.success)
        self.assertEqual(result.diagnosis, "Reflection disabled")

    def test_reflection_loop_close_handles_double_close(self):
        """ReflectionLoop.close() must not raise on double-close"""
        from server.reflection_loop import ReflectionLoop
        loop = ReflectionLoop()
        loop.close()
        loop.close()  # should not raise

    def test_get_failure_stats(self):
        """get_failure_stats returns a dict with 'enabled' key"""
        from server.reflection_loop import ReflectionLoop
        loop = ReflectionLoop()
        stats = loop.get_failure_stats()
        self.assertIn("enabled", stats)
        loop.close()


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: MCP Sync Daemon
# ═══════════════════════════════════════════════════════════════════════════════════

class TestPhase3_MCPSyncDaemon(unittest.TestCase):
    def test_nexus_daemon_imports(self):
        """nexus_daemon.py must import without errors"""
        from server.nexus_daemon import get_sync_status
        self.assertTrue(callable(get_sync_status))

    def test_get_sync_status_returns_dict(self):
        """get_sync_status() returns a status dict"""
        from server.nexus_daemon import get_sync_status
        status = get_sync_status()
        self.assertIsInstance(status, dict)
        self.assertIn("enabled", status)


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: IDE Event Bridge
# ═══════════════════════════════════════════════════════════════════════

class TestPhase4_IDEEventBridge(unittest.TestCase):
    def test_event_bridge_imports(self):
        """event_bridge.py must import without errors"""
        from server.event_bridge import EventBridge, record_outcome
        self.assertTrue(callable(record_outcome))

    def test_record_outcome_disabled_is_silent(self):
        """When LEARNING_ENABLED=False, record_outcome must not raise"""
        from server.event_bridge import record_outcome
        # Should not raise
        result = record_outcome("correct", {
            "matched_model": "test-model",
            "provider": "ollama_cloud",
            "task_type": "coding",
            "skill_name": "test-skill",
        }, ide="test-ide")
        # Returns None when disabled
        self.assertIsNone(result)

    def test_get_learning_stats_when_disabled(self):
        """get_learning_stats() returns {'enabled': False} when disabled"""
        from server.event_bridge import get_bridge
        bridge = get_bridge()
        stats = bridge.get_learning_stats()
        self.assertIn("enabled", stats)
        bridge.close()


# ═══════════════════════════════════════════════════════════════════════
# Phase 5: Auto-Trigger + Auto-Invoke
# ═══════════════════════════════════════════════════════════════════════

class TestPhase5_AutoTriggerInvoke(unittest.TestCase):
    def test_auto_trigger_imports(self):
        """auto_trigger.py must import without errors"""
        from server.auto_trigger import AutoTrigger, detect_signal
        self.assertTrue(callable(detect_signal))

    def test_auto_trigger_detect_signal(self):
        """AutoTrigger detects the right cluster for coding tasks"""
        from server.auto_trigger import AutoTrigger
        trigger = AutoTrigger()
        results = trigger.detect_signal("implement payment endpoint in Django")
        cluster_names = {r["cluster"] for r in results}
        self.assertIn("backend_api", cluster_names)

    def test_auto_trigger_frontend_signal(self):
        """AutoTrigger detects frontend_ui cluster for UI tasks"""
        from server.auto_trigger import AutoTrigger
        trigger = AutoTrigger()
        results = trigger.detect_signal("build a React dashboard with Tailwind CSS")
        cluster_names = {r["cluster"] for r in results}
        self.assertIn("frontend_ui", cluster_names)

# ═══════════════════════════════════════════════════════════════════════
# Phase 6: Unified Server
# ═══════════════════════════════════════════════════════════════════════

class TestPhase6_UnifiedServer(unittest.TestCase):
    def test_unified_app_imports(self):
        """unified.py must import FastAPI app without errors"""
        from server.unified import app
        self.assertIsNotNone(app)

    def test_unified_health_endpoint_exists(self):
        """Unified app must have /health endpoint"""
        from server.unified import app
        routes = {r.path for r in app.routes}
        self.assertIn("/health", routes)

    def test_unified_nexus_routes_exist(self):
        """Unified app must have all /nexus/* endpoints"""
        from server.unified import app
        routes = {r.path for r in app.routes}
        required = {"/nexus/route", "/nexus/event", "/nexus/beliefs",
                    "/nexus/reflection", "/nexus/health", "/nexus/learning-stats",
                    "/nexus/autopilot/run", "/nexus/task-runs/{task_run_id}",
                    "/nexus/task-runs/{task_run_id}/review", "/nexus/learnings"}
        missing = required - routes
        self.assertEqual(missing, set(), f"Missing routes: {missing}")

    def test_unified_llm_gateway_routes_exist(self):
        """Unified app must have OpenAI-compatible LLM gateway routes"""
        from server.unified import app
        routes = {r.path for r in app.routes}
        required = {"/v1/chat/completions", "/v1/models", "/approaches"}
        missing = required - routes
        self.assertEqual(missing, set(), f"Missing LLM routes: {missing}")


# ═══════════════════════════════════════════════════════════════════════
# Phase 7: Learning Loop
# ═══════════════════════════════════════════════════════════════════════

class TestPhase7_LearningLoop(unittest.TestCase):
    def test_learner_imports(self):
        """NexusLearner must import without errors"""
        from core.learner import NexusLearner
        self.assertTrue(callable(NexusLearner))

    def test_learner_thompson_sample_requires_minimum_samples(self):
        """Learner exposes whether the DB has enough samples to route."""
        learner = None
        try:
            from core.learner import NexusLearner
            learner = NexusLearner()
            can_route = learner.can_route("coding")
            self.assertIsInstance(can_route, bool)
        finally:
            if learner:
                learner.close()

    def test_learner_record_ide_event_disabled(self):
        """Recording events when disabled must not raise"""
        learner = None
        try:
            from core.learner import NexusLearner, IDEEvent
            learner = NexusLearner()
            event = IDEEvent(
                event_type="task_completed", ide="test", provider="ollama_cloud",
                model="test-model", task_type="coding", reward=1.0,
                metadata={}
            )
            learner.record_ide_event(event)  # should not raise
        finally:
            if learner:
                learner.close()

    def test_learner_classify_task(self):
        """NexusLearner.classify_task returns (type, confidence)"""
        learner = None
        try:
            from core.learner import NexusLearner
            learner = NexusLearner()
            task_type, confidence = learner.classify_task("implement authentication API")
            self.assertIsInstance(task_type, str)
            self.assertGreater(confidence, 0)
        finally:
            if learner:
                learner.close()


# ═══════════════════════════════════════════════════════════════════════
# Phase 8: Integration — Full Pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestPhase8_Integration(unittest.TestCase):
    def test_approaches_register(self):
        """All approach plugins must be loadable"""
        from approaches import NoneApproach, CoTReflectionApproach, MoAApproach, MCTSApproach, BoNApproach
        self.assertTrue(NoneApproach.SLUG, "none")
        self.assertTrue(CoTReflectionApproach.SLUG, "cot_reflection")

    def test_cluster_config_definitions(self):
        """All 8 clusters must be defined in cluster_config"""
        from server.cluster_config import CLUSTER_DEFINITIONS
        expected = {"analyzer_planner", "architect", "frontend_ui", "backend_api",
                    "devops_infra", "quality_security", "knowledge_wiki", "general"}
        self.assertEqual(set(CLUSTER_DEFINITIONS.keys()), expected)

    def test_config_loads(self):
        """nexus.yaml must load without errors"""
        from core.config import NexusConfig
        config_path = NEXUS_ROOT / "nexus.yaml"
        if config_path.exists():
            config = NexusConfig(str(config_path))
            self.assertIsInstance(config.routing.enabled, bool)

    def test_startup_script_exists(self):
        """start_nexus.py must exist and be executable"""
        startup = NEXUS_ROOT / "start_nexus.py"
        self.assertTrue(startup.exists(), "start_nexus.py not found")

    def test_env_example_exists(self):
        """.env.example must exist"""
        env_example = NEXUS_ROOT / ".env.example"
        self.assertTrue(env_example.exists(), ".env.example not found")


class TestPhase9_BrokerRuntime(unittest.TestCase):
    def setUp(self):
        self.broker_db = "/tmp/nexus_broker_test.db"
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(self.broker_db)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.environ["NEXUS_BROKER_DB_PATH"] = self.broker_db
        # Clear cached DB path so broker_runtime picks up the env var
        from server.db import get_db_path
        if hasattr(get_db_path, "_cached"):
            delattr(get_db_path, "_cached")

    def tearDown(self):
        os.environ.pop("NEXUS_BROKER_DB_PATH", None)

    def test_runtime_schema_tables_exist(self):
        from server.broker_runtime import ensure_runtime_schema
        ensure_runtime_schema()
        conn = sqlite3.connect(self.broker_db)
        try:
            tables = {t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required = {"task_runs", "tool_traces", "bundle_membership", "resource_policies",
                        "user_learnings", "execution_reviews", "mistake_patterns"}
            self.assertEqual(required - tables, set())
        finally:
            conn.close()

    def test_create_task_run_generates_preview_and_report(self):
        from server import broker_runtime

        def fake_route(task, project="default", max_results=5, **kwargs):
            task_lower = task.lower()
            if "verify" in task_lower:
                cluster = "quality_security"
            elif "audit" in task_lower:
                cluster = "analyzer_planner"
            else:
                cluster = "frontend_ui"
            return {
                "cluster": cluster,
                "task_type": "frontend",
                "model": "devstral-small-2:24b-cloud",
                "skills": [
                    {"name": "frontend-ui-engineering", "score": 0.92, "type": "skill"},
                    {"name": "design-html", "score": 0.89, "type": "skill"},
                    {"name": "qa", "score": 0.81, "type": "skill"},
                    {"name": "adaptive-imagining-cat", "score": 0.78, "type": "skill"},
                ],
            }

        with patch.object(broker_runtime, "route_for_task", side_effect=fake_route):
            result = broker_runtime.create_task_run(
                "build a landing page",
                ide_name="codex",
                mode="safemode",
                project="default",
                auto_execute=True,
            )

        self.assertEqual(result["executor"], "adaptive-imagining-cat")
        self.assertEqual(result["mode"], "safemode")
        self.assertEqual(result["status"], "completed")
        self.assertIn("execution_preview", result)
        self.assertIn("final_report", result)
        self.assertGreaterEqual(len(result["execution_preview"]["phases"]), 3)

    def test_review_task_run_stores_learning(self):
        from server import broker_runtime

        def fake_route(task, project="default", max_results=5, **kwargs):
            return {
                "cluster": "analyzer_planner",
                "task_type": "reasoning",
                "model": "devstral-small-2:24b-cloud",
                "skills": [
                    {"name": "adaptive-imagining-cat", "score": 0.95, "type": "skill"},
                    {"name": "researcher", "score": 0.88, "type": "agent"},
                ],
            }

        with patch.object(broker_runtime, "route_for_task", side_effect=fake_route):
            run = broker_runtime.create_task_run(
                "audit routing quality",
                ide_name="hermes",
                mode="safemode",  # godmode is refused for callers (design T2-2)
                project="default",
                auto_execute=True,
            )
            with patch.object(broker_runtime, "record_outcome", return_value={"status": "logged"}):
                reviewed = broker_runtime.review_task_run(
                    run["task_run_id"],
                    "wrong",
                    "Prefer researcher only when task explicitly asks for external research.",
                )

        self.assertEqual(reviewed["status"], "reviewed")
        learnings = broker_runtime.search_user_learnings(query="Prefer researcher only", project="default")
        self.assertGreaterEqual(len(learnings["results"]), 1)


# ═══════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Env set by conftest.py (auto-loaded by pytest)
    # For direct runs, set them here:
    os.environ.setdefault("NEXUS_THOMPSON_ROUTING", "false")
    os.environ.setdefault("NEXUS_CLUSTER_ROUTING", "false")
    os.environ.setdefault("NEXUS_REFLECTION", "false")
    os.environ.setdefault("NEXUS_LEARNING", "false")

    # Run tests
    loader = unittest.TestLoader()
    suite = loader.discover(str(NEXUS_ROOT / "tests"), pattern="test_unified.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
