"""Capture state machine with bounded work and a completed-pause commit barrier."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import Counter, deque
from dataclasses import replace
from datetime import UTC, datetime

from xilogue.collector.backend import WgcBackend
from xilogue.collector.policy import inspect_target
from xilogue.processing.change import compare, prepare
from xilogue.storage.store import StorageError, encode_webp


class Engine:
    def __init__(
        self,
        config,
        store,
        monitor,
        backend_factory=WgcBackend,
        restrict=None,
        hook=None,
        ocr_backend_factory=None,
        analysis_backend_factory=None,
    ):
        self.config, self.store, self.monitor = config, store, monitor
        self.backend_factory, self.restrict = backend_factory, restrict
        self.hook = hook or (lambda stage, frame: None)
        self._lock = threading.RLock()
        self._commit = threading.Lock()
        self._wake = threading.Event()
        self._worker_stop = threading.Event()
        self.paused = threading.Event()
        self.paused.set()
        self.closed = threading.Event()
        self.state, self.generation, self.target = "stopped", 0, None
        self.mode = "capture"
        self._ocr_cutoff = None
        self._processing_generation = 0
        self.reason, self.last_error = "not_started", ""
        self.candidates = queue.Queue(config.candidate_capacity)
        self.events = queue.Queue(config.event_capacity)
        self.counts = Counter()
        self.timings = {
            k: deque(maxlen=512) for k in ("copy", "compare", "encode", "commit", "total")
        }
        self.storage_stats = store.stats()
        self.latest = None
        self.last_metrics = {}
        self._last_sample = 0.0
        self._reference = None
        self._backend = None  # Owned exclusively by the lifecycle thread.
        self._backend_error = False
        self._retry_at, self._retry_delay = 0.0, 1.0
        self._threads = [
            threading.Thread(target=self._process, name="memory-process"),
            threading.Thread(target=self._maintain, name="memory-maintain"),
            threading.Thread(target=self._supervise, name="memory-capture"),
        ]
        self.ocr = None
        if ocr_backend_factory is not None:
            from xilogue.processing.ocr import OcrWorker

            self.ocr = OcrWorker(self, ocr_backend_factory)
            self._threads.append(self.ocr.thread)
        self.analysis = None
        if analysis_backend_factory is not None:
            from xilogue.processing.analysis import AnalysisWorker

            self.analysis = AnalysisWorker(self, analysis_backend_factory)
            self._threads.append(self.analysis.thread)
        for thread in self._threads:
            thread.start()

    def _event(self, kind, reason, **details):
        with self._lock:
            self.reason = reason
            event = {"at": datetime.now(UTC).isoformat(), "kind": kind, "reason": reason, **details}
            try:
                self.events.put_nowait(event)
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass
                self.counts["event_dropped"] += 1
                self.events.put_nowait(event)
            if kind == "error":
                self.last_error = reason
                self.counts["errors"] += 1
                logging.getLogger("xilogue").warning("event=%s", reason)

    def _discard(self):
        while True:
            try:
                self.candidates.get_nowait()
                self.counts["invalidated"] += 1
            except queue.Empty:
                return

    def _invalidate(self):
        self.generation += 1
        self._last_sample = 0.0
        self._discard()

    def start(self, *, mode="capture"):
        if mode not in ("capture", "ocr_only", "analysis_only", "index_only"):
            raise ValueError("invalid_run_mode")
        with self._lock:
            if self.state not in ("stopped", "paused"):
                raise RuntimeError("start_requires_stopped_or_paused")
            if mode == "ocr_only" and self.ocr is None:
                raise RuntimeError("ocr_worker_unavailable")
            if mode in ("analysis_only", "index_only") and self.analysis is None:
                raise RuntimeError("analysis_worker_unavailable")
            self._invalidate()
            self._processing_generation += 1
            self.state, self.target = "running", None
            self.mode = mode
            self._ocr_cutoff = datetime.now(UTC) if mode != "capture" else None
            self._retry_at, self._retry_delay = 0.0, 1.0
            self.paused.clear()
            self._event(
                "state",
                {
                    "ocr_only": "ocr_only_started",
                    "analysis_only": "analysis_only_started",
                    "index_only": "index_only_started",
                }.get(mode, "starting"),
            )
        self._wake.set()

    def complete_ocr_pass(self, epoch):
        with self._lock:
            if (
                self.state == "running"
                and self.mode == "ocr_only"
                and epoch == self._processing_generation
            ):
                self.pause()
                self._event("state", "ocr_pass_finished")

    def complete_analysis_pass(self, epoch):
        with self._lock:
            if (
                self.state == "running"
                and self.mode in ("analysis_only", "index_only")
                and epoch == self._processing_generation
            ):
                self.pause()
                self._event(
                    "state",
                    "index_pass_finished"
                    if self.mode == "index_only"
                    else "analysis_pass_finished",
                )

    def pause(self):
        with self._lock:
            if self.state == "running":
                self._processing_generation += 1
                self.state, self.target = "pausing", None
                self._invalidate()
                self._event("state", "pausing")
        self._wake.set()
        return self.paused

    def close(self):
        with self._lock:
            if self.state != "closed":
                self._processing_generation += 1
                self.state, self.target = "closing", None
                self._invalidate()
        self._wake.set()
        return self.closed

    def reload(self, config):
        with self._commit, self._lock:
            if self.state not in ("stopped", "paused"):
                raise RuntimeError("reload_requires_pause")
            self._invalidate()
            self._processing_generation += 1
            self.config = config
            self.candidates = queue.Queue(config.candidate_capacity)
            self.events = queue.Queue(config.event_capacity)
            with self.store.lock:
                self.store.config = config
            self._event("config", "config_reloaded")

    def refresh_storage(self):
        stats = self.store.stats()
        with self._lock:
            self.storage_stats = stats

    def snapshot(self):
        with self._lock:
            return {
                "state": self.state,
                "mode": self.mode,
                "reason": self.reason,
                "generation": self.generation,
                "target": self.target,
                "last_error": self.last_error,
                "queue": self.candidates.qsize(),
                "counts": dict(self.counts),
                "storage": dict(self.storage_stats),
                "latest": self.latest,
                "metrics": dict(self.last_metrics),
                "timings": {k: list(v) for k, v in self.timings.items()},
                "ocr": self.ocr.snapshot() if self.ocr is not None else None,
                "analysis": self.analysis.snapshot() if self.analysis is not None else None,
            }

    def _valid(self, frame):
        with self._lock:
            return (
                self.state == "running"
                and self.mode == "capture"
                and frame.generation == self.generation
                and frame.target == self.target
            )

    def _accept(self, target, generation):
        now = time.perf_counter()
        with self._lock:
            if (
                self.state != "running"
                or self.mode != "capture"
                or generation != self.generation
                or self.target != target
                or now - self._last_sample < self.config.sample_interval_seconds
            ):
                return False
        current, _ = inspect_target(self.monitor, self.config, target, self.restrict)
        if current is None:
            self._wake.set()
            return False
        with self._lock:
            if generation != self.generation or self.state != "running":
                return False
            self._last_sample = now
            return True

    def _offer(self, frame):
        if not self._valid(frame):
            with self._lock:
                self.counts["invalidated"] += 1
            return
        current, _ = inspect_target(self.monitor, self.config, frame.target, self.restrict)
        with self._lock:
            if current is None or not self._valid(frame):
                self.counts["invalidated"] += 1
                return
            frame = replace(frame, target=current)
            try:
                self.candidates.put_nowait(frame)
                self.counts["candidates"] += 1
                self.counts["queue_peak"] = max(self.counts["queue_peak"], self.candidates.qsize())
            except queue.Full:
                self.counts["queue_full"] += 1
                self._event("skip", "queue_full")

    def _on_error(self, error, generation):
        with self._lock:
            if generation != self.generation or self.state != "running":
                return
            self._backend_error = True
            self._invalidate()
            self._retry_at = time.perf_counter() + self._retry_delay
            self._retry_delay = min(30, self._retry_delay * 2)
            self._event("error", error.code)
        self._wake.set()

    def _stop_backend(self):
        if self._backend is None:
            return True
        try:
            self._backend.stop()
            self._backend = None
            self._backend_error = False
            return True
        except Exception as exc:
            self._event("error", "backend_stop_" + type(exc).__name__)
            return False

    def _supervise(self):
        while True:
            try:
                self._supervise_loop()
                return
            except Exception as exc:
                with self._lock:
                    if self.state != "closing":
                        self.state = "pausing"
                    self.target = None
                    self._invalidate()
                    self._event("error", "supervisor_" + type(exc).__name__)
                self._wake.wait(1)

    def _supervise_loop(self):
        active, stable_since = None, 0.0
        while True:
            self._wake.clear()
            with self._lock:
                state = self.state
                mode = self.mode
            if state != "running":
                if not self._stop_backend():
                    self._wake.wait(1)
                    continue
                active = None
                if state in ("pausing", "closing"):
                    with self._commit:
                        with self._lock:
                            if self.state == "pausing":
                                self.state = "paused"
                                self._event("state", "paused")
                                self.paused.set()
                if state == "closing":
                    self._worker_stop.set()
                    for thread in self._threads:
                        if thread is not threading.current_thread():
                            thread.join()
                    with self._lock:
                        self.state = "closed"
                        self.closed.set()
                    return
            elif mode != "capture":
                if not self._stop_backend():
                    self._wake.wait(1)
                    continue
                active = None
            else:
                target, reason = inspect_target(self.monitor, self.config, restrict=self.restrict)
                now = time.perf_counter()
                with self._lock:
                    if self.state != "running":
                        continue
                    if target != self.target:
                        self._invalidate()
                        self.target = target
                        stable_since = now
                    elif target is not None:
                        self.target = target  # Refresh title without changing identity.
                    if target is None and self.reason != reason:
                        self._event("skip", reason)
                    generation = self.generation
                if active != target or self._backend_error:
                    if not self._stop_backend():
                        self._wake.wait(1)
                        continue
                    active = None
                if self._backend is not None and self._backend.finished():
                    self._stop_backend()
                    active = None
                    with self._lock:
                        self._invalidate()
                        generation = self.generation
                    self._retry_at = now + self._retry_delay
                    self._retry_delay = min(30, self._retry_delay * 2)
                    self._event("error", "capture_ended")
                if (
                    target is not None
                    and self._backend is None
                    and now >= self._retry_at
                    and now - stable_since >= self.config.foreground_stabilize_seconds
                ):
                    try:
                        backend = self.backend_factory()
                        self._backend = backend
                        backend.start(
                            target,
                            generation,
                            self._offer,
                            lambda error, g=generation: self._on_error(error, g),
                            lambda t=target, g=generation: self._accept(t, g),
                        )
                        active = target
                        self._event("capture", "capturing")
                    except Exception as exc:
                        self._stop_backend()
                        self._retry_at = now + self._retry_delay
                        self._retry_delay = min(30, self._retry_delay * 2)
                        self._event("error", "backend_start_" + type(exc).__name__)
            self._wake.wait(self.config.poll_interval_seconds)

    def _process(self):
        while not self._worker_stop.is_set():
            try:
                frame = self.candidates.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self.hook("dequeued", frame)
                if not self._valid(frame):
                    continue
                started = time.perf_counter()
                preview = prepare(frame.pixels, self.config)
                reference = (
                    self._reference[1]
                    if self._reference is not None and self._reference[0] == frame.generation
                    else None
                )
                decision = compare(preview, reference, self.config)
                compared = time.perf_counter()
                self.hook("prepared", frame)
                with self._lock:
                    self.last_metrics = decision.metrics
                if not decision.keep:
                    with self._lock:
                        self.counts["skipped"] += 1
                    self._event("skip", decision.reason)
                    continue
                encoded = encode_webp(frame.pixels, self.config.image_webp_quality)
                encoded_at = time.perf_counter()
                self.hook("encoded", frame)
                with self._commit:
                    current, _ = inspect_target(
                        self.monitor, self.config, frame.target, self.restrict
                    )
                    if current is None or not self._valid(frame):
                        with self._lock:
                            self.counts["invalidated"] += 1
                        continue
                    self.hook("committing", frame)

                    def validate(frame=frame):
                        allowed, _ = inspect_target(
                            self.monitor, self.config, frame.target, self.restrict
                        )
                        return allowed is not None and self._valid(frame)

                    result = self.store.save(frame, encoded, decision, validate=validate)
                    committed = time.perf_counter()
                    self._reference = (frame.generation, preview)
                    stats = self.store.stats()
                    with self._lock:
                        self.latest = result
                        self.storage_stats = stats
                        self.counts["saved"] += 1
                        self._retry_delay = 1.0
                        durations = {
                            "copy": frame.copy_seconds,
                            "compare": compared - started,
                            "encode": encoded_at - compared,
                            "commit": committed - encoded_at,
                            "total": committed - frame.received_monotonic,
                        }
                        for key, value in durations.items():
                            self.timings[key].append(value)
                        self._event("save", decision.reason, **result)
            except StorageError as exc:
                code = str(exc)
                if code == "candidate_invalidated":
                    with self._lock:
                        self.counts["invalidated"] += 1
                    self._event("skip", code)
                else:
                    reason = (
                        code
                        if code in ("single_image_over_quota", "quota_reclaim_failed")
                        else "storage_failed"
                    )
                    self._event("error", reason)
            except Exception as exc:
                self._event("error", "processing_" + type(exc).__name__)

    def _maintain(self):
        while not self._worker_stop.wait(self.config.maintenance_seconds):
            try:
                recovery = self.store.recover()
                result = self.store.maintenance()
                stats = self.store.stats()
                with self._lock:
                    self.storage_stats = stats
                if recovery["errors"] or result["errors"]:
                    self._event("error", "maintenance_incomplete")
                with self._commit:
                    with self._lock:
                        review_enabled = (
                            self.state == "running"
                            and self.mode == "capture"
                            and self.config.ai_enabled
                        )
                    if review_enabled and self.monitor.desktop_available():
                        from datetime import timedelta
                        from zoneinfo import ZoneInfo

                        zone = self.config.review_timezone
                        yesterday = (
                            datetime.now(ZoneInfo(zone)).date() - timedelta(days=1)
                        ).isoformat()
                        self.store.generate_review(yesterday, zone)
            except Exception as exc:
                self._event("error", "maintenance_" + type(exc).__name__)
