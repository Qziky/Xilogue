import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_storage import save

from xilogue.config import Config
from xilogue.processing.ocr_types import OcrDocument, OcrLine
from xilogue.storage.store import SCHEMA_VERSION, Store


def document(text="中文测试 Hello std::vector<int> 100%_ 数据库"):
    return OcrDocument("test/local", (OcrLine(text, ((0, 0), (40, 0), (40, 20), (0, 20)), 0.99),))


def complete(store, text=None):
    result = save(store)
    job = store.claim_ocr()
    assert job and job["id"] == result["id"]
    assert store.finish_ocr(job, document(text) if text else document())
    return result, job


def test_literal_short_chinese_code_and_normalized_search(tmp_path):
    with_store = Store(tmp_path, Config())
    try:
        result, _ = complete(with_store)
        for query in ("中", "数据", "中文测试", "HELLO", "std::vector<int>", "100%_", '"'):
            rows = with_store.search(query)
            assert bool(rows) == (query != '"')
            if rows:
                assert rows[0]["id"] == result["id"]
        assert not with_store.search("不存在")
        assert not with_store.search("Hello OR secret")
        assert not with_store.search("hello", app="other.exe")
        assert with_store.search("hello", app="SAFE.EXE")
        assert not with_store.search(
            "hello", after=(datetime.now(UTC) + timedelta(days=1)).isoformat()
        )
        with pytest.raises(ValueError):
            with_store.search("hello", after="2026-01-01")
        detail = with_store.document(result["id"])
        assert json.loads(detail["lines_json"])[0]["confidence"] == 0.99
        assert detail["text"] == document().text
    finally:
        with_store.close()


@pytest.mark.parametrize("stage", ["delete_marked", "delete_file_removed", "missing"])
def test_deletion_or_missing_source_removes_derived_data_before_file_cleanup(tmp_path, stage):
    store = Store(tmp_path, Config())
    try:
        result, stale = complete(store)
        if stage == "missing":
            store.image_path(result["image_path"]).unlink()
            store.recover()
        else:

            def fail(point):
                if point == stage:
                    raise OSError("injected")

            store.fault = fail
            assert store.maintenance(now=datetime.now(UTC) + timedelta(days=8))["errors"] == 1
        assert not store.search("中文") and store.document(result["id"]) is None
        assert store.ocr_stats() == dict.fromkeys(("pending", "running", "done", "failed"), 0)
        assert store.db.execute("SELECT count(*) FROM ocr_fts").fetchone()[0] == 0
        assert not store.finish_ocr(stale, document())
    finally:
        store.close()


def test_ocr_commit_failure_rolls_back_text_index_and_done_state(tmp_path):
    store = Store(tmp_path, Config())
    try:
        save(store)
        job = store.claim_ocr()

        def fail(stage):
            if stage == "before_ocr_commit":
                raise OSError("injected")

        store.fault = fail
        with pytest.raises(OSError):
            store.finish_ocr(job, document())
        assert not store.search("中文")
        assert store.ocr_stats()["running"] == 1
        store.fault = lambda stage: None
        assert store.finish_ocr(job, document())
    finally:
        store.close()


def test_restart_reclaims_interrupted_job_and_rejects_old_lease(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    old = store.claim_ocr()
    store.close()
    store = Store(tmp_path, Config())
    try:
        job = store.claim_ocr()
        assert old["token"] != job["token"] and job["attempts"] == 0
        assert not store.finish_ocr(old, document())
        assert store.finish_ocr(job, document())
    finally:
        store.close()


def test_retry_limit_and_cancelled_attempt_not_consumed(tmp_path):
    store = Store(tmp_path, Config())
    try:
        save(store)
        job = store.claim_ocr()
        store.release_ocr(job)
        now = datetime.now(UTC) + timedelta(seconds=1)
        for attempt in range(3):
            job = store.claim_ocr(now)
            assert job["attempts"] == attempt
            store.release_ocr(job, "ocr_failed", now)
            assert store.claim_ocr(now) is None
            now += timedelta(seconds=61)
        assert store.claim_ocr(now) is None
        assert store.retry_ocr() == 1
        assert store.claim_ocr(now)
    finally:
        store.close()


def test_invalid_coordinates_are_never_indexed(tmp_path):
    store = Store(tmp_path, Config())
    try:
        save(store)
        job = store.claim_ocr()
        line = OcrLine("text", ((0, 0), (100, 0), (100, 5), (0, 5)), 1)
        with pytest.raises(ValueError):
            store.finish_ocr(job, OcrDocument("test", (line,)))
        assert not store.search("text")
    finally:
        store.close()


def downgrade_fixture_to_v1(store):
    with store.db:
        for name in ("source_created", "source_invalidated", "ocr_insert", "ocr_delete"):
            store.db.execute(f"DROP TRIGGER {name}")
        for name in ("ocr_fts", "ocr_results", "processing_jobs"):
            store.db.execute(f"DROP TABLE {name}")
        store.db.execute("PRAGMA user_version=1")


def test_v1_migration_backfills_and_failure_leaves_old_schema_and_image(tmp_path):
    store = Store(tmp_path, Config())
    item = save(store)
    downgrade_fixture_to_v1(store)
    store.close()

    def fail(stage):
        if stage == "before_schema_commit":
            raise OSError("injected migration")

    with pytest.raises(OSError):
        Store(tmp_path, Config(), fault=fail)
    with sqlite3.connect(tmp_path / "memory.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='ocr_results'").fetchall()
    store = Store(tmp_path, Config())
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.image_path(item["image_path"]).is_file()
        assert store.ocr_stats()["pending"] == 1
    finally:
        store.close()
