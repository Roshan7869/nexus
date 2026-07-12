"""V2 performance benchmarks — target-based latency assertions.

Run: python -m pytest tests/test_v2_performance.py -v --benchmark
"""
import time
import pytest


@pytest.mark.benchmark
class TestNexusBackendPerformance:
    """NEXUS resource search must complete <50ms."""

    def test_search_resources_under_50ms(self, db):
        from server.context_harness.nexus_bridge import search_resources
        from unittest.mock import patch
        with patch("server.context_harness.nexus_bridge.get_conn", return_value=db):
            t0 = time.perf_counter()
            for _ in range(10):
                search_resources("tensorflow", max_results=5)
            dt = ((time.perf_counter() - t0) / 10) * 1000
            assert dt < 50, f"Avg {dt:.2f}ms, expected <50ms"

    def test_search_learnings_under_50ms(self, db):
        from server.context_harness.nexus_bridge import search_learnings
        from unittest.mock import patch
        with patch("server.context_harness.nexus_bridge.get_conn", return_value=db):
            t0 = time.perf_counter()
            for _ in range(10):
                search_learnings("test", limit=5)
            dt = ((time.perf_counter() - t0) / 10) * 1000
            assert dt < 50, f"Avg {dt:.2f}ms, expected <50ms"


@pytest.mark.benchmark
class TestInternetBackendPerformance:
    """agent-reach doctor() must complete <100ms (local probe only)."""

    def test_doctor_under_100ms(self):
        from server.context_harness.agent_reach import doctor
        t0 = time.perf_counter()
        for _ in range(20):
            doctor()
        dt = ((time.perf_counter() - t0) / 20) * 1000
        assert dt < 100, f"Avg {dt:.2f}ms, expected <100ms"

    def test_route_to_channel_under_50ms(self, mock_agent_reach):
        from server.context_harness.agent_reach import route_to_channel
        t0 = time.perf_counter()
        for _ in range(50):
            route_to_channel("github search for tensorflow")
        dt = ((time.perf_counter() - t0) / 50) * 1000
        assert dt < 50, f"Avg {dt:.2f}ms, expected <50ms"


@pytest.mark.benchmark
class TestMemoryBackendPerformance:
    """gcode search via learning-notes fallback must complete <50ms."""

    def test_search_fallback_under_50ms(self, db, mock_gcode_unavailable):
        from server.context_harness.gcode_backend import search
        from unittest.mock import patch
        with patch("server.context_harness.nexus_bridge.get_conn", return_value=db):
            t0 = time.perf_counter()
            for _ in range(10):
                search("test", max_results=5)
            dt = ((time.perf_counter() - t0) / 10) * 1000
            assert dt < 50, f"Avg {dt:.2f}ms, expected <50ms"

    def test_store_fallback_under_50ms(self, db, mock_gcode_unavailable):
        from server.context_harness.gcode_backend import store
        from unittest.mock import patch
        with patch("server.context_harness.nexus_bridge.get_conn", return_value=db):
            t0 = time.perf_counter()
            for _ in range(10):
                store("key", "value", tags=["t"])
            dt = ((time.perf_counter() - t0) / 10) * 1000
            assert dt < 50, f"Avg {dt:.2f}ms, expected <50ms"


@pytest.mark.benchmark
class TestFusionPerformance:
    """Fuse must complete <10ms for 30 total items (10 per backend)."""

    def test_fuse_under_10ms(self, sample_nexus_results, sample_internet_results, sample_memory_results):
        from server.context_harness.fusion import fuse
        n = sample_nexus_results * 3
        i = sample_internet_results * 5
        m = sample_memory_results * 3
        t0 = time.perf_counter()
        for _ in range(50):
            fuse(n, i, m, max_results=10)
        dt = ((time.perf_counter() - t0) / 50) * 1000
        assert dt < 10, f"Avg {dt:.2f}ms, expected <10ms"


@pytest.mark.benchmark
class TestRouterPerformance:
    """HubAndSpokeRouter.route() must complete <50ms (with mocked DB)."""

    def test_router_latency_under_50ms(self, db):
        from server.context_harness.router import HubAndSpokeRouter, RouteContext
        from tests.conftest import mock_ro_conn, mock_rw_conn
        with mock_ro_conn(db), mock_rw_conn(db):
            router = HubAndSpokeRouter(db_path=":memory:")
            t0 = time.perf_counter()
            for _ in range(20):
                router.route(RouteContext(query_text="tensorflow neural network", top_k=5))
            dt = ((time.perf_counter() - t0) / 20) * 1000
            assert dt < 50, f"Avg {dt:.2f}ms, expected <50ms"


@pytest.mark.benchmark
class TestTOINPerformance:
    """TOIN ingest must complete <5ms per entry."""

    def test_ingest_under_5ms(self):
        from server.context_harness.toin import TOIN, ToolOutputSource
        import time, uuid
        toin = TOIN()
        source = ToolOutputSource("skill", "test", str(uuid.uuid4()), time.time(), 10.0)
        t0 = time.perf_counter()
        for _ in range(100):
            toin.ingest(source, "Hello @user, found #results", mime_type="text/markdown")
        dt = ((time.perf_counter() - t0) / 100) * 1000
        assert dt < 5, f"Avg {dt:.2f}ms, expected <5ms"

    def test_batch_format_under_5ms(self):
        from server.context_harness.toin import TOIN, ToolOutputSource, FormattedEntry, ToolOutput
        import time, uuid
        entry = FormattedEntry(
            entry_id=str(uuid.uuid4()),
            tool_output=ToolOutput(
                source=ToolOutputSource("skill", "test", str(uuid.uuid4()), time.time(), 0.0),
                raw_text="test data", structured=None, mime_type="text/plain",
                token_count=5, status="success", error_message=None,
            ),
            normalized_text="test normalized", normalized_tokens=3,
            compression_savings=2, section_title="test output",
            priority=70, tags=["test"],
        )
        t0 = time.perf_counter()
        for _ in range(100):
            TOIN.batch_format([entry], max_tokens=4096)
        dt = ((time.perf_counter() - t0) / 100) * 1000
        assert dt < 5, f"Avg {dt:.2f}ms, expected <5ms"


@pytest.mark.benchmark
class TestCompressionPerformance:
    """Volatile detection must complete <10ms for 1KB text."""

    def test_volatile_detection_under_10ms(self):
        from server.context_harness.compression import VolatileContentDetector
        detector = VolatileContentDetector()
        text = "Started 2024-01-15T10:30:00Z with session_abc123def4567890abcdef12345678. " * 20
        t0 = time.perf_counter()
        for _ in range(50):
            detector.detect_all(text)
        dt = ((time.perf_counter() - t0) / 50) * 1000
        assert dt < 10, f"Avg {dt:.2f}ms, expected <10ms"

    def test_window_packing_under_5ms(self):
        from server.context_harness.compression import WindowPacker, PackedSection
        packer = WindowPacker(max_tokens=8192, min_tokens=4096)
        sections = [
            PackedSection(i, i, f"Section {i}", "Res", "body text here " * 10, 30, 0.9, 0.0, [])
            for i in range(20)
        ]
        for s in sections:
            packer.add_section(s)
        t0 = time.perf_counter()
        for _ in range(100):
            packer.flush()
            for s in sections:
                packer.add_section(s)
        dt = ((time.perf_counter() - t0) / 100) * 1000
        assert dt < 5, f"Avg {dt:.2f}ms, expected <5ms"


@pytest.mark.benchmark
class TestEnginePerformance:
    """Full engine.harness() must complete <200ms (mocked backends)."""

    def test_engine_harness_under_200ms(self, engine, mock_agent_reach, mock_gcode_unavailable):
        from server.context_harness.models import HarnessQuery
        t0 = time.perf_counter()
        for _ in range(10):
            engine.harness(HarnessQuery(query="tensorflow ml react", sources="all", max_results=5))
        dt = ((time.perf_counter() - t0) / 10) * 1000
        assert dt < 200, f"Avg {dt:.2f}ms, expected <200ms"
