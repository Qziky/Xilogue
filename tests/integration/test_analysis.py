import threading
from datetime import UTC, datetime, timedelta

import pytest
from test_engine import wait_for
from test_ocr_storage import complete
from test_ocr_worker import OcrOnlyMonitor, forbid_capture
from test_storage import save

from xilogue.collector.engine import Engine
from xilogue.config import Config
from xilogue.processing.analysis_types import validate_analysis
from xilogue.processing.ollama import OllamaProvider, ProviderError
from xilogue.storage.store import Store


def output(evidence):
    return {
        "summary": "本地数据库相关文字",
        "topics": ["数据库"],
        "keywords": ["中文"],
        "importance": 0.6,
        "confidence": 0.8,
        "uncertainties": ["无法证明用户是否理解"],
        "claims": [
            {
                "text": "画面包含数据库文字",
                "source_id": evidence[0]["id"],
                "quote": evidence[0]["text"][:20],
            }
        ],
    }


class Provider:
    embedding_digest = "test-digest"

    def __init__(self, config):
        self.closed = False

    def analyze(self, evidence):
        return output(evidence)

    def close(self):
        self.closed = True

    def embed(self, texts):
        return [[1.0, 0.0, 1.0] for _ in texts]


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path, Config())
    complete(value)
    value.queue_analysis()
    yield value
    value.close()


def close(engine):
    assert engine.close().wait(5)
    for t in engine._threads:
        t.join(1)
        assert not t.is_alive()


def test_analysis_validated_commit_and_source_deletion_cascade(store):
    job = store.claim_analysis()
    assert store.finish_analysis(job, output(job["evidence"]), "test/local")
    assert store.insight(job["session_id"])["payload"]["claims"]
    store.delete_observation(job["evidence"][0]["id"])
    assert store.insight(job["session_id"]) is None
    for table in ("insights", "insight_sources", "analysis_jobs"):
        assert store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_session_revision_changes_reject_old_analysis_and_make_old_result_unavailable(store):
    job = store.claim_analysis()
    save(store)
    store.sync_sessions()
    assert not store.finish_analysis(job, output(job["evidence"]), "test/local")
    assert store.insight(job["session_id"]) is None
    assert store.analysis_stats()["pending"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"confidence": float("nan")},
        {"importance": True},
        {"extra": "ignored?"},
        {"summary": "x" * 1201},
        {"claims": [{"text": "fake", "source_id": "unknown", "quote": "中文"}]},
    ],
)
def test_untrusted_model_output_requires_schema_and_exact_evidence(store, change):
    job = store.claim_analysis()
    with pytest.raises(ValueError):
        store.finish_analysis(job, output(job["evidence"]) | change, "test/local")
    assert store.insight(job["session_id"]) is None


def test_analysis_database_failure_rolls_back_payload_sources_and_job(store):
    job = store.claim_analysis()

    def fail(stage):
        if stage == "before_analysis_commit":
            raise OSError("injected")

    store.fault = fail
    with pytest.raises(OSError):
        store.finish_analysis(job, output(job["evidence"]), "test/local")
    assert store.insight(job["session_id"]) is None and store.analysis_stats()["running"] == 1
    assert store.db.execute("SELECT count(*) FROM insight_sources").fetchone()[0] == 0


def test_analysis_restart_lease_and_retry_limit(tmp_path):
    store = Store(tmp_path, Config())
    complete(store)
    store.queue_analysis()
    old = store.claim_analysis()
    store.close()
    store = Store(tmp_path, Config())
    try:
        job = store.claim_analysis()
        assert job["token"] != old["token"] and job["attempts"] == 0
        assert not store.finish_analysis(old, output(old["evidence"]), "test")
        store.release_analysis(job)
        for attempt in range(3):
            job = store.claim_analysis(datetime.now(UTC) + timedelta(minutes=2))
            assert job["attempts"] == attempt
            store.release_analysis(job, "provider_failed")
        assert store.claim_analysis(datetime.now(UTC) + timedelta(minutes=3)) is None
    finally:
        store.close()


@pytest.mark.parametrize("stage", ["analysis_claimed", "analysis_generated", "analysis_committing"])
def test_analysis_pause_fence_at_controlled_stages_and_provider_cleanup(store, stage):
    entered, release = threading.Event(), threading.Event()
    providers = []

    def factory(config):
        value = Provider(config)
        providers.append(value)
        return value

    def hook(point, job):
        if point == stage:
            entered.set()
            assert release.wait(5)

    engine = Engine(
        store.config,
        store,
        OcrOnlyMonitor(),
        forbid_capture,
        analysis_backend_factory=factory,
        hook=hook,
    )
    try:
        engine.start(mode="analysis_only")
        assert entered.wait(3)
        done = engine.pause()
        if stage == "analysis_committing":
            assert not done.wait(0.1)
        else:
            assert done.wait(3)
        release.set()
        assert done.wait(3)
        wait_for(lambda: not engine.analysis.snapshot()["provider_active"])
        assert all(p.closed for p in providers)
        if stage != "analysis_committing":
            assert store.analysis_stats()["done"] == 0
        assert engine.snapshot()["counts"].get("saved", 0) == 0
    finally:
        release.set()
        close(engine)


def test_explicit_analysis_does_not_start_ocr_or_capture_and_auto_pauses(store):
    save(store)
    store.sync_sessions()
    store.queue_analysis()
    providers = []

    def factory(config):
        value = Provider(config)
        providers.append(value)
        return value

    engine = Engine(
        store.config,
        store,
        OcrOnlyMonitor(),
        forbid_capture,
        ocr_backend_factory=lambda threads: pytest.fail("analysis_must_not_start_ocr"),
        analysis_backend_factory=factory,
    )
    try:
        assert engine.snapshot()["state"] == "stopped" and engine.paused.is_set()
        assert not providers
        engine.start(mode="analysis_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert store.analysis_stats()["done"] == 1 and providers[0].closed
        assert store.ocr_stats()["pending"] == 1
    finally:
        close(engine)


def test_local_provider_preserves_original_residency_and_rejects_remote_model():
    class Stub(OllamaProvider):
        def __init__(self, original=False, remote=False):
            self.original_flag, self.remote = original, remote
            self.calls = []
            super().__init__(Config())

        def _request(self, method, path, body=None):
            self.calls.append((path, body))
            if path == "/api/ps":
                return {"models": [{"name": Config().ai_model}] if self.original_flag else []}
            if path == "/api/tags":
                return {"models": [{"name": Config().ai_model, "size": 100}]}
            if path == "/api/show":
                return {
                    "capabilities": ["completion"],
                    "remote_host": "cloud" if self.remote else "",
                }
            if path == "/api/generate":
                return {}
            if path == "/api/chat":
                import json

                evidence = json.loads(body["messages"][1]["content"])["untrusted_evidence"]
                return {"done": True, "message": {"content": json.dumps(output(evidence))}}
            raise AssertionError(path)

    evidence = [{"id": "source", "text": "中文数据库测试"}]
    for original in (False, True):
        provider = Stub(original)
        assert validate_analysis(provider.analyze(evidence), evidence)
        provider.close()
        assert any(path == "/api/generate" for path, _ in provider.calls) == (not original)
        body = next(body for path, body in provider.calls if path == "/api/chat")
        assert not body["think"] and "tools" not in body
    provider = Stub(remote=True)
    with pytest.raises(ProviderError):
        provider.analyze(evidence)
    assert all(path != "/api/chat" for path, _ in provider.calls)
    provider.close()
