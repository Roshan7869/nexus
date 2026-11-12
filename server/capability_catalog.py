"""Explicit-database, versioned capability catalog. Importing performs no I/O."""
import base64
import fnmatch
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import yaml

from server.capability_policy import visibility

MIGRATION_VERSION = "023_capability_service"
CURSOR_TTL = 900
_CURSOR_KEY = secrets.token_bytes(32)  # Restart invalidates cursors explicitly.


class CatalogError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _json(value).encode()).hexdigest()


def load_vector_extension(conn):
    try:
        if hasattr(conn, "enable_load_extension"):
            conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        return True
    except Exception:
        return False


@contextmanager
def connect_db(db_path):
    """Yield a Row connection; commit on success, rollback on error, always close.

    No fallback path, schema creation, or production configuration import occurs.
    Each call opens a connection, so use file-backed temporary DBs in tests.
    """
    if not db_path:
        raise ValueError("explicit db_path required")
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    load_vector_extension(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def migration_sql():
    return (Path(__file__).resolve().parents[1] / "db/migrations/023_capability_service.sql").read_text()


def _statements(sql):
    pending = ""
    for char in sql:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("incomplete migration SQL")


def apply_schema(conn, sql=None):
    """Apply only 023 within the caller's BEGIN IMMEDIATE transaction.

    Validate the established schema_migrations ledger and store a checksum in
    capability_migrations. Never fabricate historical migration receipts.
    """
    if not conn.in_transaction:
        raise ValueError("migration requires an explicit transaction")
    sql = migration_sql() if sql is None else sql
    checksum = hashlib.sha256(sql.encode()).hexdigest()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "schema_migrations" in tables:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(schema_migrations)")}
        if not {"version", "applied_at", "description"} <= columns:
            raise ValueError("incompatible schema_migrations ledger")
        existing = conn.execute("SELECT version FROM schema_migrations WHERE version LIKE '023%'").fetchall()
    else:
        existing = []
    if "capability_migrations" in tables:
        row = conn.execute("SELECT checksum FROM capability_migrations WHERE version=?",
                           (MIGRATION_VERSION,)).fetchone()
        if row and row[0] == checksum and [r[0] for r in existing] == [MIGRATION_VERSION]:
            # A matching receipt is insufficient if tables/triggers were changed.
            with sqlite3.connect(":memory:") as expected:
                for statement in _statements(sql):
                    expected.execute(statement)
                objects = expected.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
            actual = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"))
            if any(actual.get(name) != ddl for name, ddl in objects):
                raise ValueError("capability schema drift")
            return "already-applied"
        raise ValueError("migration checksum or ledger conflict")
    if existing or any(t.startswith("capability_") or t in (
            "catalog_revisions", "catalog_revision_members", "provider_generations",
            "recommendation_decisions") for t in tables):
        raise ValueError("migration 023 occupied or partial capability schema")
    if "schema_migrations" not in tables:
        conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, "
                     "applied_at REAL NOT NULL DEFAULT (unixepoch()), description TEXT)")
    for statement in _statements(sql):
        conn.execute(statement)
    conn.execute("INSERT INTO capability_migrations(version,checksum) VALUES (?,?)",
                 (MIGRATION_VERSION, checksum))
    conn.execute("INSERT INTO schema_migrations(version,description) VALUES (?,?)",
                 (MIGRATION_VERSION, "Additive shared capability service"))
    for stmt in _statements(sql):
        if "CREATE TABLE" in stmt:
            parts = stmt.split()
            idx = parts.index("TABLE") + 1
            if parts[idx].upper() == "IF":
                idx += 3
            table_name = parts[idx].split("(")[0].strip('"').strip("'")
            if conn.execute(f'PRAGMA foreign_key_check("{table_name}")').fetchall():
                raise ValueError(f"foreign key check failed on {table_name}")
    return "applied"


def initialize_schema(db_path):
    """Explicitly initialize only the shared schema; safe on clean fixtures."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.close()
    with connect_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return apply_schema(conn)


def _validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise CatalogError("INVALID_MANIFEST")
    providers = manifest.get("providers", [])
    ids = [p["id"] for p in providers]
    if len(set(ids)) != len(ids):
        raise CatalogError("DUPLICATE_PROVIDER")
    graph = {p["id"]: p.get("requires", []) for p in providers}
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting or key not in graph:
            raise CatalogError("INVALID_DEPENDENCY")
        if key in visited:
            return
        visiting.add(key)
        for dep in graph[key]:
            visit(dep)
        visiting.remove(key)
        visited.add(key)

    for provider in providers:
        visit(provider["id"])
        if provider.get("transport") not in ("stdio", "http", "streamable_http", "sse", "fake"):
            raise CatalogError("INVALID_TRANSPORT")
        if provider.get("timeout_ms", 30000) <= 0 or provider.get("max_inflight", 1) <= 0:
            raise CatalogError("INVALID_PROVIDER_LIMIT")
        if provider.get("transport") == "stdio":
            argv = provider.get("endpoint_or_argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise CatalogError("INVALID_ARGV")
        if provider.get("credential_ref") and not isinstance(provider["credential_ref"], str):
            raise CatalogError("INVALID_CREDENTIAL_REFERENCE")
    def check_secrets(value):
        if isinstance(value, dict):
            if any(k.lower() in ("token", "password", "secret", "api_key", "authorization", "env") for k in value):
                raise CatalogError("LITERAL_SECRET_FIELD")
            for item in value.values():
                check_secrets(item)
        elif isinstance(value, list):
            for item in value:
                check_secrets(item)
    check_secrets(manifest)
    profiles = manifest.get("harness_profiles", [])
    if len({p["id"] for p in profiles}) != len(profiles):
        raise CatalogError("DUPLICATE_HARNESS")
    for profile in profiles:
        if (not isinstance(profile.get("adapter"), str)
                or not isinstance(profile.get("permitted_native_verbs"), list)):
            raise CatalogError("INVALID_HARNESS_PROFILE")
    policy = manifest.get("recommendation_policy")
    if policy is not None:
        if (not policy.get("version") or not 0 < policy.get("epsilon", 0) <= 1
                or not 0 < policy.get("temperature", 0) < float("inf")):
            raise CatalogError("INVALID_RECOMMENDATION_POLICY")
    for provider in providers:
        reference = provider.get("credential_ref")
        if reference and not reference.startswith(("env:", "keyring:", "vault:")):
            raise CatalogError("INVALID_CREDENTIAL_REFERENCE")
        if not isinstance(provider.get("method_policy", {}), dict):
            raise CatalogError("INVALID_METHOD_POLICY")


def _identity(publisher, key):
    if not publisher or not key or not isinstance(publisher, str) or not isinstance(key, str):
        raise CatalogError("INVALID_IDENTITY")
    return quote(publisher, safe="") + ":" + quote(key, safe="/")


def _skill_descriptors(manifest):
    seen = {}
    for source in manifest.get("skill_roots", []):
        original_root = Path(source["root"])
        root = original_root.resolve(strict=True)
        if not root.is_dir() or not original_root.is_absolute():
            raise CatalogError("INVALID_ROOT")
        paths = set()
        for pattern in source.get("include_patterns", ["**/SKILL.md"]):
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise CatalogError("INVALID_PATTERN")
            paths.update(original_root.glob(pattern))
        excludes = list(source.get("exclude_patterns", []))
        for pattern in excludes:
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise CatalogError("INVALID_PATTERN")
        for original in sorted(paths):
            if any(fnmatch.fnmatch(str(original), pat) for pat in excludes):
                continue
            real = original.resolve(strict=True)
            if not real.is_relative_to(root) or not real.is_file():
                raise CatalogError("INVALID_PATH")
            text = real.read_text()
            metadata = {}
            if text.startswith("---\n"):
                parts = text.split("---", 2)
                if len(parts) != 3:
                    raise CatalogError("INVALID_SKILL_METADATA")
                try:
                    metadata = yaml.safe_load(parts[1])
                except Exception:
                    raise CatalogError("INVALID_SKILL_METADATA")
                if not isinstance(metadata, dict):
                    raise CatalogError("INVALID_SKILL_METADATA")
            key = str(real.parent.relative_to(root))
            key = metadata.get("logical_key", key if key != "." else root.name)
            cid = _identity(source["publisher"], key)
            policy = source.get("visibility", "public")
            dedup = (str(real), source["publisher"], _json(policy))
            alias = str(original)
            if dedup in seen:
                seen[dedup]["aliases"].append(alias)
                continue
            files = {}
            for asset in sorted(real.parent.rglob("*")):
                try:
                    resolved = asset.resolve(strict=True)
                except (OSError, ValueError):
                    continue
                if not resolved.is_relative_to(root):
                    raise CatalogError("INVALID_PATH")
                if resolved.is_file():
                    files[str(asset.relative_to(real.parent))] = _hash(resolved.read_bytes())
            desc = {"kind": "skill", "canonical_id": cid, "capability_id": cid,
                    "publisher": source["publisher"], "logical_key": key,
                    "name": str(metadata.get("name", key)),
                    "description": str(metadata.get("description", "")), "aliases": [alias],
                    "visibility": policy, "requirements": metadata.get("requirements", []),
                    "execution_location": "harness", "adapter_id": None, "schema_hash": None,
                    "availability": "available", "reason": None,
                    "_root": str(real.parent), "_base_root": str(root), "_files": files}
            seen[dedup] = desc
    return list(seen.values())


def _provider_descriptors(manifest):
    descriptors = []
    for provider in manifest.get("providers", []):
        tools = list(provider.get("tools", []))
        pages = provider.get("tool_pages", [])
        for index, page in enumerate(pages):
            if (isinstance(page, dict) and (page.get("nextCursor") or page.get("next_cursor"))
                    and index == len(pages) - 1):
                raise CatalogError("INCOMPLETE_PROVIDER_LIST")
            tools.extend(page["tools"] if isinstance(page, dict) else page)
        for tool in tools:
            key = tool.get("logical_key", tool.get("name"))
            publisher = provider.get("publisher", provider["id"])
            cid = _identity(publisher, key)
            schema = tool.get("input_schema", tool.get("inputSchema", {"type": "object"}))
            from jsonschema import Draft202012Validator
            Draft202012Validator.check_schema(schema)
            method = tool.get("method", tool.get("name", key))
            policy = provider.get("method_policy", {}).get(method, {})
            descriptors.append({"kind": "tool", "canonical_id": cid, "capability_id": cid,
                "publisher": publisher, "logical_key": key, "name": tool.get("name", key),
                "description": tool.get("description", ""), "aliases": tool.get("aliases", []),
                "provider_id": provider["id"], "method": method, "input_schema": schema,
                "schema_hash": _hash(schema), "requirements": provider.get("requires", []),
                "execution_location": "nexus", "adapter_id": None,
                "execution_policy": {**policy, "methods": policy.get("methods", [method])},
                "visibility": tool.get("visibility", provider.get("visibility", "public")),
                "availability": tool.get("availability", "available"), "reason": tool.get("reason")})
    return descriptors


def _public(descriptor):
    return {k: v for k, v in descriptor.items() if not k.startswith("_")}


class CapabilityCatalog:
    def __init__(self, db_path):
        if not db_path:
            raise ValueError("explicit db_path required")
        self.db_path = db_path
        self._revision_members_cache = {}

    def reconcile_sources(self, manifest):
        """Stage local files and materialized provider lists, then publish atomically."""
        _validate_manifest(manifest)
        staged = _skill_descriptors(manifest) + _provider_descriptors(manifest)
        ids = [d["canonical_id"] for d in staged]
        if len(ids) != len(set(ids)):
            raise CatalogError("IDENTITY_COLLISION")
        for desc in staged:
            # Aliases and source paths describe provenance, not content identity.
            content = {k: v for k, v in desc.items() if k not in ("aliases", "_root", "availability", "reason")}
            desc["version"] = _hash(content)
        with connect_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO catalog_revisions(manifest_hash) VALUES (?)", (_hash(manifest),))
            revision = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for desc in staged:
                cid, version = desc["canonical_id"], desc["version"]
                retained = [r[0] for r in conn.execute(
                    "SELECT original_path FROM capability_sources WHERE capability_id=? "
                    "AND original_path IS NOT NULL", (cid,))]
                desc["aliases"] = sorted(set(desc["aliases"] + retained))
                conn.execute("INSERT INTO capability_catalog VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(capability_id) DO UPDATE SET current_version=excluded.current_version, "
                    "metadata_json=excluded.metadata_json", (cid, desc["kind"], desc["publisher"],
                    desc["logical_key"], version, _json(_public(desc))))
                conn.execute("INSERT OR IGNORE INTO capability_versions "
                    "(capability_id,version,content_hash,descriptor_json) VALUES (?,?,?,?)",
                    (cid, version, version, _json(desc)))
                for alias in desc["aliases"] or [cid]:
                    source_id = _hash([desc["publisher"], alias])
                    previous = conn.execute("SELECT capability_id FROM capability_sources WHERE source_id=?",
                                            (source_id,)).fetchone()
                    if previous and previous[0] != cid:
                        raise CatalogError("SOURCE_IDENTITY_CONFLICT")
                    conn.execute("INSERT OR IGNORE INTO capability_sources VALUES (?,?,?,?,NULL,?)",
                        (source_id, cid, str(Path(alias).resolve()) if desc["kind"] == "skill" else None,
                         alias if desc["kind"] == "skill" else None, _json({"publisher": desc["publisher"]})))
                conn.execute("INSERT INTO catalog_revision_members VALUES (?,?,?,?,?)",
                             (revision, cid, version, desc["availability"], _json(_public(desc))))
            for row in conn.execute("SELECT capability_id,current_version,metadata_json FROM capability_catalog").fetchall():
                if row["capability_id"] not in ids:
                    old = json.loads(row["metadata_json"])
                    old.update(availability="unavailable", reason="SOURCE_REMOVED")
                    conn.execute("UPDATE capability_catalog SET metadata_json=? WHERE capability_id=?",
                                 (_json(old), row["capability_id"]))
                    conn.execute("INSERT INTO catalog_revision_members VALUES (?,?,?,?,?)",
                                 (revision, row["capability_id"], row["current_version"], "unavailable", _json(old)))
            self._revision_members_cache.clear()
            return revision

    def list_capabilities(self, principal, cursor=None, limit=100, query=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise CatalogError("INVALID_LIMIT")
        binding = _hash([principal, query])
        after, expires, revision = "", time.time() + CURSOR_TTL, None
        if cursor:
            try:
                payload, signature = cursor.split(".")
                if not hmac.compare_digest(hmac.new(_CURSOR_KEY, payload.encode(), "sha256").hexdigest(), signature):
                    raise ValueError()
                data = json.loads(base64.urlsafe_b64decode(payload))
                revision, after, expires = data["revision"], data["after"], data["expires"]
                if data["binding"] != binding:
                    raise ValueError()
            except Exception:
                raise CatalogError("INVALID_CURSOR") from None
            if expires <= time.time():
                raise CatalogError("CURSOR_EXPIRED")
        with connect_db(self.db_path) as conn:
            if revision is None:
                revision = conn.execute("SELECT max(revision) FROM catalog_revisions").fetchone()[0]
            if revision is None:
                return {"items": [], "next_cursor": None, "revision": 0}
            if not conn.execute("SELECT 1 FROM catalog_revisions WHERE revision=?", (revision,)).fetchone():
                raise CatalogError("CURSOR_EXPIRED")
            if after == "" and revision in self._revision_members_cache:
                cached_items = self._revision_members_cache[revision]
            else:
                rows = conn.execute("SELECT metadata_json FROM catalog_revision_members "
                                    "WHERE revision=? AND capability_id>? ORDER BY capability_id", (revision, after))
                cached_items = [json.loads(row[0]) for row in rows]
                if after == "":
                    self._revision_members_cache[revision] = cached_items
            items = []
            for desc in cached_items:
                if not visibility(principal, desc)["allowed"]:
                    continue
                if query and query.casefold() not in _json(desc).casefold():
                    continue
                items.append(desc)
                if len(items) > limit:
                    break
        next_cursor = None
        if len(items) > limit:
            items = items[:limit]
            data = {"revision": revision, "after": items[-1]["canonical_id"],
                    "expires": expires, "binding": binding}
            payload = base64.urlsafe_b64encode(_json(data).encode()).decode()
            next_cursor = payload + "." + hmac.new(_CURSOR_KEY, payload.encode(), "sha256").hexdigest()
        return {"items": items, "next_cursor": next_cursor, "revision": revision}

    def get_capability(self, id, version=None):
        with connect_db(self.db_path) as conn:
            if version is None:
                row = conn.execute("SELECT metadata_json FROM capability_catalog WHERE capability_id=?", (id,)).fetchone()
            else:
                row = conn.execute("SELECT descriptor_json FROM capability_versions WHERE capability_id=? AND version=?",
                                   (id, version)).fetchone()
            if not row:
                raise CatalogError("NOT_FOUND")
            return _public(json.loads(row[0]))

    def read_skill(self, id, version=None, path="SKILL.md"):
        desc = self.get_capability(id, version)
        if desc["kind"] != "skill":
            raise CatalogError("NOT_A_SKILL")
        with connect_db(self.db_path) as conn:
            row = conn.execute("SELECT descriptor_json FROM capability_versions WHERE capability_id=? AND version=?",
                               (id, desc["version"])).fetchone()
        pinned = json.loads(row[0])
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or path not in pinned["_files"]:
            raise CatalogError("INVALID_PATH")
        root = Path(pinned["_root"])
        base_root = Path(pinned.get("_base_root", root))
        try:
            target = (root / relative).resolve(strict=True)
            if not target.is_relative_to(base_root):
                raise CatalogError("INVALID_PATH")
            data = target.read_bytes()
        except OSError:
            raise CatalogError("VERSION_CHANGED") from None
        if _hash(data) != pinned["_files"][path]:
            raise CatalogError("VERSION_CHANGED")
        return {"capability_id": id, "canonical_id": id, "version": desc["version"],
                "path": path, "content": data.decode("utf-8"), "content_hash": _hash(data),
                "provenance": {"publisher": desc["publisher"], "aliases": desc["aliases"]}}
