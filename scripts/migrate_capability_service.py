#!/usr/bin/env python3
"""Checked, explicit-target migration 023 runner. No service management."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.capability_catalog import apply_schema, connect_db, migration_sql, load_vector_extension


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _schema(conn):
    return dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"))


def _counts(conn):
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {n: conn.execute('SELECT count(*) FROM "' + n.replace('"', '""') + '"').fetchone()[0]
            for n in names}


def _checks(conn, baseline_fks=None):
    integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise ValueError("database integrity validation failed")
    current_fks = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")]
    if baseline_fks is not None:
        new_fks = set(current_fks) - set(baseline_fks)
        if new_fks:
            raise ValueError(f"migration introduced new foreign key violations: {new_fks}")
    elif current_fks:
        raise ValueError("foreign key validation failed")
    return {"integrity_check": integrity, "foreign_key_check": current_fks}


def migrate(db_path, receipt_path, *, dry_run=False, expected_preimage_sha256=None,
            write_coordinated=False, sql=None):
    """Real execution additionally requires an operator-declared write window.

    Dry runs open the target read-only. A real run holds BEGIN IMMEDIATE across
    backup, preimage validation and DDL. This excludes intervening SQLite writes;
    the receipt also records data_version evidence, not just a main-file hash.
    """
    db, receipt = Path(db_path), Path(receipt_path)
    if not db.is_absolute() or not receipt.is_absolute() or not db.is_file():
        raise ValueError("existing absolute --db and absolute --receipt required")
    if receipt.resolve() == db.resolve() or receipt.exists():
        raise ValueError("receipt must be a new file distinct from the database")
    if not dry_run and not write_coordinated:
        raise ValueError("real migration requires --write-coordinated operator attestation")
    sql = migration_sql() if sql is None else sql
    # Reserve the receipt before acquiring any write connection. If this fails,
    # no migration can be applied. Keep durable failure receipts for diagnosis.
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    result = {"db": str(db), "dry_run": dry_run, "status": "failed", "stage": "backup"}
    live = None
    try:
        live = sqlite3.connect(db.as_uri() + ("?mode=ro" if dry_run else "?mode=rw"),
                               uri=True, timeout=30)
        load_vector_extension(live)
        live.execute("PRAGMA foreign_keys=ON")
        if not dry_run:
            live.execute("BEGIN IMMEDIATE")
        before_schema, before_counts = _schema(live), _counts(live)
        baseline_fks = [tuple(r) for r in live.execute("PRAGMA foreign_key_check")]
        data_before = live.execute("PRAGMA data_version").fetchone()[0]
        # A second reader can back up while our RESERVED lock prevents writers.
        # Calling backup on the write-transaction connection itself can hang.
        with tempfile.TemporaryDirectory(prefix="nexus-capability-migration-") as directory:
            snapshot = Path(directory) / "preimage.sqlite"
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as source:
                load_vector_extension(source)
                with sqlite3.connect(snapshot) as backup:
                    load_vector_extension(backup)
                    source.backup(backup)
            preimage = _hash(snapshot.read_bytes())
            result.update(preimage_sha256=preimage,
                          schema_sha256=_hash(json.dumps(before_schema, sort_keys=True).encode()),
                          migration_sha256=_hash(sql.encode()), data_version_before=data_before)
            result["stage"] = "preimage"
            if expected_preimage_sha256 and preimage != expected_preimage_sha256:
                raise ValueError("consistent preimage checksum mismatch")
            if not dry_run:
                # Retain the pristine preimage durably; trial DDL below modifies
                # only the disposable copy. Never overwrite an earlier backup.
                backup_path = Path(str(receipt) + ".preimage.sqlite")
                backup_fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(backup_fd, "wb") as output, snapshot.open("rb") as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                result["backup_path"] = str(backup_path)
            # Test the entire migration and checks against the consistent copy
            # first. Failed DDL never reaches the live target.
            result["stage"] = "validation"
            with connect_db(snapshot) as trial:
                trial.execute("BEGIN IMMEDIATE")
                status = apply_schema(trial, sql)
                result.update(_checks(trial, baseline_fks))
                after_schema, after_counts = _schema(trial), _counts(trial)
            result["schema_diff"] = {n: ddl for n, ddl in after_schema.items()
                                     if before_schema.get(n) != ddl}
            result["row_count_deltas"] = {n: after_counts.get(n, 0) - before_counts.get(n, 0)
                for n in sorted(before_counts.keys() | after_counts.keys())}
            result["data_version_locked"] = live.execute("PRAGMA data_version").fetchone()[0]
            if not dry_run:
                if (_schema(live) != before_schema or _counts(live) != before_counts
                        or result["data_version_locked"] != data_before):
                    raise ValueError("database changed after preimage capture")
                result["stage"] = "apply"
                status = apply_schema(live, sql)
                _checks(live, baseline_fks)
                # Test receipt serialization and durable write before COMMIT.
                result.update(status=status, stage="commit")
                os.write(fd, json.dumps(result, sort_keys=True, indent=2).encode())
                os.fsync(fd)
                live.commit()
                with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as verify:
                    load_vector_extension(verify)
                    _checks(verify, baseline_fks)
            result.update(status=("dry-run-" + status if dry_run else status), stage="complete")
        return result
    except BaseException as exc:
        if live is not None:
            live.rollback()
        result.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        if live is not None:
            live.close()
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(result, sort_keys=True, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected-preimage-sha256")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--write-coordinated", action="store_true",
                        help="operator confirms an established deployment/write window")
    args = parser.parse_args(argv)
    try:
        result = migrate(args.db, args.receipt, dry_run=args.dry_run,
                         expected_preimage_sha256=args.expected_preimage_sha256,
                         write_coordinated=args.write_coordinated)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
