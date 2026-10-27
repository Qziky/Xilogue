import threading
import weakref
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_engine import Backend, Monitor, wait_for
from test_ocr_storage import document
from test_storage import save

from xilogue.collector.engine import Engine
from xilogue.collector.models import Target
from xilogue.config import Config
from xilogue.processing.ocr import process_pending
from xilogue.storage.store import Store


class LocalOcr:
    def recognize(self, pixels):
        assert pixels.shape == (40, 50, 3)
        return document()


def make_engine(store, hook=None, ocr_factory=None):
    return Engine(
        store.config,
        store,
        Monitor(),
        Backend,
        hook=hook,
        ocr_backend_factory=ocr_factory or (lambda threads: LocalOcr()),
    )


def close(engine, store):
    assert engine.close().wait(5)
    for thread in engine._threads:
        thread.join(1)
        assert not thread.is_alive()
    store.close()


def test_stopped_initialization_never_loads_or_processes_ocr(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    called = threading.Event()

    def factory(threads):
        called.set()
        return LocalOcr()

    engine = make_engine(store, ocr_factory=factory)
    try:
        assert not called.wait(0.3)
        assert store.ocr_stats()["pending"] == 1
        engine.start()
        wait_for(lambda: store.ocr_stats()["done"] == 1)
        assert called.is_set() and store.search("中文")
    finally:
        close(engine, store)


@pytest.mark.parametrize("stage", ["ocr_claimed", "ocr_recognized", "ocr_committing"])
def test_pause_fences_old_ocr_at_each_stage_and_resume_retries(tmp_path, stage):
    store = Store(tmp_path, Config())
    save(store)
    entered, release = threading.Event(), threading.Event()

    def hook(point, job):
        if point == stage and not entered.is_set():
            entered.set()
            assert release.wait(5)

    engine = make_engine(store, hook)
    try:
        engine.start()
        assert entered.wait(3)
        paused = engine.pause()
        if stage == "ocr_committing":
            assert not paused.wait(0.1)
            release.set()
            assert paused.wait(3)
        else:
            assert paused.wait(3)
            # Resume before the old inference completes: its epoch is still invalid.
            assert not store.search("中文")
            engine.start()
            release.set()
        wait_for(lambda: store.ocr_stats()["done"] == 1)
        assert len(store.search("中文")) == 1
    finally:
        release.set()
        close(engine, store)


def test_delete_during_inference_cannot_resurrect_text_or_job(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    entered, release = threading.Event(), threading.Event()

    def hook(point, job):
        if point == "ocr_recognized":
            entered.set()
            assert release.wait(5)

    engine = make_engine(store, hook)
    try:
        engine.start()
        assert entered.wait(3)
        assert store.delete_observation(store.rows()[0]["id"]) == {"status": "deleted"}
        release.set()
        wait_for(lambda: engine.ocr.snapshot()["state"] == "waiting")
        assert not store.search("中文")
        assert not any(store.ocr_stats().values())
    finally:
        release.set()
        close(engine, store)


def test_ocr_failure_is_sanitized_and_does_not_stop_capture(tmp_path, caplog):
    store = Store(tmp_path, Config())
    save(store)

    class BrokenOcr:
        def recognize(self, pixels):
            raise RuntimeError("PRIVATE_OCR_CONTENT")

    engine = make_engine(store, ocr_factory=lambda threads: BrokenOcr())
    try:
        engine.start()
        wait_for(lambda: store.ocr_stats()["failed"] == 1)
        assert engine.snapshot()["state"] == "running"
        assert engine.ocr.snapshot()["last_error"] == "ocr_RuntimeError"
        assert "PRIVATE_OCR_CONTENT" not in caplog.text
    finally:
        close(engine, store)


def test_disabled_ocr_can_be_enabled_while_paused(tmp_path):
    store = Store(tmp_path, Config(ocr_enabled=False))
    save(store)
    engine = make_engine(store)
    try:
        engine.start()
        assert not engine._worker_stop.wait(0.3)
        assert store.ocr_stats()["pending"] == 1
        assert engine.pause().wait(3)
        engine.reload(replace(store.config, ocr_enabled=True))
        engine.start()
        wait_for(lambda: store.ocr_stats()["done"] == 1)
    finally:
        close(engine, store)


def test_explicit_backlog_processing_without_capture(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    try:
        report = process_pending(store, lambda threads: LocalOcr())
        assert report["processed"] == 1 and report["failed"] == 0
        assert store.stats()["observations"] == 1
    finally:
        store.close()


def test_completed_pause_rejects_late_inference_until_explicit_resume(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class BlockingOcr(LocalOcr):
        def recognize(self, pixels):
            calls.append(1)
            entered.set()
            assert release.wait(5)
            return super().recognize(pixels)

    engine = make_engine(store, ocr_factory=lambda threads: BlockingOcr())
    try:
        engine.start()
        assert entered.wait(3)
        assert engine.pause().wait(3)
        release.set()
        wait_for(lambda: store.ocr_stats()["pending"] == 1)
        assert not store.search("中文") and calls == [1]
        engine.start()
        wait_for(lambda: store.ocr_stats()["done"] == 1)
        assert calls == [1, 1]
    finally:
        release.set()
        close(engine, store)


def test_window_switch_does_not_cancel_processing_of_saved_evidence(tmp_path):
    store = Store(tmp_path, Config(poll_interval_seconds=0.01))
    save(store)
    entered, release = threading.Event(), threading.Event()

    def hook(stage, job):
        if stage == "ocr_recognized":
            entered.set()
            assert release.wait(5)

    engine = make_engine(store, hook)
    try:
        engine.start()
        assert entered.wait(3)
        old = engine.snapshot()["generation"]
        engine.monitor.target = Target(88, 89, 90, "second.exe", "safe")
        wait_for(lambda: engine.snapshot()["generation"] != old)
        release.set()
        wait_for(lambda: store.ocr_stats()["done"] == 1)
        assert store.search("中文")
    finally:
        release.set()
        close(engine, store)


@pytest.mark.parametrize("mode", ["capture", "ocr_only"])
def test_exit_waits_for_inference_and_drops_its_result(tmp_path, mode):
    store = Store(tmp_path, Config())
    save(store)
    entered, release = threading.Event(), threading.Event()

    def hook(stage, job):
        if stage == "ocr_recognized":
            entered.set()
            assert release.wait(5)

    engine = make_engine(store, hook)
    try:
        engine.start(mode=mode)
        assert entered.wait(3)
        done = engine.close()
        assert not done.wait(0.1)
        release.set()
        assert done.wait(3)
        assert not store.search("中文")
        assert store.ocr_stats()["pending"] == 1
    finally:
        release.set()
        close(engine, store)


class OcrOnlyMonitor(Monitor):
    def foreground(self):
        raise AssertionError("ocr_only_must_not_inspect_foreground")

    def idle_seconds(self):
        raise AssertionError("ocr_only_must_not_inspect_input")


def forbid_capture():
    raise AssertionError("ocr_only_must_not_create_capture_backend")


def test_ocr_only_processes_saved_sources_with_auto_ocr_disabled_and_releases_model(tmp_path):
    store = Store(tmp_path, Config(ocr_enabled=False))
    items = [save(store), save(store)]
    refs = []

    def factory(threads):
        backend = LocalOcr()
        refs.append(weakref.ref(backend))
        return backend

    engine = Engine(
        store.config, store, OcrOnlyMonitor(), forbid_capture, ocr_backend_factory=factory
    )
    try:
        engine.start(mode="ocr_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert store.ocr_stats()["done"] == 2
        assert {r["id"] for r in store.search("中文")} == {item["id"] for item in items}
        assert engine.snapshot()["mode"] == "ocr_only" and engine.target is None
        assert engine.snapshot()["counts"].get("saved", 0) == 0
        assert store.stats()["observations"] == 2
        assert engine.ocr.snapshot()["pass_finished"]
        assert not engine.ocr.snapshot()["model_loaded"]
        assert len(refs) == 1 and refs[0]() is None
    finally:
        close(engine, store)


def test_empty_or_backoff_only_ocr_pass_does_not_load_models_or_wait_for_retries(tmp_path):
    store = Store(tmp_path, Config())
    engine = Engine(
        store.config,
        store,
        OcrOnlyMonitor(),
        forbid_capture,
        ocr_backend_factory=lambda threads: pytest.fail("no_eligible_jobs"),
    )
    try:
        engine.start(mode="ocr_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        save(store)
        job = store.claim_ocr()
        store.release_ocr(job, "ocr_failed", now=datetime.now(UTC) + timedelta(minutes=1))
        engine.start(mode="ocr_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert store.ocr_stats()["failed"] == 1 and not engine.ocr.snapshot()["model_loaded"]
    finally:
        close(engine, store)


@pytest.mark.parametrize("stage", ["ocr_claimed", "ocr_recognized", "ocr_committing"])
def test_ocr_only_pause_preserves_barrier_and_later_pass_can_resume(tmp_path, stage):
    store = Store(tmp_path, Config())
    save(store)
    entered, release = threading.Event(), threading.Event()

    def hook(point, job):
        if point == stage and not entered.is_set():
            entered.set()
            assert release.wait(5)

    engine = Engine(
        store.config,
        store,
        OcrOnlyMonitor(),
        forbid_capture,
        ocr_backend_factory=lambda threads: LocalOcr(),
        hook=hook,
    )
    try:
        engine.start(mode="ocr_only")
        assert entered.wait(3)
        pause = engine.pause()
        if stage == "ocr_committing":
            assert not pause.wait(0.1)
        else:
            assert pause.wait(2)
        release.set()
        assert pause.wait(3)
        wait_for(lambda: not engine.ocr.snapshot()["model_loaded"])
        assert not store.search("中文") if stage != "ocr_committing" else store.search("中文")
        engine.start(mode="ocr_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert store.ocr_stats()["done"] == 1
    finally:
        release.set()
        close(engine, store)


def test_old_ocr_pass_cannot_pause_a_new_capture_run(tmp_path):
    store = Store(tmp_path, Config())
    engine = make_engine(store)
    try:
        engine.start()
        epoch = engine._processing_generation
        assert engine.pause().wait(3)
        engine.start()
        engine.complete_ocr_pass(epoch)
        assert engine.snapshot()["state"] == "running" and engine.snapshot()["mode"] == "capture"
        assert engine.pause().wait(3)
        with pytest.raises(ValueError):
            engine.start(mode="invalid")
    finally:
        close(engine, store)


def test_capture_pause_drops_model_after_uninterruptible_inference_finishes(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    entered, release = threading.Event(), threading.Event()
    refs = []

    class Blocking(LocalOcr):
        def recognize(self, pixels):
            entered.set()
            assert release.wait(5)
            return super().recognize(pixels)

    def factory(threads):
        backend = Blocking()
        refs.append(weakref.ref(backend))
        return backend

    engine = make_engine(store, ocr_factory=factory)
    try:
        engine.start()
        assert entered.wait(3)
        assert engine.pause().wait(3)
        assert engine.ocr.snapshot()["model_loaded"] and refs[0]() is not None
        release.set()
        wait_for(lambda: not engine.ocr.snapshot()["model_loaded"])
        assert refs[0]() is None and not store.search("中文")
    finally:
        release.set()
        close(engine, store)


def test_failed_ocr_only_pass_finishes_without_repeating_backoff_or_retaining_model(tmp_path):
    store = Store(tmp_path, Config())
    save(store)

    class Failing(LocalOcr):
        def recognize(self, pixels):
            raise RuntimeError("PRIVATE_SYNTHETIC")

    engine = Engine(
        store.config,
        store,
        OcrOnlyMonitor(),
        forbid_capture,
        ocr_backend_factory=lambda threads: Failing(),
    )
    try:
        engine.start(mode="ocr_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        row = store.db.execute("SELECT attempts FROM processing_jobs").fetchone()
        assert row[0] == 1 and store.ocr_stats()["failed"] == 1
        assert not engine.ocr.snapshot()["model_loaded"]
        assert engine.ocr.snapshot()["last_error"] == "ocr_RuntimeError"
        assert engine.ocr.snapshot()["pass_finished"]
    finally:
        close(engine, store)


def test_switching_to_ocr_only_rejects_stale_capture_callbacks_then_can_start_capture(tmp_path):
    store = Store(tmp_path, Config(foreground_stabilize_seconds=0))
    engine = make_engine(store)
    try:
        engine.start()
        wait_for(lambda: engine._backend is not None)
        old = engine._backend
        assert engine.pause().wait(3)
        save(store)
        engine.monitor = OcrOnlyMonitor()
        engine.start(mode="ocr_only")
        old.send(force=True)
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert old.stopped and store.stats()["observations"] == 1
        assert engine.snapshot()["counts"].get("saved", 0) == 0
        engine.monitor = Monitor()
        engine.start()
        wait_for(lambda: engine._backend is not None)
        assert engine.snapshot()["mode"] == "capture" and engine._backend is not old
    finally:
        close(engine, store)


def test_ocr_only_waits_for_available_desktop_and_can_be_paused_while_waiting(tmp_path):
    store = Store(tmp_path, Config())
    save(store)
    monitor = OcrOnlyMonitor()
    monitor.desktop = False
    engine = Engine(
        store.config,
        store,
        monitor,
        forbid_capture,
        ocr_backend_factory=lambda threads: LocalOcr(),
    )
    try:
        engine.start(mode="ocr_only")
        assert not engine.paused.wait(0.25)
        assert store.ocr_stats()["pending"] == 1 and not engine.ocr.snapshot()["model_loaded"]
        assert engine.pause().wait(3)
        monitor.desktop = True
        engine.start(mode="ocr_only")
        wait_for(lambda: engine.snapshot()["state"] == "paused")
        assert store.ocr_stats()["done"] == 1
    finally:
        close(engine, store)
