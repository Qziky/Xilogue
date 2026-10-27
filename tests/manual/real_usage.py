"""User-driven real-workload measurement; never starts capture automatically.

uv run --locked python tests/manual/real_usage.py
Each launch gets an independent ignored data/real-usage/<run>/ directory.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import queue
import sys
import threading
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

import numpy as np
import psutil
from windows_acceptance import environment

from xilogue.collector.engine import Engine
from xilogue.collector.windows import WindowsMonitor
from xilogue.config import Config, ConfigError, default_data_dir, load_config
from xilogue.processing.ocr import RapidBackend
from xilogue.storage.store import Store
from xilogue.web import serve


def utc_now():
    return datetime.now(UTC).isoformat()


class JsonLines(RotatingFileHandler):
    """Flush each row; expose write failures and bounded-file rollover counts."""

    def __init__(self, path):
        super().__init__(path, maxBytes=8 * 1024 * 1024, backupCount=2, encoding="utf-8")
        self.setFormatter(logging.Formatter("%(message)s"))
        self.rollovers = 0

    def handleError(self, record):
        raise  # Let the recorder count failures; do not lose diagnostics silently.

    def doRollover(self):
        super().doRollover()
        self.rollovers += 1

    def write(self, item):
        self.emit(
            logging.LogRecord(
                "usage_metrics", logging.INFO, "", 0, json.dumps(item, ensure_ascii=True), (), None
            )
        )


def public_snapshot(engine):
    """Metrics omit target identity/title, latest image IDs/paths, and image contents."""
    snap = engine.snapshot()
    timings = {}
    for key, values in snap["timings"].items():
        if values:
            timings[key] = {
                "count": len(values),
                "mean_ms": float(np.mean(values)) * 1000,
                "p95_ms": float(np.percentile(values, 95)) * 1000,
                "max_ms": float(max(values)) * 1000,
            }
    return {
        "state": snap["state"],
        "reason": snap["reason"],
        "last_error": snap["last_error"],
        "queue": snap["queue"],
        "counts": snap["counts"],
        "storage": snap["storage"],
        "recent_saved_frame_timings": timings,
        "ocr": snap.get("ocr"),
    }


class Recorder:
    def __init__(self, output, interval=60, process=None):
        self.output, self.interval = Path(output), interval
        self.process = process or psutil.Process()
        self.events = queue.Queue(512)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.samples = self.dropped_events = self.write_errors = 0
        self.started = time.perf_counter()
        self.last_sample = self.started
        self.engine = self.thread = None
        self.final_storage = None
        self.final_sample = None
        self.metrics_writer = JsonLines(self.output / "metrics.jsonl")
        self.events_writer = JsonLines(self.output / "events.jsonl")

    def note(self, kind, reason):
        # Called from capture/control threads: no disk writes and no blocking put.
        item = {
            "at_utc": utc_now(),
            "elapsed_seconds": time.perf_counter() - self.started,
            "kind": kind,
            "reason": reason,
        }
        try:
            self.events.put_nowait(item)
        except queue.Full:
            with self._lock:
                self.dropped_events += 1

    def start(self, engine):
        self.engine = engine
        self.thread = threading.Thread(target=self._run, name="usage-metrics", daemon=True)
        self.thread.start()

    def status(self):
        with self._lock:
            return {
                "samples": self.samples,
                "dropped_events": self.dropped_events,
                "write_errors": self.write_errors,
            }

    def _write(self, writer, item):
        try:
            writer.write(item)
            return True
        except (OSError, ValueError):
            with self._lock:
                self.write_errors += 1
            print("Usage diagnostics: write failed", file=sys.stderr, flush=True)
            return False

    def _sample(self, final=False):
        now = time.perf_counter()
        memory = self.process.memory_info()
        io = self.process.io_counters()
        cores = psutil.cpu_count() or 1
        cpu = self.process.cpu_percent()
        item = {
            "at_utc": utc_now(),
            "elapsed_seconds": now - self.started,
            "sample_window_seconds": now - self.last_sample,
            "sampling_gap": now - self.last_sample > self.interval * 2.5,
            "final": final,
            "cpu_percent_one_core_scale": cpu,
            "cpu_percent_machine_scale": cpu / cores,
            "logical_cpu_count": cores,
            "rss_bytes": memory.rss,
            "private_bytes": getattr(memory, "private", memory.rss),
            "threads": self.process.num_threads(),
            "handles": self.process.num_handles(),
            "io_read_bytes": io.read_bytes,
            "io_write_bytes": io.write_bytes,
            **public_snapshot(self.engine),
            **self.status(),
        }
        if final and self.final_storage is not None:
            item["storage"] = self.final_storage
        if self._write(self.metrics_writer, item):
            with self._lock:
                self.samples += 1
        self.last_sample = now
        self.final_sample = item

    def _drain_events(self):
        for _ in range(512):
            try:
                self._write(self.events_writer, self.events.get_nowait())
            except queue.Empty:
                break

    def _run(self):
        try:
            self.process.cpu_percent()  # Prime the non-blocking measurement.
            self._sample()
            next_sample = time.perf_counter() + self.interval
            while not self._stop.wait(0.1):
                self._drain_events()
                if time.perf_counter() >= next_sample:
                    self._sample()
                    next_sample = time.perf_counter() + self.interval
            self._drain_events()
            self._sample(final=True)
        except Exception as exc:
            with self._lock:
                self.write_errors += 1
            print("Usage diagnostics stopped: " + type(exc).__name__, file=sys.stderr, flush=True)
        finally:
            self.metrics_writer.close()
            self.events_writer.close()

    def stop(self, final_storage=None):
        self.final_storage = final_storage
        self._stop.set()
        if self.thread is not None:
            self.thread.join(10)
        else:
            self.metrics_writer.close()
            self.events_writer.close()
        return self.thread is None or not self.thread.is_alive()


class MeasuredEngine(Engine):
    def __init__(self, *args, recorder, **kwargs):
        self.recorder = recorder
        super().__init__(*args, **kwargs)

    def _event(self, kind, reason, **details):
        super()._event(kind, reason, **details)
        self.recorder.note(kind, reason)  # Deliberately discard all content-bearing details.


def selected_config(explicit):
    if explicit:
        return load_config(Path(explicit)), "explicit"
    existing = default_data_dir() / "config.json"
    return (
        (load_config(existing), "existing_application_config")
        if existing.exists()
        else (Config(), "defaults")
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default="data/real-usage")
    parser.add_argument("--config", help="可选配置；默认复用本机正式程序已有配置")
    parser.add_argument("--interval", type=float, default=60, help="指标采样间隔，默认 60 秒")
    parser.add_argument("--check", action="store_true", help="只校验准备状态，不创建数据或打开界面")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or not 1 <= args.interval <= 3600:
        parser.error("interval 必须在 1–3600 秒之间")
    try:
        config, source = selected_config(args.config)
    except (ConfigError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    parent = Path(args.output_root).expanduser().resolve()
    if args.check:
        print(
            json.dumps(
                {
                    "output_root": str(parent),
                    "config_source": source,
                    "config": config.to_dict(),
                    "interval": args.interval,
                    "capture_starts_automatically": False,
                },
                ensure_ascii=True,
            )
        )
        return
    output = parent / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
    store = Store(output / "app", config)
    config_path = output / "app" / "config.json"
    config_path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    manifest = {
        "started_at_utc": utc_now(),
        "ended_cleanly": False,
        "mode": "normal_usage",
        "config_source": source,
        "config": config.to_dict(),
        "environment": environment(),
        "sample_interval_seconds": args.interval,
        "capture_starts_automatically": False,
        "metrics_contain_window_titles_or_images": False,
    }
    (output / "session.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    recorder = Recorder(output, args.interval)
    engine = MeasuredEngine(
        config,
        store,
        WindowsMonitor(),
        recorder=recorder,
        restrict=lambda target: target.pid != os.getpid(),
        ocr_backend_factory=RapidBackend,
    )
    print("Usage test directory: " + str(output), flush=True)
    print("Capture is stopped. Click Start when ready; click Exit when finished.", flush=True)
    try:
        recorder.start(engine)
        serve(
            engine,
            store,
            config_path,
            port=args.port,
            open_browser=not args.no_browser,
            notice=lambda: {
                "elapsed_seconds": time.perf_counter() - recorder.started,
                **recorder.status(),
            },
        )
    finally:
        closed = engine.close().wait(20)
        for thread in engine._threads:
            thread.join(2)
        clean = closed and all(not t.is_alive() for t in engine._threads)
        storage = store.stats() if clean else None
        recorder_done = recorder.stop(storage)
        manifest.update(
            ended_at_utc=utc_now(),
            ended_cleanly=clean and recorder_done,
            diagnostics=recorder.status(),
            final=recorder.final_sample,
            metrics_rollovers=recorder.metrics_writer.rollovers,
            events_rollovers=recorder.events_writer.rollovers,
            diagnostics_complete=(
                recorder_done
                and not recorder.status()["write_errors"]
                and not recorder.status()["dropped_events"]
                and recorder.metrics_writer.rollovers <= 2
                and recorder.events_writer.rollovers <= 2
            ),
        )
        (output / "session.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        store.close()
        print("Usage report saved: " + str(output / "session.json"), flush=True)


if __name__ == "__main__":
    main()
