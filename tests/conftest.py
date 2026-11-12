"""conftest.py — Isolated test database fixture and test harness mocks.

Provides hermetic database isolation using temporary SQLite databases for each test,
preventing any test from reading or modifying the production database.
"""
import sys, os, sqlite3, time, hashlib, uuid, threading, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Generator
import pytest

NEXUS_ROOT = Path("/home/roshan/nexus").resolve()
if str(NEXUS_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXUS_ROOT))
if str(NEXUS_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(NEXUS_ROOT / "server"))

PROD_DB = (NEXUS_ROOT / "db" / "nexus.db").resolve()

# Prevent any import-time resolution to the production DB
_SESSION_FALLBACK_DB = (Path(tempfile.gettempdir()) / "nexus_test_session.db").resolve()
os.environ.setdefault("NEXUS_DB_PATH", str(_SESSION_FALLBACK_DB))
os.environ.setdefault("NEXUS_BROKER_DB_PATH", str(_SESSION_FALLBACK_DB))



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


# ── Schema definitions ───────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL DEFAULT 'skill',
    repo_name TEXT NOT NULL DEFAULT 'unknown',
    file_path TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    tier INTEGER NOT NULL DEFAULT 2,
    embedding BLOB,
    token_count INTEGER DEFAULT 0,
    times_triggered INTEGER NOT NULL DEFAULT 0,
    times_correct INTEGER NOT NULL DEFAULT 0,
    times_wrong INTEGER NOT NULL DEFAULT 0,
    cluster TEXT DEFAULT NULL,
    cluster_confidence REAL DEFAULT 0.0,
    cooldown_until TEXT,
    archive_reason TEXT,
    archived_at TEXT,
    lifecycle_state TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS resource_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    section_order INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    heading TEXT,
    content TEXT NOT NULL DEFAULT '',
    token_count INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES resources(id)
);

CREATE TABLE IF NOT EXISTS bundle_membership (
    resource_id INTEGER NOT NULL,
    bundle_name TEXT NOT NULL,
    bundle_kind TEXT NOT NULL DEFAULT 'capability',
    priority INTEGER NOT NULL DEFAULT 50,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resource_id, bundle_name),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cache_hierarchy (
    layer TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    value BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (layer, cache_key)
);

CREATE TABLE IF NOT EXISTS classification (
    resource_id INTEGER PRIMARY KEY,
    project_type TEXT,
    confidence REAL,
    dominant_keyword TEXT,
    match_count INTEGER,
    scores TEXT,
    model_version INTEGER
);

CREATE TABLE IF NOT EXISTS user_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'task_type',
    task_type TEXT NOT NULL DEFAULT 'general',
    project TEXT NOT NULL DEFAULT 'default',
    note TEXT NOT NULL,
    bundles_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cluster_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    trigger_keywords TEXT NOT NULL DEFAULT '[]',
    resource_types TEXT NOT NULL DEFAULT '[]',
    name_patterns TEXT NOT NULL DEFAULT '[]',
    default_skill TEXT NOT NULL DEFAULT '',
    default_agents TEXT NOT NULL DEFAULT '[]',
    centroid_embedding BLOB,
    auto_activate_threshold REAL DEFAULT 0.35,
    max_resources INTEGER DEFAULT 200,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cluster_membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_name TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES resources(id)
);

CREATE TABLE IF NOT EXISTS routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_summary TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT 'default',
    matched_tier INTEGER DEFAULT 0,
    matched_model TEXT DEFAULT 'unknown',
    matched_skills TEXT DEFAULT '[]',
    outcome TEXT DEFAULT 'routed',
    outcome_source TEXT DEFAULT 'explicit',
    quality REAL DEFAULT 1.0,
    skill_name TEXT DEFAULT '',
    tokens_used INTEGER DEFAULT 0,
    ide TEXT DEFAULT 'unknown',
    action_type TEXT DEFAULT '',
    intent TEXT DEFAULT '',
    route_match INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0.0,
    cost REAL DEFAULT 0.0,
    injected_ctx_len INTEGER DEFAULT 0,
    repeat_count_1h INTEGER DEFAULT 0,
    harness_tag TEXT DEFAULT 'raw-api',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reflection_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    resource_name TEXT NOT NULL,
    resource_id INTEGER,
    expected TEXT,
    actual TEXT,
    root_cause TEXT,
    fix_attempted TEXT,
    fix_successful INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reflection_fixes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER,
    failure_summary TEXT,
    fix_description TEXT,
    times_applied INTEGER DEFAULT 0,
    times_successful INTEGER DEFAULT 0,
    last_applied_at TEXT,
    alternative_resource_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_run_id TEXT NOT NULL,
    phase_id TEXT NOT NULL DEFAULT '',
    subtask_name TEXT NOT NULL DEFAULT '',
    bundle_name TEXT,
    resource_name TEXT NOT NULL,
    resource_type TEXT NOT NULL DEFAULT 'unknown',
    execution_mode TEXT NOT NULL DEFAULT 'review_only',
    side_effect_level TEXT NOT NULL DEFAULT 'read',
    tool_role TEXT NOT NULL DEFAULT 'candidate',
    trace_order INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'selected',
    outcome TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_hash TEXT NOT NULL UNIQUE,
    task_summary TEXT NOT NULL,
    task_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    cluster TEXT DEFAULT NULL,
    hit_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'default',
    model TEXT NOT NULL DEFAULT 'unknown',
    task_type TEXT NOT NULL DEFAULT 'general',
    alpha REAL DEFAULT 1.0,
    beta REAL DEFAULT 1.0,
    total_requests INTEGER DEFAULT 0,
    total_reward REAL DEFAULT 0.0,
    avg_reward REAL DEFAULT 0.0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resource_id INTEGER,
    UNIQUE(provider, model, task_type)
);

CREATE TABLE IF NOT EXISTS ide_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ide TEXT NOT NULL DEFAULT 'unknown',
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    reward REAL NOT NULL DEFAULT 0.0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    task_summary TEXT NOT NULL DEFAULT '',
    cluster TEXT DEFAULT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    sampled_score REAL NOT NULL DEFAULT 0.0,
    benchmark_prior REAL NOT NULL DEFAULT 0.0,
    approach TEXT DEFAULT 'none',
    candidates_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    benchmark TEXT NOT NULL,
    score REAL NOT NULL,
    samples INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, model, benchmark)
);

CREATE TABLE IF NOT EXISTS cluster_affinity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_name TEXT,
    trigger_query TEXT,
    old_affinity REAL,
    new_affinity REAL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    description TEXT
);
"""


def _init_test_database(db_path: Path) -> None:
    """Initialize a test database with baseline schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    
    baseline_file = NEXUS_ROOT / "db" / "migrations" / "BASELINE.sql"
    if baseline_file.exists():
        try:
            conn.executescript(baseline_file.read_text())
        except Exception:
            pass
            
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()


# ── DB Isolation autouse fixture ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path, monkeypatch):
    """Isolate each test in a dedicated temporary database.
    
    Initializes schema from BASELINE.sql and SCHEMA_SQL.
    Sets NEXUS_DB_PATH and NEXUS_BROKER_DB_PATH to the temporary path.
    Overrides server.db.DEFAULT_DB_PATH to protect production.
    Works cleanly for both sync and async tests.
    """
    test_db = (tmp_path / "test_nexus.db").resolve()
    _init_test_database(test_db)

    # Fail closed if somehow pointing to production
    assert test_db != PROD_DB, f"isolated_test_db resolved to production DB: {test_db}"
    assert "tmp" in str(test_db).lower() or "pytest" in str(test_db).lower()

    monkeypatch.setenv("NEXUS_DB_PATH", str(test_db))
    monkeypatch.setenv("NEXUS_BROKER_DB_PATH", str(test_db))

    import server.db
    monkeypatch.setattr(server.db, "DEFAULT_DB_PATH", test_db)
    monkeypatch.setattr(server.db, "get_db_path", server.db.resolve_db_path)
    if hasattr(server.db.get_db_path, "_cached"):
        monkeypatch.delattr(server.db.get_db_path, "_cached", raising=False)

    # Patch cached DB_PATH in any already imported modules
    for mod_name, mod in list(sys.modules.items()):
        if (mod_name.startswith("server") or mod_name.startswith("core")) and hasattr(mod, "DB_PATH"):
            monkeypatch.setattr(mod, "DB_PATH", test_db)

    yield test_db


# ── Legacy and unit-test helper fixtures ─────────────────────────────────

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
    baseline_file = NEXUS_ROOT / "db" / "migrations" / "BASELINE.sql"
    if baseline_file.exists():
        try:
            conn.executescript(baseline_file.read_text())
        except Exception:
            pass
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


@pytest.fixture
def mock_agent_reach():
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
    with patch("server.context_harness.gcode_backend._gcode_available", return_value=False), \
         patch("server.context_harness.gcode_backend.GCODE_SOCKET", Path("/nonexistent/gcode.sock")):
        yield


@pytest.fixture
def mock_gcode_available():
    fake_binary = MagicMock()
    fake_binary.exists.return_value = True
    with patch("server.context_harness.gcode_backend._gcode_available", return_value=True), \
         patch("server.context_harness.gcode_backend.GCODE_BINARY", fake_binary), \
         patch("server.context_harness.gcode_backend._cli_exec", return_value='{"memory": "graph data"}'):
        yield
