from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_analysis import output
from test_ocr_storage import complete, document
from test_storage import save

from xilogue.config import Config
from xilogue.storage.reviews import day_window
from xilogue.storage.store import Store


@pytest.fixture
def prepared(tmp_path):
    store = Store(tmp_path, Config())
    complete(store)
    store.queue_analysis()
    job = store.claim_analysis()
    store.finish_analysis(job, output(job["evidence"]), "test")
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    yield store, job["session_id"], day
    store.close()


def test_local_calendar_windows_handle_dst_and_chinese_midnight():
    start, end = day_window("2026-03-08", "America/New_York")
    assert (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() == 23 * 3600
    assert day_window("2026-10-27", "Asia/Shanghai")[0] == "2026-10-26T16:00:00+00:00"
    with pytest.raises(ValueError):
        day_window("invalid", "UTC")
    with pytest.raises(ValueError):
        day_window("2026-10-27", "../../private")


def test_review_is_source_grounded_and_feedback_updates_are_preserved(prepared):
    store, ident, day = prepared
    assert not store.review(day, "Asia/Shanghai")["generated"]
    result = store.generate_review(day, "Asia/Shanghai")
    assert len(result["cards"]) == 1 and result["cards"][0]["claims"]
    store.feedback(ident, {"correction": "这是用户修正", "kind": "later"})
    store.feedback(ident, {"hidden": True})
    assert store.review(day, "Asia/Shanghai")["cards"] == []
    hidden = store.review(day, "Asia/Shanghai", include_hidden=True)["cards"][0]
    assert hidden["correction"] == "这是用户修正" and hidden["feedback"] == "later"
    store.feedback(ident, {"hidden": False, "kind": "useful"})
    assert store.review(day, "Asia/Shanghai")["cards"][0]["feedback"] == "useful"
    store.feedback(ident, {"kind": "never"})
    assert not store.review(day, "Asia/Shanghai")["cards"]


def test_deletion_removes_review_cards_feedback_and_all_related_text(prepared):
    store, ident, day = prepared
    store.generate_review(day, "Asia/Shanghai")
    store.feedback(ident, {"correction": "私人修正"})
    source = store.session(ident)["sources"][0]["id"]
    store.delete_observation(source)
    assert store.review(day, "Asia/Shanghai", include_hidden=True)["cards"] == []
    assert store.db.execute("SELECT count(*) FROM review_feedback").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM review_cards").fetchone()[0] == 0


def test_review_failure_rolls_back_previous_snapshot(prepared):
    store, ident, day = prepared
    before = store.generate_review(day, "Asia/Shanghai")

    def fail(stage):
        if stage == "before_review_commit":
            raise OSError("injected")

    store.fault = fail
    with pytest.raises(OSError):
        store.generate_review(day, "Asia/Shanghai")
    assert store.review(day, "Asia/Shanghai")["cards"] == before["cards"]


def test_cross_midnight_session_review_uses_only_claims_from_selected_day(tmp_path):
    store = Store(tmp_path, Config())
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    start, _ = day_window(day, "Asia/Shanghai")
    try:
        a = save(store, datetime.fromisoformat(start) - timedelta(seconds=1))
        assert store.finish_ocr(store.claim_ocr(), document("前一天内容"))
        b = save(store, datetime.fromisoformat(start) + timedelta(seconds=1))
        assert store.finish_ocr(store.claim_ocr(), document("当天内容"))
        store.queue_analysis()
        job = store.claim_analysis()
        payload = output(job["evidence"])
        payload["claims"] = [
            {"text": e["text"], "source_id": e["id"], "quote": e["text"]} for e in job["evidence"]
        ]
        assert store.finish_analysis(job, payload, "test")
        card = store.generate_review(day, "Asia/Shanghai")["cards"][0]
        assert card["claims"][0]["source_id"] == b["id"] and a["id"] != b["id"]
        assert "前一天" not in card["summary"]
    finally:
        store.close()
