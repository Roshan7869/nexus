"""T0-1: resolve_db_path must refuse an unwritable DB instead of copying to /tmp."""
import os
from pathlib import Path

import pytest

from server import db


def test_unwritable_db_raises_and_names_path(tmp_path, monkeypatch):
    target = tmp_path / "nexus.db"
    target.write_bytes(b"")
    monkeypatch.setenv("NEXUS_BROKER_DB_PATH", str(target))
    monkeypatch.delenv("NEXUS_DB_PATH", raising=False)

    real_access = os.access

    def fake_access(path, mode, *a, **kw):
        if Path(path) == target and mode == os.W_OK:
            return False
        return real_access(path, mode, *a, **kw)

    monkeypatch.setattr(db.os, "access", fake_access)
    tmp_copy = Path("/tmp/nexus.db")
    existed_before = tmp_copy.exists()

    with pytest.raises(RuntimeError) as exc:
        db.resolve_db_path()

    assert str(target) in str(exc.value)
    assert list(tmp_path.glob("*")) == [target]
    if not existed_before:
        assert not tmp_copy.exists()


def test_no_tmp_path_in_source():
    src = Path(db.__file__).read_text()
    assert "/tmp/nexus.db" not in src
