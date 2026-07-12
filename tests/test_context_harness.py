#!/usr/bin/env python3
"""
NEXUS Context Harness Engine — V (Verification Suite)
=====================================================
16 block-level verification tests for P0-P5 components.
Each test is self-contained with in-memory SQLite DB + fixture data.

Run: python -m pytest tests/test_context_harness.py -v
     python -m tests.test_context_harness
"""
import os, sys, json, sqlite3, time, hashlib, uuid, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone

NEXUS_ROOT = Path("/home/roshan/nexus")
sys.path.insert(0, str(NEXUS_ROOT))

os.environ.setdefault("NEXUS_THOMPSON_ROUTING", "false")
os.environ.setdefault("NEXUS_CLUSTER_ROUTING", "false")
os.environ.setdefault("NEXUS_REFLECTION", "false")
os.environ.setdefault("NEXUS_LEARNING", "false")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY,
    name TEXT, description TEXT, body_text TEXT,
    resource_type TEXT, cluster TEXT, status TEXT DEFAULT 'active',
    tier INTEGER DEFAULT 2, token_count INTEGER DEFAULT 100,
    times_correct INTEGER DEFAULT 0, times_wrong INTEGER DEFAULT 0,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS resource_sections (
    id INTEGER PRIMARY KEY,
    resource_id INTEGER, heading TEXT, content TEXT, sort_order INTEGER DEFAULT 0,
    FOREIGN KEY (resource_id) REFERENCES resources(id)
);
CREATE TABLE IF NOT EXISTS resource_edges (
    source_id INTEGER, target_id INTEGER, weight REAL DEFAULT 1.0,
    edge_type TEXT DEFAULT 'co_triggered',
    PRIMARY KEY (source_id, target_id)
);
CREATE TABLE IF NOT EXISTS bundle_membership (
    resource_id INTEGER, bundle_name TEXT, bundle_kind TEXT,
    priority INTEGER DEFAULT 50, is_canonical INTEGER DEFAULT 0,
    PRIMARY KEY (resource_id, bundle_name)
);
CREATE TABLE IF NOT EXISTS cache_hierarchy (
    layer TEXT, cache_key TEXT, value BLOB,
    created_at TEXT, updated_at TEXT,
    PRIMARY KEY (layer, cache_key)
);
CREATE TABLE IF NOT EXISTS classification (
    resource_id INTEGER PRIMARY KEY,
    project_type TEXT, confidence REAL,
    dominant_keyword TEXT, match_count INTEGER,
    scores TEXT, model_version INTEGER
);
"""


def make_db(extra_schema: str = "") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA_SQL)
    if extra_schema:
        conn.executescript(extra_schema)
    return conn


FIXTURE_RESOURCES = [
    (1, "LLM Agent", "A large language model agent", "llm agent using tensorflow for inference", "skill", "general", "active", 1, 300, 10, 1, "2026-06-15T10:00:00+00:00"),
    (2, "React Dashboard", "Web dashboard built with React", "react components with css grid layout", "skill", "frontend_ui", "active", 2, 200, 8, 2, "2026-06-14T10:00:00+00:00"),
    (3, "Docker Deploy", "CI/CD deployment pipeline", "docker kubernetes deploy automation", "command", "devops_infra", "active", 2, 150, 5, 0, "2026-06-13T10:00:00+00:00"),
    (4, "Trading Bot", "Crypto trading bot", "trading strategy market entry exit signal", "agent", "general", "active", 2, 250, 3, 1, "2026-06-10T10:00:00+00:00"),
    (5, "Research Paper", "Benchmark evaluation paper", "research paper analysis benchmark methodology", "doc", "knowledge_wiki", "active", 2, 400, 12, 0, "2026-06-12T10:00:00+00:00"),
    (6, "Python SDK", "General dev SDK", "python sdk library package module cli", "tool", "general", "active", 2, 180, 7, 1, "2026-06-11T10:00:00+00:00"),
    (7, "Old Resource", "Stale test resource", "legacy deprecated content not used", "skill", "general", "active", 2, 50, 0, 5, "2025-01-01T00:00:00+00:00"),
]

FIXTURE_SECTIONS = [
    (1, 1, "Overview", "tensorflow neural network training pipeline", 0),
    (2, 1, "Usage", "machine learning model inference", 1),
    (3, 2, "Setup", "install npm and configure tailwind react", 0),
    (4, 3, "Deploy", "docker compose kubernetes deployment", 0),
    (5, 4, "Strategy", "crypto market entry exit risk management", 0),
    (6, 5, "Abstract", "novel approach to benchmark evaluation analysis", 0),
    (7, 6, "API", "python sdk library module cli interface", 0),
]

FIXTURE_EDGES = [
    (1, 2, 0.8, "co_triggered"),
    (1, 3, 0.5, "co_triggered"),
    (2, 3, 0.6, "co_triggered"),
    (4, 1, 0.3, "co_triggered"),
    (5, 6, 0.7, "co_triggered"),
]

FIXTURE_BUNDLES = [
    (1, "core_infra", "core_infra", 10, 1),
    (2, "web_dev_bundle", "project_type", 30, 1),
    (3, "devops_bundle", "project_type", 30, 1),
    (4, "trading_bundle", "project_type", 30, 1),
    (5, "research_bundle", "project_type", 40, 1),
    (6, "general_dev_bundle", "project_type", 50, 1),
    (1, "ai_ml_bundle", "project_type", 30, 0),
]


def insert_fixtures(conn: sqlite3.Connection) -> None:
    for r in FIXTURE_RESOURCES:
        conn.execute(
            "INSERT INTO resources (id, name, description, body_text, resource_type, cluster, status, tier, token_count, times_correct, times_wrong, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            r,
        )
    for s in FIXTURE_SECTIONS:
        conn.execute(
            "INSERT INTO resource_sections (id, resource_id, heading, content, sort_order) VALUES (?, ?, ?, ?, ?)",
            s,
        )
    for e in FIXTURE_EDGES:
        conn.execute(
            "INSERT INTO resource_edges (source_id, target_id, weight, edge_type) VALUES (?, ?, ?, ?)",
            e,
        )
    for b in FIXTURE_BUNDLES:
        conn.execute(
            "INSERT INTO bundle_membership (resource_id, bundle_name, bundle_kind, priority, is_canonical) VALUES (?, ?, ?, ?, ?)",
            b,
        )
    conn.commit()


class _NoCloseConnection:
    """Wraps a sqlite3.Connection so close() is a no-op.

    The cache module's ``finally: conn.close()`` pattern would otherwise
    close the in-memory DB shared across patched calls.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        pass  # keep the in-memory DB alive


def _mock_ro_conn(conn: sqlite3.Connection):
    """Return a patcher that makes get_ro_conn return *conn* (close-safe)."""
    return patch("server.context_harness.cache.get_ro_conn", return_value=_NoCloseConnection(conn))


def _mock_rw_conn(conn: sqlite3.Connection):
    """Return a patcher that makes _get_rw_conn return *conn* (close-safe)."""
    return patch("server.context_harness.cache._get_rw_conn", return_value=_NoCloseConnection(conn))


# ═══════════════════════════════════════════════════════════════════════
# Group A — Core (tests 1-3)
# ═══════════════════════════════════════════════════════════════════════

class TestA_Core(unittest.TestCase):

    def test_project_type_enum(self):
        from server.context_harness.project_types import ProjectType, get_signature, get_idf
        types = list(ProjectType)
        self.assertEqual(len(types), 6)
        names = {pt.value for pt in types}
        expected = {"ai-ml", "web-dev", "general-dev", "research", "devops", "trading"}
        self.assertEqual(names, expected)

        sig = get_signature(ProjectType.AI_ML)
        self.assertIsInstance(sig, list)
        self.assertGreater(len(sig), 0)
        self.assertIn("keyword", sig[0])

        sig_web = get_signature(ProjectType.WEB_DEV)
        self.assertGreater(len(sig_web), 0)
        self.assertEqual(sig_web[0]["keyword"], "react")

        idf = get_idf("tensorflow")
        self.assertGreater(idf, 0)
        idf_missing = get_idf("nonexistent_keyword_xyz")
        self.assertEqual(idf_missing, 1.0)

    def test_scorer_scoring(self):
        from server.context_harness.project_types import ProjectType, get_signature, get_idf
        from server.context_harness.classifier.scorer import ResourceSignals, compute_single_score

        signatures = {}
        for pt in ProjectType:
            signatures[pt] = get_signature(pt)

        idf_cache = {}
        for sigs in signatures.values():
            for s in sigs:
                if s["keyword"] not in idf_cache:
                    idf_cache[s["keyword"]] = get_idf(s["keyword"])

        signals = ResourceSignals(
            resource_id=1,
            name="LLM Agent",
            description="A large language model agent with tensorflow",
            body_text_snippet="llm agent using tensorflow for inference neural network training pipeline",
            resource_type="skill",
            cluster="general",
            section_headings=["Overview", "Usage"],
            section_contents=["tensorflow neural network training pipeline", "machine learning model inference"],
        )
        vector = compute_single_score(signals, signatures, idf_cache)
        for pt_name, score in vector.scores.items():
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)
        self.assertEqual(vector.dominant_type, ProjectType.AI_ML.value)
        self.assertGreater(vector.match_count, 0)

        web_signals = ResourceSignals(
            resource_id=2,
            name="React Dashboard",
            description="Web dashboard built with React",
            body_text_snippet="react components with css grid layout",
            resource_type="skill",
            cluster="frontend_ui",
            section_headings=["Setup"],
            section_contents=["install npm and configure tailwind react"],
        )
        vector2 = compute_single_score(web_signals, signatures, idf_cache)
        self.assertEqual(vector2.dominant_type, ProjectType.WEB_DEV.value)

    def test_bundle_definitions(self):
        from server.context_harness.bundles.definitions import (
            get_bundles, BundleDefinition, BundleCriteria,
            match_criteria, get_canonical_bundle,
        )
        bundles = get_bundles()
        self.assertEqual(len(bundles), 7)
        for name, defn in bundles.items():
            self.assertIsInstance(defn, BundleDefinition)
            self.assertIn(defn.kind, {"core_infra", "project_type"})

        ai_resource = {"id": 1, "name": "LLM Agent", "description": "tensorflow ml ai", "body_text": "llm rag embedding neural", "resource_type": "skill", "cluster": "general", "tier": 2}
        classification = {"project_type": "ai-ml"}
        matched = match_criteria(ai_resource, classification, bundles["ai_ml_bundle"].criteria)
        self.assertTrue(matched)

        web_resource = {"id": 2, "name": "React Dashboard", "description": "react typescript css", "body_text": "frontend web application", "resource_type": "skill", "cluster": "frontend_ui", "tier": 2}
        web_classification = {"project_type": "web-dev"}
        matched_web = match_criteria(web_resource, web_classification, bundles["web_dev_bundle"].criteria)
        self.assertTrue(matched_web)

        canonical = get_canonical_bundle(ai_resource, {1: classification}, bundles)
        self.assertEqual(canonical, "ai_ml_bundle")

        dep_resource = {"id": 7, "name": "Old Resource", "description": "legacy deprecated", "body_text": "old stuff", "resource_type": "skill", "cluster": "general", "tier": 2}
        excluded = match_criteria(dep_resource, classification, bundles["ai_ml_bundle"].criteria)
        self.assertFalse(excluded)


# ═══════════════════════════════════════════════════════════════════════
# Group B — Cache (tests 4-7)
# ═══════════════════════════════════════════════════════════════════════

class TestB_Cache(unittest.TestCase):

    def test_l0_cache(self):
        conn = make_db()
        insert_fixtures(conn)

        from server.context_harness.cache import L0Cache, get_l0_cache

        with _mock_ro_conn(conn), _mock_rw_conn(conn):
            cache = L0Cache()
            entry = cache.get(1)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.resource_id, 1)
            self.assertEqual(entry.name, "LLM Agent")
            self.assertGreater(cache.token_count(), 0)

            cache.serialize_to_db()
            row = conn.execute("SELECT value FROM cache_hierarchy WHERE layer='L0' AND cache_key='default'").fetchone()
            self.assertIsNotNone(row)

            cache2 = L0Cache()
            self.assertIsNotNone(cache2.get(1))

    def test_l1_cache(self):
        conn = make_db()
        insert_fixtures(conn)

        from server.context_harness.cache import L1Cache

        with _mock_ro_conn(conn), _mock_rw_conn(conn):
            cache = L1Cache()
            cache.fill("general_dev")
            entry = cache.get("general_dev", 6)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "Python SDK")

            bundle = cache.get_bundle("general_dev")
            self.assertIn(6, bundle)
            self.assertIn("general_dev", cache.loaded_project_types())

            cache.hit("general_dev", 6)
            after_hit = cache.get("general_dev", 6)
            self.assertGreater(after_hit.hit_count, 0)

            cache.fill("web_dev")
            cache.swap("web_dev")
            self.assertNotIn("general_dev", cache.loaded_project_types())
            self.assertIn("web_dev", cache.loaded_project_types())

    def test_l2_cache(self):
        conn = make_db()
        insert_fixtures(conn)

        from server.context_harness.cache import L2Cache

        with _mock_ro_conn(conn), _mock_rw_conn(conn):
            cache = L2Cache()
            cache.activate(["frontend_ui"])
            entries = cache.get_all_for_cluster("frontend_ui")
            self.assertGreater(len(entries), 0)
            self.assertEqual(entries[0].name, "React Dashboard")

            entry = cache.get("frontend_ui", 2)
            self.assertIsNotNone(entry)

            cache.activate(["devops_infra"])
            devops = cache.get_all_for_cluster("devops_infra")
            self.assertGreater(len(devops), 0)

    def test_l3_cache(self):
        from server.context_harness.cache import L3Cache

        cache = L3Cache()
        iso_scores = [{"resource_id": 1, "score": 0.9, "token_count": 50}]
        cache.set("test query", iso_scores)
        result = cache.get("test query")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["resource_id"], 1)

        cache.invalidate("test query")
        self.assertIsNone(cache.get("test query"))

        big_scores = [{"resource_id": i, "score": 0.5, "token_count": 1000} for i in range(10)]
        cache.set("big query", big_scores)
        result = cache.get("big query")
        self.assertIsNotNone(result)

        self.assertGreaterEqual(len(cache), 0)

        t0 = time.time()
        cache2 = L3Cache()
        cache2.set("ttl query", iso_scores)
        self.assertIsNotNone(cache2.get("ttl query"))


# ═══════════════════════════════════════════════════════════════════════
# Group C — Compression (tests 8-10)
# ═══════════════════════════════════════════════════════════════════════

class TestC_Compression(unittest.TestCase):

    def test_volatile_detector(self):
        from server.context_harness.compression import VolatileContentDetector

        detector = VolatileContentDetector()

        text_with_ts = "Started at 2024-01-15T10:30:00Z and finished"
        tags = detector.detect_all(text_with_ts)
        self.assertGreater(len(tags), 0)
        self.assertEqual(tags[0].detector, "timestamp")
        normalized = detector.normalize(text_with_ts, tags)
        self.assertIn("<TS>", normalized)
        self.assertNotIn("2024-01-15T10:30:00Z", normalized)

        text_with_sid = "session_abc123def4567890abcdef12345678"
        tags = detector.detect_all(text_with_sid)
        self.assertGreater(len(tags), 0)
        self.assertEqual(tags[0].detector, "session_id")

        text_with_uuid = f"guid {uuid.uuid4()} here"
        tags = detector.detect_all(text_with_uuid)
        self.assertGreater(len(tags), 0)
        self.assertEqual(tags[0].detector, "auto_id")

        text_with_error = "Error: connection refused\n  at line 42\n"
        tags = detector.detect_all(text_with_error)
        error_tags = [t for t in tags if t.detector == "error_msg"]
        self.assertGreater(len(error_tags), 0)
        normalized = detector.normalize(text_with_error, tags)
        self.assertEqual(normalized.strip(), "")

        from server.context_harness.compression import VolatileTag
        merged = VolatileContentDetector._merge_overlapping([
            VolatileTag("a", "longer match", "<X>", 0, 13),
            VolatileTag("b", "short", "<Y>", 2, 7),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].original, "longer match")

    def test_window_packer(self):
        from server.context_harness.compression import WindowPacker, PackedSection

        packer = WindowPacker(max_tokens=100, min_tokens=10)
        section = PackedSection(
            resource_id=1, section_id=1,
            section_title="Test", resolved_name="Test Resource",
            body="hello world", body_tokens=30,
            salience=0.9, compression_ratio=1.0,
            volatile_stripped=[],
        )
        added = packer.add_section(section)
        self.assertTrue(added)

        windows = packer.flush()
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].total_tokens, 30)
        self.assertEqual(windows[0].max_tokens, 100)

        big_section = PackedSection(
            resource_id=2, section_id=2,
            section_title="Big", resolved_name="Big Resource",
            body="x" * 500, body_tokens=200,
            salience=0.5, compression_ratio=1.0,
            volatile_stripped=[],
        )
        added = packer.add_section(big_section)
        self.assertFalse(added)

        windows = packer.flush()
        self.assertEqual(len(windows), 0)

        packer.add_section(section)
        packer.add_section(section)
        packer.add_section(section)
        multiple = packer.flush()
        self.assertEqual(len(multiple), 1)

    def test_crusher(self):
        from server.context_harness.compression import (
            Crusher, CrusherConfig, CompressionStrategy,
        )

        config = CrusherConfig(max_chars=50, max_sentences=2, min_chars=5)
        crusher = Crusher(config)

        text = "This is a long text. It has multiple sentences. And some more content here."
        result, ratio = crusher.crush(text, CompressionStrategy.NONE)
        self.assertEqual(result, text)
        self.assertEqual(ratio, 1.0)

        result, ratio = crusher.crush(text, CompressionStrategy.TRUNCATE)
        self.assertLessEqual(len(result), 50)

        result, ratio = crusher.crush(text, CompressionStrategy.EXTRACTIVE)
        sentences = result.split(".")
        self.assertLessEqual(len([s for s in sentences if s.strip()]), 2)

        kw_result, kw_ratio = crusher.crush(text, CompressionStrategy.KEYWORD, keywords=["sentences"])
        self.assertIn("sentences", kw_result)

        title_result, title_ratio = crusher.crush(text, CompressionStrategy.TITLE_ONLY)
        self.assertEqual(title_result, "")
        self.assertEqual(title_ratio, 0.0)

        volatile_text = "Started 2024-01-15T10:30:00Z with session_abc123def4567890abcdef12345678"
        vo_result, vo_ratio = crusher.crush(volatile_text, CompressionStrategy.VOLATILE_ONLY)
        self.assertIn("<TS>", vo_result)
        self.assertIn("<SID>", vo_result)
        self.assertNotIn("2024-01-15T10:30:00Z", vo_result)

        strategy = crusher.select_strategy(salience=0.8, token_budget=1000, current_usage=100)
        self.assertEqual(strategy, CompressionStrategy.VOLATILE_ONLY)

        strategy = crusher.select_strategy(salience=0.2, token_budget=1000, current_usage=800)
        self.assertEqual(strategy, CompressionStrategy.TITLE_ONLY)

        strategy = crusher.select_strategy(salience=0.5, token_budget=1000, current_usage=500)
        self.assertEqual(strategy, CompressionStrategy.EXTRACTIVE)


# ═══════════════════════════════════════════════════════════════════════
# Group D — Router & Decisions (tests 11-13)
# ═══════════════════════════════════════════════════════════════════════

class TestD_RouterAndDecisions(unittest.TestCase):

    def test_classifier(self):
        from server.context_harness.classifier import Classifier, Classification
        from server.context_harness.classifier.scorer import ResourceSignals

        classifier = Classifier(db_path=":memory:")

        signals = ResourceSignals(
            resource_id=1,
            name="LLM Agent",
            description="tensorflow pytorch neural network",
            body_text_snippet="llm rag embedding inference transformer model training pipeline",
            resource_type="skill",
            cluster="general",
            section_headings=["Overview"],
            section_contents=["machine learning deep learning gpt bert"],
        )
        result = classifier.classify(signals)
        self.assertIsInstance(result, Classification)
        self.assertEqual(result.resource_id, 1)
        self.assertEqual(result.project_type, "ai-ml")
        self.assertGreater(result.confidence, 0.0)
        self.assertGreater(result.match_count, 0)
        self.assertIsInstance(result.scores, dict)

        results = classifier.classify_batch([
            {"id": 1, "name": "LLM Agent", "description": "tensorflow ml", "body_text": "llm rag", "resource_type": "skill", "cluster": "general"},
            {"id": 2, "name": "React Dashboard", "description": "react css", "body_text": "frontend typescript", "resource_type": "skill", "cluster": "frontend_ui"},
        ])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].project_type, "ai-ml")
        self.assertEqual(results[1].project_type, "web-dev")

        filtered = classifier.filter_by_type({1: result}, "ai-ml")
        self.assertIn(1, filtered)

    def test_router(self):
        conn = make_db()
        insert_fixtures(conn)

        from server.context_harness.router import HubAndSpokeRouter, RouteContext

        with _mock_ro_conn(conn), _mock_rw_conn(conn):
            router = HubAndSpokeRouter(db_path=":memory:")

            ctx = RouteContext(query_text="neural network inference", project_type="ai-ml", top_k=5)
            result = router.route(ctx)
            self.assertGreater(len(result.resource_ids), 0)
            self.assertGreaterEqual(result.latency_ms, 0)

            result2 = router.route(RouteContext(query_text="frontend react css", top_k=5))
            self.assertIsNotNone(result2.project_type)

            rates = router.cache_hit_rate()
            for level in ("L0", "L1", "L2", "L3"):
                self.assertIn(level, rates)
                self.assertGreaterEqual(rates[level], 0.0)
                self.assertLessEqual(rates[level], 1.0)

            cached = router.l3.get("neural network inference")
            self.assertIsNotNone(cached)

    def test_prediction_engine(self):
        conn = make_db()
        insert_fixtures(conn)

        from server.context_harness.workflow_graph import WorkflowGraph
        from server.context_harness.prediction import (
            WorkflowPredictor, SignalChoke, PredictionEngine,
            FrequencyTracker, PredictedResource,
        )

        graph = WorkflowGraph.__new__(WorkflowGraph)
        graph._db_path = ":memory:"
        graph._outgoing = __import__("collections").defaultdict(list)
        graph._incoming = __import__("collections").defaultdict(list)
        graph._edge_count = 0
        graph._node_count = 0

        from server.context_harness.workflow_graph import Edge
        graph._outgoing[1].append(Edge(target_id=2, weight=0.8, edge_type="co_triggered"))
        graph._outgoing[1].append(Edge(target_id=3, weight=0.5, edge_type="co_triggered"))
        graph._outgoing[2].append(Edge(target_id=3, weight=0.6, edge_type="co_triggered"))
        graph._incoming[2].append(Edge(target_id=1, weight=0.8, edge_type="co_triggered"))
        graph._incoming[3].append(Edge(target_id=1, weight=0.5, edge_type="co_triggered"))
        graph._incoming[3].append(Edge(target_id=2, weight=0.6, edge_type="co_triggered"))
        graph._edge_count = 5
        graph._node_count = 3

        predictor = WorkflowPredictor(graph)
        predictions = predictor.predict_next([1])
        self.assertGreater(len(predictions), 0)
        for p in predictions:
            self.assertGreaterEqual(p.score, 0.0)

        choke = SignalChoke()
        for p in predictions:
            event = choke.evaluate(p)
            if event is not None:
                self.assertIn(event.signal_type, {"prewarm", "cache_hint", "load"})
                self.assertGreater(event.confidence, 0.0)
                self.assertEqual(event.source, "workflow_affinity")

        tracker = FrequencyTracker(window_seconds=3600)
        tracker.record(1)
        tracker.record(1)
        self.assertGreater(tracker.frequency(1), 0.0)
        tracker.record(2)
        top = tracker.top_k(2)
        self.assertLessEqual(len(top), 2)

        pred = PredictedResource(resource_id=1, score=0.8, source="workflow_affinity", reason="test", predicted_at=time.time())
        event = choke.evaluate(pred)
        self.assertIsNotNone(event)
        self.assertEqual(event.signal_type, "prewarm")


# ═══════════════════════════════════════════════════════════════════════
# Group E — TOIN & Integration (tests 14-16)
# ═══════════════════════════════════════════════════════════════════════

class TestE_TOINAndIntegration(unittest.TestCase):

    def test_toin(self):
        from server.context_harness.toin import TOIN, ToolOutputSource, FormattedEntry

        toin = TOIN()
        source = ToolOutputSource(
            source_type="skill",
            source_name="agent_reach",
            invocation_id=str(uuid.uuid4()),
            started_at=time.time(),
            duration_ms=123.4,
        )
        entry = toin.ingest(source, "Hello @user, found #results on [test platform]", mime_type="text/markdown")
        self.assertIsInstance(entry, FormattedEntry)
        self.assertEqual(entry.tool_output.source.source_type, "skill")
        self.assertIn("user", entry.tags)
        self.assertIn("results", entry.tags)
        self.assertIn("test platform", entry.tags)

        fmt = toin.format_for_cache(entry)
        self.assertIn("---", fmt)
        self.assertIn("agent_reach output", fmt)

        entries = [entry, entry]
        batch = toin.batch_format(entries, max_tokens=500)
        self.assertGreater(len(batch), 0)
        for b in batch:
            self.assertIn("---", b)

        merged = toin.merge_results(entry, entry)
        self.assertIsInstance(merged, FormattedEntry)
        self.assertGreater(merged.normalized_tokens, 0)
        merged2 = toin.merge_results(None, entry)
        self.assertEqual(merged2.entry_id, entry.entry_id)

        entry2 = toin.ingest(source, "traceback\nError: something failed\n", mime_type="text/plain")
        self.assertIn("error", entry2.tags)




# ═══════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.environ.setdefault("NEXUS_THOMPSON_ROUTING", "false")
    os.environ.setdefault("NEXUS_CLUSTER_ROUTING", "false")
    os.environ.setdefault("NEXUS_REFLECTION", "false")
    os.environ.setdefault("NEXUS_LEARNING", "false")

    loader = unittest.TestLoader()
    suite = loader.discover(str(NEXUS_ROOT / "tests"), pattern="test_context_harness.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
