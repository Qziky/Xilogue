from dataclasses import replace
from datetime import UTC, datetime, timedelta

from test_ocr_storage import complete
from test_storage import save

from xilogue.config import Config
from xilogue.storage.store import Store


def test_automatic_waits_for_stability_ocr_and_preserves_retry_budget(tmp_path):
    store = Store(tmp_path, Config(ai_enabled=True, semantic_enabled=True))
    try:
        complete(store)
        store.queue_automatic()
        assert store.analysis_stats()["pending"] == 0
        later = datetime.now(UTC) + timedelta(seconds=120)
        store.queue_automatic(later)
        assert store.analysis_stats()["pending"] == 1
        assert store.embedding_stats()["pending"] == 1
        with store.db:
            store.db.execute("UPDATE analysis_jobs SET status='failed',attempts=3")
        store.queue_automatic(later)
        assert store.db.execute("SELECT attempts FROM analysis_jobs").fetchone()[0] == 3
        save(store)
        store.sync_sessions()
        assert store.stable_sessions(later) == []
        assert store.claim_analysis(automatic=True) is None
        assert store.claim_embedding(automatic=True) is None
    finally:
        store.close()


def test_automatic_disabled_does_not_enqueue_models(tmp_path):
    store = Store(tmp_path, Config())
    try:
        complete(store)
        store.queue_automatic(datetime.now(UTC) + timedelta(hours=1))
        assert store.analysis_stats()["pending"] == store.embedding_stats()["pending"] == 0
        store.config = replace(store.config, ai_enabled=True)
        store.queue_automatic(datetime.now(UTC) + timedelta(hours=1))
        assert store.analysis_stats()["pending"] == 1
    finally:
        store.close()
