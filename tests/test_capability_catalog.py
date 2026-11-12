"""Catalog/policy/migration contracts using temporary roots and databases only."""
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.migrate_capability_service import migrate
from server.capability_catalog import (
    CapabilityCatalog, CatalogError, apply_schema, connect_db, initialize_schema, migration_sql,
)
from server.capability_policy import (
    argument_hash, authorize_invocation, eligibility, visibility,
)

PRINCIPAL = {"id": "alice", "harness": "codex", "projects": ["demo"], "verifier": False}


@pytest.fixture
def catalog(tmp_path):
    db = tmp_path / "capabilities.sqlite"
    initialize_schema(db)
    return CapabilityCatalog(db)


def skill(root, name, body=None):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(body or f"---\nname: {name}\ndescription: useful task skill\n---\nFull {name} instructions")
    return folder


def manifest(root, publisher="fixture"):
    return {"version": 1, "skill_roots": [{"publisher": publisher, "root": str(root),
                                              "include_patterns": ["**/SKILL.md"]}], "providers": []}


def traverse(catalog, principal=PRINCIPAL, limit=7, query=None):
    found, cursor, revisions = [], None, set()
    while True:
        page = catalog.list_capabilities(principal, cursor, limit, query)
        found.extend(page["items"])
        revisions.add(page["revision"])
        cursor = page["next_cursor"]
        if cursor is None:
            return found, revisions


def test_every_page_and_nonrecommended_skill(catalog, tmp_path):
    root = tmp_path / "skills"
    for i in range(25):
        skill(root, f"skill-{i:02}")
    revision = catalog.reconcile_sources(manifest(root))
    found, revisions = traverse(catalog)
    assert revisions == {revision}
    assert [x["canonical_id"] for x in found] == [f"fixture:skill-{i:02}" for i in range(25)]
    recommended = {x["canonical_id"] for x in found[:8]}
    chosen = found[-1]["canonical_id"]
    assert chosen not in recommended
    assert "Full skill-24 instructions" in catalog.read_skill(chosen)["content"]
    searched, _ = traverse(catalog, limit=4, query="useful")
    assert [x["canonical_id"] for x in searched] == [x["canonical_id"] for x in found]


def test_publisher_collisions_mirror_and_aliases(catalog, tmp_path):
    root, other = tmp_path / "one", tmp_path / "two"
    skill(root, "same")
    skill(other, "same")
    mirror = tmp_path / "mirror"
    mirror.symlink_to(root, target_is_directory=True)
    m = manifest(root)
    m["skill_roots"] += manifest(mirror)["skill_roots"] + manifest(other, "other")["skill_roots"]
    catalog.reconcile_sources(m)
    found, _ = traverse(catalog)
    assert {d["canonical_id"] for d in found} == {"fixture:same", "other:same"}
    assert set(catalog.get_capability("fixture:same")["aliases"]) == {
        str(root / "same/SKILL.md"), str(mirror / "same/SKILL.md")}
    with connect_db(catalog.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM capability_sources").fetchone()[0] == 3
    m["skill_roots"] = [m["skill_roots"][0], m["skill_roots"][2]]
    catalog.reconcile_sources(m)
    assert str(mirror / "same/SKILL.md") in catalog.get_capability("fixture:same")["aliases"]


def test_versions_assets_and_removed_source(catalog, tmp_path):
    root = tmp_path / "skills"
    folder = skill(root, "one")
    (folder / "asset.txt").write_text("old")
    catalog.reconcile_sources(manifest(root))
    old = catalog.get_capability("fixture:one")
    assert catalog.read_skill("fixture:one", path="asset.txt")["content"] == "old"
    (folder / "asset.txt").write_text("new")
    with pytest.raises(CatalogError, match="VERSION_CHANGED"):
        catalog.read_skill("fixture:one", old["version"], "asset.txt")
    catalog.reconcile_sources(manifest(root))
    new = catalog.get_capability("fixture:one")
    assert old["canonical_id"] == new["canonical_id"] and old["version"] != new["version"]
    assert catalog.get_capability("fixture:one", old["version"]) == old
    catalog.reconcile_sources({"version": 1, "skill_roots": [], "providers": []})
    removed = catalog.get_capability("fixture:one")
    assert removed["availability"] == "unavailable" and removed["reason"] == "SOURCE_REMOVED"
    assert catalog.get_capability("fixture:one", old["version"])["version"] == old["version"]


def test_revision_pinning_visibility_and_cursor_validation(catalog, tmp_path, monkeypatch):
    root = tmp_path / "skills"
    for i in range(3):
        skill(root, str(i))
    m = manifest(root)
    revision = catalog.reconcile_sources(m)
    page = catalog.list_capabilities(PRINCIPAL, limit=1)
    m["skill_roots"][0]["visibility"] = {"principals": ["bob"]}
    catalog.reconcile_sources(m)
    assert catalog.list_capabilities(PRINCIPAL)["items"] == []
    tail = catalog.list_capabilities(PRINCIPAL, page["next_cursor"], limit=10)
    assert tail["revision"] == revision
    assert [x["canonical_id"] for x in tail["items"]] == ["fixture:1", "fixture:2"]
    with pytest.raises(CatalogError, match="INVALID_CURSOR"):
        catalog.list_capabilities({**PRINCIPAL, "id": "bob"}, page["next_cursor"])
    with pytest.raises(CatalogError, match="INVALID_CURSOR"):
        catalog.list_capabilities(PRINCIPAL, page["next_cursor"] + "x")
    monkeypatch.setattr("server.capability_catalog.time.time", lambda: time.time_ns() / 1e9 + 901)
    with pytest.raises(CatalogError, match="CURSOR_EXPIRED"):
        catalog.list_capabilities(PRINCIPAL, page["next_cursor"])


def test_read_rejects_traversal_and_replaced_symlink(catalog, tmp_path):
    root = tmp_path / "skills"
    folder = skill(root, "one")
    (folder / "asset.txt").write_text("content")
    catalog.reconcile_sources(manifest(root))
    outside = tmp_path / "secret"
    outside.write_text("secret")
    for path in ("../secret", str(outside), "unknown"):
        with pytest.raises(CatalogError, match="INVALID_PATH"):
            catalog.read_skill("fixture:one", path=path)
    (folder / "asset.txt").unlink()
    (folder / "asset.txt").symlink_to(outside)
    with pytest.raises(CatalogError, match="INVALID_PATH"):
        catalog.read_skill("fixture:one", path="asset.txt")
    with pytest.raises(CatalogError, match="INVALID_PATH"):
        catalog.reconcile_sources(manifest(root))


def test_failed_reconcile_preserves_revision(catalog, tmp_path):
    root = tmp_path / "skills"
    folder = skill(root, "one")
    rev = catalog.reconcile_sources(manifest(root))
    (folder / "SKILL.md").write_text("---\nname: [invalid\n---\n")
    with pytest.raises(Exception):
        catalog.reconcile_sources(manifest(root))
    assert catalog.list_capabilities(PRINCIPAL)["revision"] == rev
    with pytest.raises(FileNotFoundError):
        catalog.reconcile_sources(manifest(tmp_path / "missing"))
    assert catalog.list_capabilities(PRINCIPAL)["revision"] == rev


def provider_manifest():
    return {"version": 1, "providers": [{"id": "fake", "transport": "fake",
        "tool_pages": [{"tools": [{"name": "read", "inputSchema": {"type": "object"}}]},
                       {"tools": [{"name": "write", "inputSchema": {"type": "object"}}]}],
        "method_policy": {"read": {"allow": True}, "write": {"allow": False}}}]}


def test_materialized_provider_pages_and_validation(catalog):
    m = provider_manifest()
    revision = catalog.reconcile_sources(m)
    found, _ = traverse(catalog, limit=1)
    assert [d["canonical_id"] for d in found] == ["fake:read", "fake:write"]
    assert found[0]["schema_hash"] and found[0]["execution_location"] == "nexus"
    with connect_db(catalog.db_path) as conn:
        assert conn.execute("SELECT real_path FROM capability_sources").fetchall()[0][0] is None
    invalid = provider_manifest()
    invalid["providers"] *= 2
    with pytest.raises(CatalogError, match="DUPLICATE_PROVIDER"):
        catalog.reconcile_sources(invalid)
    m["providers"][0]["requires"] = ["fake"]
    with pytest.raises(CatalogError, match="INVALID_DEPENDENCY"):
        catalog.reconcile_sources(m)
    assert catalog.list_capabilities(PRINCIPAL)["revision"] == revision


def session():
    return {"session_id": "s", "principal_id": "alice", "harness_id": "codex", "project": "demo",
            "status": "active", "expires_at": time.time() + 100,
            "context": {"policy_version": "1", "provider_snapshot": {"fake": "active"}}}


def test_authorization_independent_of_rank_and_scope(catalog):
    catalog.reconcile_sources(provider_manifest())
    read, write = catalog.get_capability("fake:read"), catalog.get_capability("fake:write")
    assert authorize_invocation(PRINCIPAL, session(), read, {})["allowed"]
    assert not authorize_invocation(PRINCIPAL, session(), write, {})["allowed"]
    assert not authorize_invocation({**PRINCIPAL, "harness": "hermes"}, session(), read, {})["allowed"]
    assert not authorize_invocation({**PRINCIPAL, "projects": ["other"]}, session(), read, {})["allowed"]
    assert not authorize_invocation(PRINCIPAL, {**session(), "status": "closed"}, read, {})["allowed"]
    assert visibility(PRINCIPAL, {"availability": "unavailable"})["allowed"]
    assert not eligibility({"principal": PRINCIPAL, "project": "demo"}, read, {})["allowed"]


def test_approval_and_schema_binding(catalog):
    catalog.reconcile_sources(provider_manifest())
    desc = catalog.get_capability("fake:read")
    desc["execution_policy"]["approval_required"] = True
    approval = {"verified": True, "principal_id": "alice", "harness_id": "codex", "session_id": "s",
                "project": "demo", "capability_id": "fake:read", "version": desc["version"],
                "args_hash": argument_hash({}), "expires_at": time.time() + 50}
    approved_session = session()
    approved_session["context"]["_verified_approvals"] = {"approval-1": approval}
    assert authorize_invocation(PRINCIPAL, approved_session, desc, {}, {"id": "approval-1"})["allowed"]
    # A request-body verified flag cannot manufacture trusted approval.
    assert not authorize_invocation(PRINCIPAL, session(), desc, {}, approval)["allowed"]
    for key, value in (("harness_id", "hermes"), ("session_id", "other"), ("version", "old"),
                       ("args_hash", "wrong"), ("expires_at", 0), ("verified", False)):
        invalid_session = session()
        invalid_session["context"]["_verified_approvals"] = {"approval-1": {**approval, key: value}}
        assert not authorize_invocation(PRINCIPAL, invalid_session, desc, {}, "approval-1")["allowed"]
    desc["input_schema"] = {"type": "object", "required": ["name"]}
    assert not authorize_invocation(PRINCIPAL, session(), desc, {}, approval)["allowed"]
    native = {**desc, "kind": "native_action", "adapter_id": "codex", "method": "arbitrary-shell"}
    assert eligibility({"principal": PRINCIPAL, "project": "demo"}, native, {})["reason"] == "INCOMPATIBLE_ADAPTER"


def test_schema_constraints_and_transaction_helper(catalog):
    catalog.reconcile_sources(provider_manifest())
    version = catalog.get_capability("fake:read")["version"]
    with connect_db(catalog.db_path) as conn:
        conn.execute("INSERT INTO capability_sessions VALUES ('s','alice','codex','demo','{}','active',?)",
                     (time.time() + 100,))
        values = ("i", "alice", "s", "fake:read", version, None, "key", "hash", "succeeded", None, None)
        conn.execute("INSERT INTO capability_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
        conn.execute("INSERT INTO capability_events(event_id,invocation_id,session_id,type,payload_json) "
                     "VALUES ('e','i','s','selected','{}')")
        conn.execute("INSERT INTO capability_rewards(invocation_id,verdict,evidence_ref,verified_by) "
                     "VALUES ('i','correct','evidence','trusted')")
    statements = [
        ("UPDATE capability_versions SET content_hash='changed'", ()),
        ("DELETE FROM capability_events", ()),
        ("UPDATE catalog_revision_members SET availability='changed'", ()),
        ("INSERT INTO capability_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)", values),
        ("INSERT INTO capability_rewards(invocation_id,verdict,evidence_ref,verified_by) VALUES ('i','wrong','x','y')", ()),
        ("INSERT INTO capability_beliefs VALUES ('fake:read',?,'1','general',0.5,0)", (version,)),
        ("INSERT INTO capability_beliefs VALUES ('missing','v','1','general',0,0)", ()),
    ]
    for sql, args in statements:
        with pytest.raises(sqlite3.IntegrityError), connect_db(catalog.db_path) as conn:
            conn.execute(sql, args)
    with pytest.raises(RuntimeError), connect_db(catalog.db_path) as conn:
        conn.execute("INSERT INTO capability_sessions VALUES ('rollback','alice','codex','demo','{}','active',1)")
        raise RuntimeError("rollback")
    with connect_db(catalog.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM capability_sessions").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_concurrent_reconcile_serializes(catalog):
    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(lambda _: catalog.reconcile_sources(provider_manifest()), range(2)))
    assert len(set(revisions)) == 2
    assert len(traverse(catalog)[0]) == 2


def test_migration_dry_run_wal_and_receipts(tmp_path):
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE resources(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO resources VALUES (1,'preserve')")
        conn.commit()
        before = db.read_bytes()
        receipt = tmp_path / "dry.json"
        result = migrate(db, receipt, dry_run=True)
        assert result["status"] == "dry-run-applied" and db.read_bytes() == before
        assert conn.execute("SELECT name FROM resources").fetchone()[0] == "preserve"
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='capability_catalog'").fetchone()[0] == 0
        assert result["row_count_deltas"]["resources"] == 0
        assert json.loads(receipt.read_text())["integrity_check"] == ["ok"]
    real = migrate(db, tmp_path / "real.json", write_coordinated=True)
    assert real["status"] == "applied"
    assert initialize_schema(db) == "already-applied"
    assert migrate(db, tmp_path / "again.json", write_coordinated=True)["status"] == "already-applied"


def test_migration_failures_are_atomic(tmp_path):
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE resources(id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO resources VALUES (1)")
    with pytest.raises(ValueError, match="preimage"):
        migrate(db, tmp_path / "bad-hash.json", expected_preimage_sha256="wrong", write_coordinated=True)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db, tmp_path / "ddl-failure.json", sql=migration_sql() + "CREATE BROKEN SQL;", write_coordinated=True)
    with pytest.raises(sqlite3.OperationalError), connect_db(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        apply_schema(conn, migration_sql() + "CREATE BROKEN SQL;")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("resources",)]
        assert conn.execute("SELECT id FROM resources").fetchall() == [(1,)]
    initialize_schema(db)
    with pytest.raises(ValueError, match="checksum"):
        migrate(db, tmp_path / "checksum.json", sql=migration_sql() + "\n", write_coordinated=True)
    with pytest.raises(ValueError, match="write-coordinated"):
        migrate(db, tmp_path / "unsafe.json")
    with pytest.raises(ValueError, match="new file"):
        migrate(db, db, dry_run=True)


def test_retained_backup_and_incomplete_provider_list(catalog, tmp_path):
    db = tmp_path / "backup.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE preserve(value TEXT)")
        conn.execute("INSERT INTO preserve VALUES ('data')")
    result = migrate(db, tmp_path / "backup.json", write_coordinated=True)
    from pathlib import Path
    preimage = Path(result["backup_path"])
    assert hashlib.sha256(preimage.read_bytes()).hexdigest() == result["preimage_sha256"]
    with sqlite3.connect(preimage) as conn:
        assert conn.execute("SELECT * FROM preserve").fetchall() == [("data",)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("preserve",)]
    m = provider_manifest()
    m["providers"][0]["tool_pages"][-1]["nextCursor"] = "not-collected"
    with pytest.raises(CatalogError, match="INCOMPLETE_PROVIDER_LIST"):
        catalog.reconcile_sources(m)


def test_migration_ledger_preconditions(tmp_path):
    db = tmp_path / "ledger.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE schema_migrations(version TEXT)")
    with pytest.raises(ValueError, match="incompatible"):
        initialize_schema(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("schema_migrations",)]
