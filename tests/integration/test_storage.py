from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from xilogue.collector.models import CaptureFrame, Target
from xilogue.config import Config
from xilogue.processing.change import Decision
from xilogue.storage.store import StorageError, Store, encode_webp


def frame(at=None):
    return CaptureFrame(
        np.full((40, 50, 3), 128, np.uint8),
        Target(1, 2, 3, "safe.exe", "private test title"),
        1,
        at or datetime.now(UTC),
        100,
    )


def save(store, at=None, encoded=None):
    f = frame(at)
    return store.save(f, encoded or encode_webp(f.pixels, 78), Decision(True, "first_frame", {}))


def test_save_real_webp_and_times(tmp_path):
    import cv2

    store = Store(tmp_path, Config())
    at = datetime.now(UTC) - timedelta(seconds=3)
    result = save(store, at)
    row = store.rows()[0]
    assert row["captured_at_utc"] < row["created_at_utc"]
    assert not ("private" in row["image_path"] or ":" in row["image_path"])
    assert cv2.imread(str(store.image_path(result["image_path"]))).shape == (40, 50, 3)
    assert store.stats()["observations"] == 1
    store.close()


@pytest.mark.parametrize(
    "stage", ["before_file_write", "before_file_replace", "after_file_replace", "before_db_commit"]
)
def test_write_failures_compensated(tmp_path, stage):
    store = Store(tmp_path, Config())

    def fail(point):
        if point == stage:
            raise OSError("injected")

    store.fault = fail
    with pytest.raises(OSError):
        save(store)
    assert store.rows(True) == []
    assert store.stats()["image_bytes"] == store.stats()["temporary_bytes"] == 0
    store.close()


def test_recovery_orphan_temporary_missing_and_unowned(tmp_path):
    store = Store(tmp_path, Config())
    a, b = save(store), save(store)
    store.image_path(a["image_path"]).unlink()
    with store.db:
        store.db.execute("DELETE FROM observations WHERE id=?", (b["id"],))
    temp = store.image_path(b["image_path"] + ".tmp")
    temp.write_bytes(b"unfinished")
    unowned = store.images / "notes.txt"
    unowned.write_text("preserve", encoding="utf-8")
    store.close()
    recovered = Store(tmp_path, Config())
    assert recovered.recovery == {
        "temporary_removed": 1,
        "orphan_removed": 1,
        "missing": 1,
        "deleted": 0,
        "errors": 0,
    }
    assert not recovered.rows()
    assert recovered.rows(True)[0]["storage_state"] == "missing"
    assert unowned.exists()
    recovered.close()


@pytest.mark.parametrize("stage", ["delete_marked", "delete_file_removed"])
def test_delete_failure_retried_at_restart(tmp_path, stage):
    store = Store(tmp_path, Config())
    save(store)

    def fail(point):
        if point == stage:
            raise OSError("injected")

    store.fault = fail
    assert store.maintenance(now=datetime.now(UTC) + timedelta(days=8))["errors"] == 1
    assert not store.rows()
    store.close()
    recovered = Store(tmp_path, Config())
    assert recovered.rows(True) == []
    assert recovered.stats()["image_bytes"] == 0
    recovered.close()


def test_quota_single_over_and_oldest_reclaimed(tmp_path):
    store = Store(tmp_path, Config(image_quota_mib=1))
    with pytest.raises(StorageError, match="single_image"):
        save(store, encoded=b"x" * (1024 * 1024 + 1))
    a = save(store, encoded=b"x" * 700_000)
    b = save(store, encoded=b"y" * 700_000)
    assert [x["id"] for x in store.rows()] == [b["id"]]
    assert not store.image_path(a["image_path"]).exists()
    store.close()


def test_failed_reclaim_rejects_new_save(tmp_path, monkeypatch):
    store = Store(tmp_path, Config(image_quota_mib=1))
    save(store, encoded=b"x" * 700_000)

    def fail(path):
        raise PermissionError("denied")

    monkeypatch.setattr(store, "_unlink", fail)
    with pytest.raises(StorageError, match="quota_reclaim_failed"):
        save(store, encoded=b"y" * 700_000)
    assert len(store.rows(True)) == 1
    assert store.rows(True)[0]["storage_state"] == "deleting"
    store.close()


def test_retention_without_new_save_and_path_guard(tmp_path):
    store = Store(tmp_path, Config())
    at = datetime.now(UTC)
    save(store, at)
    store.maintenance(now=at + timedelta(days=7))
    assert not store.rows(True)
    with pytest.raises(StorageError):
        store.image_path("../external.webp")
    store.close()


def test_do_not_claim_unrelated_directory(tmp_path):
    (tmp_path / "unrelated.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(StorageError, match="not_empty"):
        Store(tmp_path, Config())


@pytest.mark.parametrize(
    "stage", ["after_file_replace", "before_db_commit", "delete_marked", "delete_file_removed"]
)
def test_process_crash_recovered(tmp_path, stage):
    import subprocess
    import sys

    script = """
import os, sys
from datetime import UTC, datetime, timedelta
import numpy as np
from xilogue.collector.models import CaptureFrame, Target
from xilogue.config import Config
from xilogue.processing.change import Decision
from xilogue.storage.store import Store, encode_webp
from pathlib import Path
store=Store(Path(sys.argv[1]), Config())
stage=sys.argv[2]
def crash(point):
    if point == stage:
        os._exit(71)
if not stage.startswith('delete'):
    store.fault=crash
f=CaptureFrame(np.full((10,10,3),128,np.uint8),Target(1,2,3,'safe.exe','safe'),
               1,datetime.now(UTC),0)
store.save(f,encode_webp(f.pixels,78),Decision(True,'first_frame',{}))
store.fault=crash
store.maintenance(now=datetime.now(UTC)+timedelta(days=8))
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), stage], timeout=15)
    assert result.returncode == 71
    recovered = Store(tmp_path, Config())
    assert recovered.rows(True) == []
    assert recovered.stats()["image_bytes"] == 0
    recovered.close()


def test_encoder_and_sqlite_failure(tmp_path, monkeypatch):
    import sqlite3

    import cv2

    monkeypatch.setattr(cv2, "imencode", lambda *args: (False, None))
    with pytest.raises(StorageError, match="encode_failed"):
        encode_webp(frame().pixels, 78)
    store = Store(tmp_path, Config())
    store.db.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT else sqlite3.SQLITE_OK
        )
    )
    with pytest.raises(sqlite3.DatabaseError):
        save(store, encoded=b"test")
    store.db.set_authorizer(None)
    assert not store.rows(True) and store.stats()["image_bytes"] == 0
    store.close()


def test_same_directory_cannot_have_two_writers(tmp_path):
    store = Store(tmp_path, Config())
    try:
        with pytest.raises(RuntimeError, match="data_directory_in_use"):
            Store(tmp_path, Config())
    finally:
        store.close()
    reopened = Store(tmp_path, Config())
    reopened.close()
