"""Explicit/opt-in analysis worker with the same pause commit fence as capture/OCR."""

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from xilogue.processing.ollama import ProviderError
from xilogue.processing.providers import API_ERRORS


class AnalysisWorker:
    def __init__(self, engine, factory):
        self.engine, self.store, self.factory = engine, engine.store, factory
        self.thread = threading.Thread(target=self.run, name="memory-analysis")
        self._lock = threading.Lock()
        self._stats = {
            "state": "stopped",
            "last_error": "",
            "jobs": {},
            "provider_active": False,
            "cleanup_error": "",
        }
        self.provider = None
        self.provider_role = None
        self.provider_config = None
        self.provider_epoch = None
        self._discover_at = 0

    def snapshot(self):
        with self._lock:
            return dict(self._stats)

    def status(self, **items):
        with self._lock:
            self._stats.update(items)

    def lease(self):
        with self.engine._lock:
            if self.engine.state == "running" and (
                self.engine.mode in ("analysis_only", "index_only")
                or (
                    self.engine.mode == "capture"
                    and (self.engine.config.ai_enabled or self.engine.config.semantic_enabled)
                )
            ):
                return self.engine._processing_generation
        return None

    def claim(self, cutoff):
        with self.engine._lock:
            mode, config = self.engine.mode, self.engine.config
        if mode == "index_only":
            return self.store.claim_embedding(cutoff)
        job = (
            self.store.claim_analysis(cutoff, automatic=mode == "capture")
            if mode == "analysis_only" or config.ai_enabled
            else None
        )
        if job is None and mode == "capture" and config.semantic_enabled:
            job = self.store.claim_embedding(cutoff, automatic=True)
        return job

    def release_job(self, job, error=None):
        if job.get("kind") == "embedding":
            self.store.release_embedding(job, error)
        else:
            self.store.release_analysis(job, error)

    def release_provider(self):
        if self.provider is not None:
            try:
                self.provider.close()
            except Exception:
                self.status(cleanup_error="model_release_unconfirmed")
                raise
            self.provider = None
        self.status(provider_active=False, cleanup_error="")
        self.provider_role = None
        self.provider_config = None
        self.provider_epoch = None

    def run(self):
        stop = self.engine._worker_stop
        try:
            while not stop.is_set():
                epoch = self.lease()
                job = None
                try:
                    self.status(
                        jobs=self.store.analysis_stats(), embeddings=self.store.embedding_stats()
                    )
                    if epoch is None or not self.engine.monitor.desktop_available():
                        self.release_provider()
                        self.status(state="stopped")
                        stop.wait(0.2)
                        continue
                    with self.engine._lock:
                        cutoff = self.engine._ocr_cutoff if self.engine.mode != "capture" else None
                        capture = self.engine.mode == "capture"
                    if capture and time.monotonic() >= self._discover_at:
                        with self.engine._commit:
                            if self.lease() == epoch and self.engine.monitor.desktop_available():
                                self.store.queue_automatic()
                        self._discover_at = time.monotonic() + 2
                    job = self.claim(cutoff)
                    if job is None:
                        self.release_provider()
                        self.status(state="waiting")
                        self.engine.complete_analysis_pass(epoch)
                        stop.wait(0.2)
                        continue
                    prefix = job.get("kind", "analysis")
                    self.engine.hook(prefix + "_claimed", job)
                    if self.lease() != epoch:
                        self.release_job(job)
                        continue
                    if not job["evidence"]:
                        raise ValueError("no_text_evidence")
                    if self.provider is not None and (
                        self.provider_role != prefix
                        or self.provider_config != self.engine.config
                        or self.provider_epoch != epoch
                    ):
                        self.release_provider()
                    if self.provider is None:
                        factory = (
                            getattr(self.factory, "for_embedding", self.factory)
                            if prefix == "embedding"
                            else self.factory
                        )
                        self.provider = factory(self.engine.config)
                        self.provider_config = self.engine.config
                        self.provider_epoch = epoch
                        self.provider_role = prefix
                        self.status(provider_active=True)
                    self.status(state="processing")
                    if self.lease() != epoch or not self.engine.monitor.desktop_available():
                        self.release_job(job)
                        continue
                    if prefix == "embedding":
                        from xilogue.storage.semantic import unit_vector

                        vectors = [
                            unit_vector(v)
                            for v in self.provider.embed([e["text"][:450] for e in job["evidence"]])
                        ]
                        if (
                            len(vectors) != len(job["evidence"])
                            or len({len(v) for v in vectors}) != 1
                        ):
                            raise ValueError("embedding_dimensions_mismatch")
                        payload = [
                            sum(v[i] for v in vectors) / len(vectors)
                            for i in range(len(vectors[0]))
                        ]
                    else:
                        payload = self.provider.analyze(job["evidence"])
                    self.engine.hook(prefix + "_generated", job)
                    with self.engine._commit:
                        if self.lease() == epoch and self.engine.monitor.desktop_available():
                            self.engine.hook(prefix + "_committing", job)
                            if prefix == "embedding":
                                self.store.finish_embedding(
                                    job,
                                    payload,
                                    self.engine.config.embedding_model,
                                    self.provider.embedding_digest,
                                )
                            else:
                                self.store.finish_analysis(
                                    job,
                                    payload,
                                    getattr(
                                        self.provider, "analysis_model", self.engine.config.ai_model
                                    ),
                                )
                                if capture:
                                    zone = self.engine.config.review_timezone
                                    yesterday = (
                                        datetime.now(ZoneInfo(zone)).date() - timedelta(days=1)
                                    ).isoformat()
                                    self.store.generate_review(yesterday, zone)
                        else:
                            self.release_job(job)
                    self.status(last_error="")
                except Exception as exc:
                    code = (
                        "model_release_unconfirmed"
                        if self.snapshot()["cleanup_error"]
                        else "analysis_" + type(exc).__name__
                    )
                    if isinstance(exc, ProviderError) and exc.code in API_ERRORS | {
                        "ollama_unavailable",
                        "local_model_missing",
                        "remote_model_rejected",
                        "model_capability_unavailable",
                        "analysis_truncated",
                        "analysis_validation_failed",
                    }:
                        code = exc.code
                    self.status(state="error", last_error=code)
                    if job is not None:
                        try:
                            self.release_job(job, code if self.lease() == epoch else None)
                        except Exception:
                            pass  # Durable lease is reclaimed on restart.
                    self.engine._event("error", code)
                    stop.wait(1)
        finally:
            try:
                self.release_provider()
            except Exception:
                self.status(last_error="model_release_unconfirmed")
            self.status(state="closed")
