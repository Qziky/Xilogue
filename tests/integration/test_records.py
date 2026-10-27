from datetime import UTC, datetime, timedelta

import pytest
from test_ocr_storage import complete, document
from test_storage import save

from xilogue.config import Config
from xilogue.storage.store import Store


@pytest.fixture
def store(tmp_path):
    storage = Store(tmp_path, Config())
    yield storage
    storage.close()


def test_keyset_pages_survive_ties_new_captures_deleted_cursor_and_missing_files(store):
    at = datetime.now(UTC) - timedelta(minutes=1)
    items = [save(store, at) for _ in range(8)]
    ids = sorted([item["id"] for item in items], reverse=True)
    first = store.observations_page(limit=2)
    assert [row["id"] for row in first["results"]] == ids[:2]
    assert all(row["ocr_status"] == "pending" for row in first["results"])
    assert "image_path" not in first["results"][0]
    assert store.delete_observation(ids[1])["status"] == "deleted"
    added = save(store)
    missing = next(item for item in items if item["id"] == ids[2])
    store.image_path(missing["image_path"]).unlink()
    seen, cursor = [], first["next_cursor"]
    while cursor:
        page = store.observations_page(limit=2, cursor=cursor)
        seen.extend(row["id"] for row in page["results"])
        cursor = page["next_cursor"]
    assert seen == ids[3:]
    assert added["id"] not in seen
    assert store.latest_observation()["id"] == added["id"]
    store.image_path(added["image_path"]).unlink()
    assert store.latest_observation()["id"] == ids[0]


def test_browse_filters_include_failed_and_unprocessed_sources(store):
    done, _ = complete(store)
    failed = save(store)
    lease = store.claim_ocr()
    store.release_ocr(lease, "ocr_failed")
    pending = save(store)
    assert [r["id"] for r in store.observations_page(status="failed")["results"]] == [failed["id"]]
    assert [r["id"] for r in store.observations_page(status="pending")["results"]] == [
        pending["id"]
    ]
    for query in ("中", "数据", "HELLO", "std::vector<int>"):
        assert [r["id"] for r in store.observations_page(query=query)["results"]] == [done["id"]]
    assert not store.observations_page(query="Hello OR secret")["results"]
    assert not store.observations_page(app="other.exe")["results"]
    assert len(store.observations_page(app="SAFE.EXE")["results"]) == 3
    assert not store.observations_page(after=(datetime.now(UTC) + timedelta(days=1)).isoformat())[
        "results"
    ]
    assert not store.observations_page(before=done["captured_at_utc"])["results"]
    assert [
        r["id"]
        for r in store.observations_page(
            after=done["captured_at_utc"], before=failed["captured_at_utc"]
        )["results"]
    ] == [done["id"]]


@pytest.mark.parametrize(
    "options",
    [
        {"cursor": "not-a-cursor"},
        {"cursor": "x" * 513},
        {"cursor": "W10"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"status": "deleting"},
        {"query": "x" * 513},
        {"after": "2026-01-01"},
        {"after": "2026-01-02T00:00:00Z", "before": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_browse_parameters_are_rejected(store, options):
    with pytest.raises(ValueError):
        store.observations_page(**options)


@pytest.mark.parametrize("stage", ["delete_marked", "delete_file_removed"])
def test_single_delete_clears_derived_data_even_when_physical_cleanup_fails(store, stage):
    item, stale = complete(store)
    other, _ = complete(store, "其他记录")

    def fail(point):
        if point == stage:
            raise OSError("injected")

    store.fault = fail
    assert store.delete_observation(item["id"]) == {"status": "cleanup_pending"}
    assert not store.search("中文")
    assert store.observation(item["id"]) is None
    assert not store.finish_ocr(stale, document())
    assert [r["id"] for r in store.observations_page()["results"]] == [other["id"]]
    assert store.db.execute("SELECT count(*) FROM ocr_fts").fetchone()[0] == 1
    store.fault = lambda _: None
    assert store.maintenance()["errors"] == 0
    assert not store.image_path(item["image_path"]).exists()
    assert store.delete_observation(item["id"]) == {"status": "not_found"}
    assert store.document(other["id"]) is not None


def test_single_retry_does_not_reset_other_failed_or_running_jobs(store):
    failed = [save(store), save(store)]
    for _ in failed:
        job = store.claim_ocr()
        store.release_ocr(job, "ocr_failed")
    running = save(store)
    lease = store.claim_ocr()
    assert lease["id"] == running["id"]
    assert store.retry_ocr(failed[0]["id"]) == 1
    assert store.retry_ocr(running["id"]) == 0
    assert store.retry_ocr("missing") == 0
    assert store.ocr_stats() == {"pending": 1, "running": 1, "done": 0, "failed": 1}
    assert store.delete_observation(running["id"]) == {"status": "deleted"}
    assert not store.finish_ocr(lease, document())
    assert store.retry_ocr(running["id"]) == 0


def test_keyword_preview_includes_matches_later_in_a_long_document(store):
    item, _ = complete(store, "开头" * 300 + "capture_frame" + "末尾" * 100)
    page = store.observations_page(query="capture_frame")
    assert page["results"][0]["id"] == item["id"]
    assert "capture_frame" in page["results"][0]["preview"]
    assert len(page["results"][0]["preview"]) <= 180
