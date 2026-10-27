import json
import runpy
import subprocess
import sys
import time
from pathlib import Path

import pytest

MANUAL = Path(__file__).parents[1] / "manual"


@pytest.fixture
def usage(monkeypatch):
    monkeypatch.syspath_prepend(str(MANUAL))
    return runpy.run_path(str(MANUAL / "real_usage.py"))


class FakeEngine:
    def snapshot(self):
        return {
            "state": "paused",
            "reason": "paused",
            "last_error": "",
            "queue": 0,
            "counts": {"saved": 3},
            "storage": {"observations": 3},
            "timings": {"total": [0.01, 0.02]},
            "target": {"title": "PRIVATE_CONTENT_MUST_NOT_APPEAR"},
            "latest": {"image_path": "PRIVATE_CONTENT_MUST_NOT_APPEAR"},
        }


def test_metrics_flush_without_private_window_content(usage, tmp_path, monkeypatch):
    recorder = usage["Recorder"](tmp_path, interval=0.05)
    monkeypatch.setattr(usage["Engine"], "_event", lambda *args, **kwargs: None)
    engine = usage["MeasuredEngine"].__new__(usage["MeasuredEngine"])
    engine.recorder = recorder
    engine._event(
        "save",
        "first_frame",
        window_title="PRIVATE_CONTENT_MUST_NOT_APPEAR",
        image_path="PRIVATE_CONTENT_MUST_NOT_APPEAR",
    )
    recorder.start(FakeEngine())
    try:
        deadline = time.monotonic() + 3
        while recorder.status()["samples"] < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        assert recorder.stop({"observations": 7})
    assert not recorder.thread.is_alive()
    metrics_text = (tmp_path / "metrics.jsonl").read_text(encoding="utf-8")
    events_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE_CONTENT" not in metrics_text + events_text
    assert "image_path" not in metrics_text + events_text
    rows = [json.loads(x) for x in metrics_text.splitlines()]
    assert rows[-1]["final"] and rows[-1]["state"] == "paused"
    assert rows[-1]["storage"]["observations"] == 7
    assert rows[-1]["recent_saved_frame_timings"]["total"]["mean_ms"] == 15
    assert json.loads(events_text.splitlines()[0])["reason"] == "first_frame"
    assert recorder.status()["write_errors"] == 0


def test_observation_queue_is_bounded_and_counts_drops(usage, tmp_path):
    recorder = usage["Recorder"](tmp_path)
    try:
        for _ in range(700):
            recorder.note("skip", "near_duplicate")
        assert recorder.events.qsize() == 512
        assert recorder.status()["dropped_events"] == 188
    finally:
        recorder.stop()


def test_rotated_metrics_files_are_bounded(usage, tmp_path):
    writer = usage["JsonLines"](tmp_path / "metrics.jsonl")
    writer.maxBytes = 128
    try:
        for n in range(20):
            writer.write({"n": n, "padding": "x" * 80})
    finally:
        writer.close()
    assert writer.rollovers > 2
    assert len(list(tmp_path.glob("metrics.jsonl*"))) == 3


def test_failed_metrics_write_is_reported(usage, tmp_path, monkeypatch):
    recorder = usage["Recorder"](tmp_path, interval=0.05)

    def fail(item):
        raise OSError("injected")

    monkeypatch.setattr(recorder.metrics_writer, "write", fail)
    recorder.start(FakeEngine())
    try:
        deadline = time.monotonic() + 3
        while recorder.status()["write_errors"] == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        assert recorder.stop()
    assert recorder.status()["write_errors"] > 0


def test_readiness_check_opens_no_gui_and_creates_no_capture_data(tmp_path):
    root = tmp_path / "not-created"
    result = subprocess.run(
        [sys.executable, str(MANUAL / "real_usage.py"), "--output-root", str(root), "--check"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert not json.loads(result.stdout)["capture_starts_automatically"]
    assert not root.exists()


def test_ui_session_starts_stopped_and_closes_all_workers(usage, tmp_path, monkeypatch):
    engines = []

    def serve(engine, store, path, **kwargs):
        engines.append(engine)
        assert engine.snapshot()["state"] == "stopped"
        assert engine.snapshot()["counts"].get("saved", 0) == 0
        assert engine.close().wait(5)

    monkeypatch.setitem(usage["main"].__globals__, "serve", serve)
    usage["main"](["--output-root", str(tmp_path)])
    sessions = list(tmp_path.glob("*/session.json"))
    assert len(sessions) == 1
    report = json.loads(sessions[0].read_text(encoding="utf-8"))
    assert report["ended_cleanly"] and report["diagnostics_complete"]
    assert report["final"]["state"] == "closed"
    assert report["final"]["storage"]["observations"] == 0
    assert all(not t.is_alive() for t in engines[0]._threads)
    assert not list(tmp_path.rglob("*.webp"))
