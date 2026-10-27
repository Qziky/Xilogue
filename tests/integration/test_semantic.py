import threading

import pytest
from test_analysis import Provider, output
from test_engine import wait_for
from test_ocr_storage import complete
from test_ocr_worker import OcrOnlyMonitor, forbid_capture
from test_storage import save

from xilogue.collector.engine import Engine
from xilogue.config import Config
from xilogue.storage.semantic import unit_vector
from xilogue.storage.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path, Config())
    complete(value)
    value.queue_embeddings()
    yield value
    value.close()


@pytest.mark.parametrize("vector", [[], [0, 0], [float("nan")], [True], ["1"], [1e30]])
def test_invalid_vectors_are_rejected(vector):
    with pytest.raises(ValueError):
        unit_vector(vector)


def test_vectors_have_model_revision_provenance_and_source_deletion(store):
    job = store.claim_embedding()
    assert store.finish_embedding(job, [1, 0, 1], "test", "digest")
    hit = store.related("无字面表达", vector=[1, 0, 1], model="test", digest="digest")["results"][0]
    assert hit["basis"] == "local_vector_and_keywords" and hit["cosine"] == pytest.approx(1)
    assert not store.related("无字面表达", vector=[1, 0, 1], model="test", digest="changed")[
        "results"
    ]
    assert store.related("中文")["results"][0]["cosine"] is None
    store.delete_observation(job["evidence"][0]["id"])
    assert not store.related("中文")["results"]
    assert store.db.execute("SELECT count(*) FROM session_vectors").fetchone()[0] == 0
    assert not store.finish_embedding(job, [1, 0, 1], "test", "digest")


def test_revised_source_rejects_inflight_vector_and_job_can_restart(store):
    job = store.claim_embedding()
    save(store)
    store.sync_sessions()
    assert not store.finish_embedding(job, [1, 1], "test", "digest")
    assert store.embedding_stats()["pending"] == 1


def test_embedding_batch_has_pause_barrier_and_releases_provider(store):
    entered, release = threading.Event(), threading.Event()
    providers = []

    def hook(stage, job):
        if stage == "embedding_generated":
            entered.set()
            assert release.wait(5)

    def factory(config):
        value = Provider(config)
        providers.append(value)
        return value

    engine = Engine(
        store.config,
        store,
        OcrOnlyMonitor(),
        forbid_capture,
        analysis_backend_factory=factory,
        hook=hook,
    )
    try:
        engine.start(mode="index_only")
        assert entered.wait(3)
        assert engine.pause().wait(3)
        release.set()
        wait_for(lambda: not engine.analysis.snapshot()["provider_active"])
        assert store.embedding_stats()["pending"] == 1 and providers[0].closed
        assert store.db.execute("SELECT count(*) FROM session_vectors").fetchone()[0] == 0
        engine.start(mode="index_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert store.embedding_stats()["done"] == 1
    finally:
        release.set()
        assert engine.close().wait(5)
        for t in engine._threads:
            t.join(1)


def test_topics_use_actual_claim_dates_and_respect_never_feedback(store):
    store.queue_analysis()
    job = store.claim_analysis()
    store.finish_analysis(job, output(job["evidence"]), "test")
    topics = store.topics("Asia/Shanghai")
    assert topics and topics[0]["sessions"][0]["id"] == job["session_id"]
    store.feedback(job["session_id"], {"kind": "never"})
    assert store.topics("Asia/Shanghai") == []
