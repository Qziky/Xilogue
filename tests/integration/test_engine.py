import threading
import time
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from xilogue.collector.engine import Engine
from xilogue.collector.models import CaptureFrame, Target
from xilogue.collector.policy import inspect_target
from xilogue.config import Config
from xilogue.storage.store import Store


def wait_for(condition, seconds=3):
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "condition timed out"
        time.sleep(0.005)


class Monitor:
    def __init__(self):
        self.target = Target(1, 2, 3, "safe.exe", "safe")
        self.desktop = True
        self.idle = 0

    def foreground(self):
        return self.target

    def desktop_available(self):
        return self.desktop

    def idle_seconds(self):
        return self.idle


class Backend:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self, target, generation, on_frame, on_error, accept_frame):
        self.target, self.generation = target, generation
        self.on_frame, self.on_error, self.accept = on_frame, on_error, accept_frame
        self.started = True

    def send(self, value=128, force=False):
        if force or self.accept():
            self.on_frame(
                CaptureFrame(
                    np.full((80, 100, 3), value, np.uint8),
                    self.target,
                    self.generation,
                    datetime.now(UTC),
                    time.perf_counter(),
                )
            )

    def finished(self):
        return self.stopped

    def stop(self):
        self.stopped = True


@pytest.fixture
def rig(tmp_path):
    config = Config(
        sample_interval_seconds=0.001,
        poll_interval_seconds=0.005,
        foreground_stabilize_seconds=0,
        event_capacity=3,
        maintenance_seconds=0.02,
    )
    store, monitor, backends = Store(tmp_path, config), Monitor(), []

    def factory():
        backend = Backend()
        backends.append(backend)
        return backend

    engine = Engine(config, store, monitor, factory)
    try:
        yield engine, store, monitor, backends
    finally:
        assert engine.close().wait(5), "engine failed to close"
        for thread in engine._threads:
            thread.join(1)
            assert not thread.is_alive()
        store.close()


def start(rig):
    engine, _, _, backends = rig
    count = len(backends)
    engine.start()
    wait_for(lambda: len(backends) > count and backends[-1].started)
    return backends[-1]


def test_default_stopped_pause_resume_and_stale_callback(rig):
    engine, store, _, backends = rig
    assert engine.snapshot()["state"] == "stopped" and not backends
    backend = start(rig)
    backend.send()
    wait_for(lambda: len(store.rows()) == 1)
    assert engine.pause().wait(2)
    backend.send(255, force=True)
    time.sleep(0.03)
    assert len(store.rows()) == 1 and backend.stopped
    second = start(rig)
    second.send()
    wait_for(lambda: len(store.rows()) == 2)
    assert engine.snapshot()["state"] == "running"


@pytest.mark.parametrize("stage", ["dequeued", "prepared", "encoded", "committing"])
def test_pause_barrier_at_controlled_processing_stage(rig, stage):
    engine, store, _, _ = rig
    entered, release = threading.Event(), threading.Event()

    def hook(point, frame):
        if point == stage:
            entered.set()
            assert release.wait(4)

    engine.hook = hook
    try:
        start(rig).send()
        assert entered.wait(2)
        done = engine.pause()
        if stage == "committing":
            assert not done.wait(0.05)
        else:
            assert done.wait(2)
        release.set()
        assert done.wait(2)
        time.sleep(0.03)
        assert not store.rows(True)
        assert store.stats()["image_bytes"] == 0
    finally:
        release.set()


@pytest.mark.parametrize("block", ["excluded", "desktop", "idle", "missing", "reused_handle"])
def test_final_privacy_gate(rig, block):
    engine, store, monitor, _ = rig
    entered, release = threading.Event(), threading.Event()

    def hook(point, frame):
        if point == "encoded":
            entered.set()
            assert release.wait(4)

    engine.hook = hook
    try:
        start(rig).send()
        assert entered.wait(2)
        if block == "excluded":
            monitor.target = replace(monitor.target, app_name="Bitwarden.EXE")
        elif block == "desktop":
            monitor.desktop = False
        elif block == "idle":
            monitor.idle = 100
        elif block == "missing":
            monitor.target = None
        else:
            monitor.target = replace(monitor.target, process_started=4)
        release.set()
        time.sleep(0.05)
        assert not store.rows(True)
    finally:
        release.set()


def test_title_change_does_not_restart_or_reset_reference(rig):
    engine, store, monitor, backends = rig
    backend = start(rig)
    backend.send()
    wait_for(lambda: len(store.rows()) == 1)
    monitor.target = replace(monitor.target, title="new safe title")
    time.sleep(0.03)
    backend.send()
    wait_for(lambda: engine.snapshot()["counts"].get("skipped", 0) == 1)
    assert len(backends) == 1 and len(store.rows()) == 1


def test_queue_overload_is_bounded_and_counted(rig):
    engine, _, _, _ = rig
    entered, release = threading.Event(), threading.Event()

    def hook(point, frame):
        if point == "dequeued":
            entered.set()
            assert release.wait(4)

    engine.hook = hook
    try:
        backend = start(rig)
        backend.send()
        assert entered.wait(2)
        for _ in range(10):
            backend.send(force=True)
        snap = engine.snapshot()
        assert snap["queue"] == 2 and snap["counts"]["queue_full"] == 8
        assert engine.events.qsize() <= 3 and snap["counts"]["event_dropped"] > 0
        engine.pause()
    finally:
        release.set()


def test_storage_failure_does_not_advance_reference(rig, monkeypatch):
    engine, store, _, _ = rig
    original = store.save

    def fail(*args, **kwargs):
        raise OSError("private title must not escape")

    monkeypatch.setattr(store, "save", fail)
    backend = start(rig)
    backend.send()
    wait_for(lambda: engine.snapshot()["counts"].get("errors", 0) > 0)
    assert "private" not in str(engine.snapshot())
    monkeypatch.setattr(store, "save", original)
    backend.send()
    wait_for(lambda: len(store.rows()) == 1)
    assert store.rows()[0]["capture_reason"] == "first_frame"


def test_reload_only_when_paused_and_exclusions_apply(rig):
    engine, _, monitor, _ = rig
    start(rig)
    with pytest.raises(RuntimeError):
        engine.reload(Config())
    assert engine.pause().wait(2)
    engine.reload(replace(engine.config, excluded_apps=("safe.exe",)))
    assert inspect_target(monitor, engine.config)[1] == "excluded"
    engine.start()
    wait_for(lambda: engine.snapshot()["reason"] == "excluded")


def test_maintenance_runs_while_paused(rig):
    engine, store, _, _ = rig
    backend = start(rig)
    backend.send()
    wait_for(lambda: len(store.rows()) == 1)
    assert engine.pause().wait(2)
    with store.lock, store.db:
        store.db.execute("UPDATE observations SET captured_at_utc='2000-01-01T00:00:00+00:00'")
    wait_for(lambda: len(store.rows(True)) == 0)


def test_backend_failure_has_retry_throttle(rig):
    from xilogue.collector.models import CaptureError

    engine, _, _, backends = rig
    backend = start(rig)
    backend.on_error(CaptureError("synthetic_failure"))
    time.sleep(0.1)
    assert backend.stopped and len(backends) == 1
    wait_for(lambda: len(backends) == 2, seconds=2)


def test_both_gates_reject_excluded_before_any_capture(rig):
    engine, store, monitor, backends = rig
    monitor.target = replace(monitor.target, app_name="BITWARDEN.EXE")
    engine.start()
    wait_for(lambda: engine.snapshot()["reason"] == "excluded")
    assert backends == [] and store.rows() == []


def test_stop_failure_does_not_claim_pause_complete(rig, monkeypatch):
    engine, _, _, _ = rig
    backend = start(rig)
    original = backend.stop

    def fail():
        raise RuntimeError("synthetic_stop_failure")

    monkeypatch.setattr(backend, "stop", fail)
    done = engine.pause()
    assert not done.wait(0.1)
    assert engine.snapshot()["state"] == "pausing"
    monkeypatch.setattr(backend, "stop", original)
    assert done.wait(2)


def test_unexpected_supervisor_error_fails_closed(rig, monkeypatch):
    engine, _, _, _ = rig
    backend = start(rig)

    def fail():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(backend, "finished", fail)
    assert engine.paused.wait(3)
    assert backend.stopped and engine.snapshot()["state"] == "paused"


def test_save_counters_are_inside_pause_barrier(rig, monkeypatch):
    engine, store, _, _ = rig
    entered, release = threading.Event(), threading.Event()
    original = store.stats

    def stats():
        if threading.current_thread().name == "memory-process":
            entered.set()
            assert release.wait(4)
        return original()

    monkeypatch.setattr(store, "stats", stats)
    try:
        start(rig).send()
        assert entered.wait(2)
        done = engine.pause()
        assert not done.wait(0.05)
        release.set()
        assert done.wait(2)
        assert engine.snapshot()["counts"]["saved"] == 1
    finally:
        release.set()
