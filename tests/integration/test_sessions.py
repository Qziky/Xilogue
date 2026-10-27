import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_ocr_storage import document
from test_storage import frame

from xilogue.collector.models import Target
from xilogue.config import Config
from xilogue.processing.change import Decision
from xilogue.storage.store import SCHEMA_VERSION, Store, encode_webp


def add(store, seconds=0, app="code.exe", title="Recorder - editor"):
    f = replace(
        frame(datetime.now(UTC) - timedelta(hours=1) + timedelta(seconds=seconds)),
        target=Target(1, 2, 3, app, title),
    )
    return store.save(f, encode_webp(f.pixels, 78), Decision(True, "fixture", {}))


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path, Config())
    yield value
    value.close()


def test_grouping_uses_app_gap_and_title_and_is_idempotent_after_restart(store):
    first, second = add(store), add(store, 20)
    add(store, 30, "msedge.exe", "Database article")
    add(store, 40)
    add(store, 400)
    add(store, 410, title="Completely different topic")
    progress = store.sync_sessions()
    assert progress["assigned"] == 6
    rows = store.sessions_page()["results"]
    assert len(rows) == 5
    combined = next(s for s in rows if s["source_count"] == 2)
    assert {r["id"] for r in store.session(combined["id"])["sources"]} == {
        first["id"],
        second["id"],
    }
    assert combined["representative_id"] == second["id"]
    assert store.sync_sessions()["assigned"] == 0
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_manual_split_merge_atomicity_and_preservation_during_rebuild(store):
    items = [add(store, i * 5) for i in range(4)]
    original = store.sessions_page()["results"][0]["id"]
    with pytest.raises(ValueError):
        store.split_session(original, items[0]["id"])
    left, right = store.split_session(original, items[2]["id"])
    assert store.session(left)["source_count"] == store.session(right)["source_count"] == 2
    assert store.session(original) is None
    store.rebuild_sessions()
    assert {r["id"] for r in store.sessions_page()["results"]} == {left, right}

    def fail(stage):
        if stage == "before_session_edit_commit":
            raise OSError("injected")

    store.fault = fail
    with pytest.raises(OSError):
        store.merge_sessions([left, right])
    assert store.session(left) and store.session(right)
    store.fault = lambda _: None
    merged = store.merge_sessions([left, right])
    assert store.session(merged)["source_count"] == 4
    assert store.session(left) is None and store.session(right) is None


def test_source_deletion_invalidates_entire_session_before_file_cleanup(store):
    items = [add(store, i) for i in range(3)]
    ident = store.sessions_page()["results"][0]["id"]

    def fail(stage):
        if stage == "delete_marked":
            raise OSError("injected")

    store.fault = fail
    assert store.delete_observation(items[1]["id"])["status"] == "cleanup_pending"
    assert store.session(ident) is None
    assert store.sync_sessions()["assigned"] == 2
    assert sum(s["source_count"] for s in store.sessions_page()["results"]) == 2


def test_missing_source_invalidates_metadata_and_ocr_updates_revision(store):
    item = add(store)
    ident = store.sessions_page()["results"][0]["id"]
    revision = store.session(ident)["revision"]
    assert store.finish_ocr(store.claim_ocr(), document())
    assert store.session(ident)["revision"] > revision
    store.image_path(item["image_path"]).unlink()
    assert store.session(ident) is None
    assert store.sessions_page()["results"] == []


def test_interrupted_grouping_rolls_back_and_can_resume(store):
    add(store)

    def fail(stage):
        if stage == "before_session_commit":
            raise sqlite3.OperationalError("injected")

    store.fault = fail
    with pytest.raises(sqlite3.Error):
        store.sync_sessions()
    assert store.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    store.fault = lambda _: None
    assert store.sync_sessions()["assigned"] == 1
