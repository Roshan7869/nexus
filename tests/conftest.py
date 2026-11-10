"""conftest.py — v2 fixtures: 7 RRF backends, in-memory DB, mocks for all external calls."""
import sys, os, json, sqlite3, time, hashlib, uuid, threading, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Generator
import pytest

def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False,
                     help="Run integration tests that hit real backends")
    parser.addoption("--benchmark", action="store_true", default=False,
                     help="Run performance benchmark tests")

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
    if not config.getoption("--benchmark"):
        skip_benchmark = pytest.mark.skip(reason="need --benchmark option to run")
        for item in items:
            if "benchmark" in item.keywords:
                item.add_marker(skip_benchmark)

NEXUS_ROOT = Path("/home/roshan/nexus")
sys.path.insert(0, str(NEXUS_ROOT))

# ── DB isolation (plan S0) ───────────────────────────────────────────────
# server/db.py:39 resolve_db_path() already honours NEXUS_DB_PATH, but
# server/learning.py and server/context_harness/cache.py hardcoded the live
# path until this change, so every route/record/execute test wrote to
# db/nexus.db — routing_log carries 71 rows with ide='pytest' as evidence.
# This must run before any `server.*` import: get_db_path() caches its first
# resolution for the life of the process, so a later override is a no-op.
PROD_DB = (NEXUS_ROOT / "db" / "nexus.db").resolve()
TEST_DB = (Path(tempfile.mkdtemp(prefix="nexus-test-")) / "nexus.db").resolve()
if PROD_DB.exists():
    _src = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    _dst = sqlite3.connect(str(TEST_DB))
    with _dst:
        _src.backup(_dst)          # atomic snapshot, safe alongside a live writer
    _src.close()
    _dst.close()
# FORCED, not defaulted: NEXUS_BROKER_DB_PATH outranks NEXUS_DB_PATH, so a
# stray export from a developer shell must not win.
os.environ["NEXUS_BROKER_DB_PATH"] = str(TEST_DB)
os.environ["NEXUS_DB_PATH"] = str(TEST_DB)

# A 596 MB snapshot per run is real disk: without this the previous seven runs
# left 4.1 GB of nexus-test-* directories behind. Remove on interpreter exit.
import atexit, shutil as _shutil
atexit.register(lambda: _shutil.rmtree(Path(TEST_DB).parent, ignore_errors=True))


@pytest.fixture(scope="session", autouse=True)
def _hermetic_db_guard():
    """Fail loudly if anything resolved back to the production database."""
    from server.db import get_db_path
    resolved = Path(get_db_path()).resolve()
    assert resolved == TEST_DB, f"tests resolved to {resolved}, expected {TEST_DB}"
    assert resolved != PROD_DB, "tests resolved to the PRODUCTION database"
    yield

# Tests must be hermetic. These are FORCED, not defaulted: `setdefault` is a no-op
# when the variable is already exported (a developer shell or .env that sources the
# production flags), and server/learning.py caches these into module globals at import
# time — so a leaked `true` silently flips the suite into production config and the
# "disabled" assertions fail. Deterministic tests require deterministic env.
os.environ["NEXUS_THOMPSON_ROUTING"] = "false"
os.environ["NEXUS_CLUSTER_ROUTING"] = "false"
os.environ["NEXUS_REFLECTION"] = "false"
os.environ["NEXUS_LEARNING"] = "false"


# ── Schema ──────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY, name TEXT, description TEXT, body_text TEXT,
    resource_type TEXT, cluster TEXT, status TEXT DEFAULT 'active',
    file_path TEXT, repo_name TEXT,
    tier INTEGER DEFAULT 2, token_count INTEGER DEFAULT 100,
    times_correct INTEGER DEFAULT 0, times_wrong INTEGER DEFAULT 0,
    times_triggered INTEGER DEFAULT 0,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS resource_sections (
    id INTEGER PRIMARY KEY, resource_id INTEGER, heading TEXT, content TEXT,
    sort_order INTEGER DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS user_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT, scope TEXT DEFAULT '',
    project TEXT DEFAULT 'default', task_type TEXT DEFAULT 'general',
    bundles_json TEXT DEFAULT '[]', tools_json TEXT DEFAULT '[]',
    created_by TEXT DEFAULT 'engine', created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS resource_policies (
    resource_id INTEGER PRIMARY KEY,
    execution_mode TEXT NOT NULL DEFAULT 'review_only',
    side_effect_level TEXT NOT NULL DEFAULT 'read',
    mode_allowance TEXT NOT NULL DEFAULT 'both',
    stage_allowance TEXT NOT NULL DEFAULT '[]',
    allowed_ides TEXT NOT NULL DEFAULT '[]',
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    fallback_priority INTEGER NOT NULL DEFAULT 50,
    confidence_floor REAL NOT NULL DEFAULT 0.55,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

FIXTURE_RESOURCES = [
    (1, "LLM Agent", "A large language model agent", "llm agent using tensorflow for inference", "skill", "general", "active", 1, 300, 10, 1, 50, "2026-06-15T10:00:00+00:00"),
    (2, "React Dashboard", "Web dashboard built with React", "react components with css grid layout", "skill", "frontend_ui", "active", 2, 200, 8, 2, 30, "2026-06-14T10:00:00+00:00"),
    (3, "Docker Deploy", "CI/CD deployment pipeline", "docker kubernetes deploy automation", "command", "devops_infra", "active", 2, 150, 5, 0, 20, "2026-06-13T10:00:00+00:00"),
    (4, "Trading Bot", "Crypto trading bot", "trading strategy market entry exit signal", "agent", "general", "active", 2, 250, 3, 1, 15, "2026-06-10T10:00:00+00:00"),
    (5, "Research Paper", "Benchmark evaluation paper", "research paper analysis benchmark methodology", "doc", "knowledge_wiki", "active", 2, 400, 12, 0, 25, "2026-06-12T10:00:00+00:00"),
    (6, "Python SDK", "General dev SDK", "python sdk library package module cli", "tool", "general", "active", 2, 180, 7, 1, 35, "2026-06-11T10:00:00+00:00"),
    (7, "Old Resource", "Stale test resource", "legacy deprecated content not used", "skill", "general", "active", 2, 50, 0, 5, 0, "2025-01-01T00:00:00+00:00"),
    (8, "Nexus Web Search", "Web search via NEXUS", "web search scraper spider fetcher", "mcp", "general", "active", 1, 200, 15, 0, 60, "2026-06-20T10:00:00+00:00"),
    (9, "Memory Graph", "Memory graph storage", "memory vector graph neuron network", "skill", "architect", "active", 2, 350, 6, 1, 40, "2026-06-18T10:00:00+00:00"),
]
FIXTURE_SECTIONS = [
    (1, 1, "Overview", "tensorflow neural network training pipeline", 0),
    (2, 1, "Usage", "machine learning model inference", 1),
    (3, 2, "Setup", "install npm and configure tailwind react", 0),
    (4, 3, "Deploy", "docker compose kubernetes deployment", 0),
    (5, 4, "Strategy", "crypto market entry exit risk management", 0),
    (6, 5, "Abstract", "novel approach to benchmark evaluation analysis", 0),
    (7, 6, "API", "python sdk library module cli interface", 0),
    (8, 8, "Config", "web scraper spider configuration settings", 0),
    (9, 9, "Memory", "vector memory graph network storage", 0),
]
FIXTURE_EDGES = [
    (1, 2, 0.8, "co_triggered"), (1, 3, 0.5, "co_triggered"),
    (2, 3, 0.6, "co_triggered"), (4, 1, 0.3, "co_triggered"),
    (5, 6, 0.7, "co_triggered"), (8, 1, 0.9, "co_triggered"),
    (9, 1, 0.4, "co_triggered"),
]
FIXTURE_BUNDLES = [
    (1, "core_infra", "core_infra", 10, 1), (2, "web_dev_bundle", "project_type", 30, 1),
    (3, "devops_bundle", "project_type", 30, 1), (4, "trading_bundle", "project_type", 30, 1),
    (5, "research_bundle", "project_type", 40, 1), (6, "general_dev_bundle", "project_type", 50, 1),
    (1, "ai_ml_bundle", "project_type", 30, 0), (8, "core_infra", "core_infra", 10, 1),
    (9, "general_dev_bundle", "project_type", 50, 0),
]
FIXTURE_CLASSIFICATIONS = [
    (1, "ai-ml", 0.92, "tensorflow", 8, '{"ai-ml":0.92,"web-dev":0.05}', 1),
    (2, "web-dev", 0.88, "react", 6, '{"web-dev":0.88,"general-dev":0.12}', 1),
    (3, "devops", 0.85, "docker", 5, '{"devops":0.85,"general-dev":0.15}', 1),
    (4, "trading", 0.78, "crypto", 4, '{"trading":0.78,"ai-ml":0.22}', 1),
    (5, "research", 0.91, "benchmark", 7, '{"research":0.91,"knowledge-wiki":0.09}', 1),
    (6, "general-dev", 0.65, "python", 3, '{"general-dev":0.65,"web-dev":0.35}', 1),
    (8, "general-dev", 0.70, "scraper", 4, '{"general-dev":0.70,"web-dev":0.30}', 1),
    (9, "general-dev", 0.75, "memory", 5, '{"general-dev":0.75,"ai-ml":0.25}', 1),
]


class _NoCloseConnection:
    def __init__(self, conn): self._conn = conn
    def __getattr__(self, name): return getattr(self._conn, name)
    def close(self): pass


def make_db(extra_schema: str = "") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA_SQL)
    if extra_schema:
        conn.executescript(extra_schema)
    return conn


def insert_fixtures(conn: sqlite3.Connection) -> None:
    for r in FIXTURE_RESOURCES:
        conn.execute(
            "INSERT INTO resources (id, name, description, body_text, resource_type, cluster, status, tier, token_count, times_correct, times_wrong, times_triggered, last_used_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    for s in FIXTURE_SECTIONS:
        conn.execute("INSERT INTO resource_sections (id, resource_id, heading, content, sort_order) VALUES (?,?,?,?,?)", s)
    for e in FIXTURE_EDGES:
        conn.execute("INSERT INTO resource_edges (source_id, target_id, weight, edge_type) VALUES (?,?,?,?)", e)
    for b in FIXTURE_BUNDLES:
        conn.execute("INSERT INTO bundle_membership (resource_id, bundle_name, bundle_kind, priority, is_canonical) VALUES (?,?,?,?,?)", b)
    for c in FIXTURE_CLASSIFICATIONS:
        conn.execute("INSERT INTO classification (resource_id, project_type, confidence, dominant_keyword, match_count, scores, model_version) VALUES (?,?,?,?,?,?,?)", c)
    conn.commit()


@contextmanager
def mock_ro_conn(conn: sqlite3.Connection) -> Generator:
    """Patch cache.get_ro_conn to return *conn* (close-safe)."""
    noc = _NoCloseConnection(conn)
    with patch("server.context_harness.cache.get_ro_conn", return_value=noc):
        yield


@contextmanager
def mock_rw_conn(conn: sqlite3.Connection) -> Generator:
    noc = _NoCloseConnection(conn)
    with patch("server.context_harness.cache._get_rw_conn", return_value=noc):
        yield


@contextmanager
def mock_nexus_conn(conn: sqlite3.Connection) -> Generator:
    """Patch nexus_bridge.get_conn to return *conn* (close-safe)."""
    noc = _NoCloseConnection(conn)
    with patch("server.context_harness.nexus_bridge.get_conn", return_value=noc):
        yield


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = make_db()
    insert_fixtures(conn)
    yield conn
    conn.close()


@pytest.fixture
def engine(db: sqlite3.Connection):
    """ContextHarnessEngine with mocked DB."""
    from server.context_harness.engine import ContextHarnessEngine
    with mock_ro_conn(db), mock_rw_conn(db):
        e = ContextHarnessEngine(db_path=":memory:")
        yield e


@pytest.fixture
def fusor():
    from server.context_harness.fusion import fuse, rank_by_relevance
    return fuse, rank_by_relevance


@pytest.fixture
def sample_nexus_results():
    from server.context_harness.models import HarnessResult
    return [
        HarnessResult(source="nexus", content="LLM Agent: tensorflow ml agent", score=0.92, metadata={"id": 1, "type": "skill"}),
        HarnessResult(source="nexus", content="React Dashboard: frontend UI", score=0.88, metadata={"id": 2, "type": "skill"}),
        HarnessResult(source="nexus", content="Python SDK: library package", score=0.65, metadata={"id": 6, "type": "tool"}),
    ]


@pytest.fixture
def sample_internet_results():
    from server.context_harness.models import HarnessResult
    return [
        HarnessResult(source="agent-reach", content="GitHub: tensorflow repos", score=0.85, metadata={"platform": "github"}),
        HarnessResult(source="agent-reach", content="Web: ml articles", score=0.70, metadata={"platform": "web"}),
    ]


@pytest.fixture
def sample_memory_results():
    from server.context_harness.models import HarnessResult
    return [
        HarnessResult(source="learning-note", content="session memory for ML agent", score=0.60, metadata={"id": 101}),
        HarnessResult(source="learning-note", content="previous query about react hooks", score=0.55, metadata={"id": 102}),
        HarnessResult(source="gcode-memory", content="vector memory graph pattern", score=0.80, metadata={"id": 103}),
    ]


def make_result(source: str, content: str, score: float, **meta):
    from server.context_harness.models import HarnessResult
    return HarnessResult(source=source, content=content, score=score, metadata=meta)


# ── Platform mocks ──────────────────────────────────────────────────────

@pytest.fixture
def mock_agent_reach():
    """Mock agent_reach.doctor() and route_to_channel() to avoid CLI probes."""
    from server.context_harness import agent_reach as ar
    fake_channels = [
        {"name": "github", "description": "GitHub search", "tier": 0, "backends": ["gh"], "probe": lambda: True},
        {"name": "web", "description": "Web pages", "tier": 0, "backends": ["jina_reader"], "probe": lambda: True},
        {"name": "youtube", "description": "YouTube subtitles", "tier": 0, "backends": ["yt-dlp"], "probe": lambda: False},
    ]
    fake_doctor = {
        "github": {"status": "ok", "name": "GitHub search", "tier": 0, "backends": ["gh"], "active_backend": "gh"},
        "web": {"status": "ok", "name": "Web pages", "tier": 0, "backends": ["jina_reader"], "active_backend": "jina_reader"},
    }
    with patch.object(ar, "CHANNELS", fake_channels), \
         patch.object(ar, "doctor", return_value=fake_doctor), \
         patch.object(ar, "route_to_channel", return_value=fake_channels[0]):
        yield


@pytest.fixture
def mock_gcode_unavailable():
    """Make gcode_backend always fall back to learning notes."""
    with patch("server.context_harness.gcode_backend._gcode_available", return_value=False), \
         patch("server.context_harness.gcode_backend.GCODE_SOCKET", Path("/nonexistent/gcode.sock")):
        yield


@pytest.fixture
def mock_gcode_available():
    """Make gcode CLI available."""
    fake_binary = MagicMock()
    fake_binary.exists.return_value = True
    with patch("server.context_harness.gcode_backend._gcode_available", return_value=True), \
         patch("server.context_harness.gcode_backend.GCODE_BINARY", fake_binary), \
         patch("server.context_harness.gcode_backend._cli_exec", return_value='{"memory": "graph data"}'):
        yield
