"""Local CPU OCR; no model download, network provider or capture side effects."""

import threading
import time
from importlib import metadata, resources

import cv2
import numpy as np

from xilogue.processing.ocr_types import OcrDocument, OcrLine, validate_document


class RapidBackend:
    def __init__(self, threads=2):
        from rapidocr_onnxruntime import RapidOCR

        models = resources.files("rapidocr_onnxruntime") / "models"
        names = {
            "det_model_path": "ch_PP-OCRv4_det_infer.onnx",
            "rec_model_path": "ch_PP-OCRv4_rec_infer.onnx",
            "cls_model_path": "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        }
        paths = {key: str(models / name) for key, name in names.items()}
        if any(not (models / name).is_file() for name in names.values()):
            raise RuntimeError("bundled_ocr_models_missing")
        self.engine_name = "rapidocr-onnxruntime/" + metadata.version("rapidocr-onnxruntime")
        self.engine = RapidOCR(
            **paths,
            intra_op_num_threads=threads,
            inter_op_num_threads=1,
            det_use_cuda=False,
            cls_use_cuda=False,
            rec_use_cuda=False,
            det_use_dml=False,
            cls_use_dml=False,
            rec_use_dml=False,
        )

    def recognize(self, pixels):
        rows, _ = self.engine(pixels)
        height, width = pixels.shape[:2]
        lines = []
        for box, text, score in rows or ():
            if text:
                points = tuple(
                    (float(np.clip(x, 0, width)), float(np.clip(y, 0, height))) for x, y in box
                )
                lines.append(OcrLine(text, points, float(score)))
        document = OcrDocument(self.engine_name, tuple(lines))
        validate_document(document, width, height)
        return document


def read_pixels(store, job):
    path = store.image_path(job["image_path"])
    if path.stat().st_size > 64 * 1024 * 1024 or job["width"] * job["height"] > 40_000_000:
        raise ValueError("ocr_image_too_large")
    pixels = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if pixels is None or pixels.shape[:2] != (job["height"], job["width"]):
        raise ValueError("ocr_image_invalid")
    return pixels


class OcrWorker:
    def __init__(self, engine, backend_factory):
        self.engine, self.store, self.backend_factory = engine, engine.store, backend_factory
        self.thread = threading.Thread(target=self.run, name="memory-ocr")
        self._lock = threading.Lock()
        self._stats = {
            "state": "stopped",
            "last_error": "",
            "jobs": {},
            "model_loaded": False,
            "pass_finished": False,
        }
        self.backend = None
        self.threads = None

    def snapshot(self):
        with self._lock:
            return dict(self._stats)

    def _status(self, **items):
        with self._lock:
            self._stats.update(items)

    def lease(self):
        with self.engine._lock:
            if self.engine.state == "running" and (
                (self.engine.mode == "capture" and self.engine.config.ocr_enabled)
                or self.engine.mode == "ocr_only"
            ):
                return self.engine._processing_generation
        return None

    def _release_backend(self):
        self.backend = None
        self.threads = None
        self._status(model_loaded=False)

    def valid(self, epoch):
        return epoch is not None and self.lease() == epoch

    def run(self):
        stop = self.engine._worker_stop
        last_epoch = None
        try:
            while not stop.is_set():
                job = None
                epoch = self.lease()
                try:
                    self._status(jobs=self.store.ocr_stats())
                    if epoch is not None and epoch != last_epoch:
                        self._status(pass_finished=False)
                        last_epoch = epoch
                    if epoch is None or not self.engine.monitor.desktop_available():
                        self._release_backend()
                        self._status(state="stopped")
                        stop.wait(0.2)
                        continue
                    with self.engine._lock:
                        cutoff = self.engine._ocr_cutoff
                    job = self.store.claim_ocr(cutoff)
                    if job is None:
                        self._status(state="waiting")
                        with self.engine._lock:
                            only_ocr = self.engine.mode == "ocr_only" and self.valid(epoch)
                        if only_ocr:
                            self._release_backend()
                            self._status(pass_finished=True)
                            self.engine.complete_ocr_pass(epoch)
                        stop.wait(0.2)
                        continue
                    self._status(state="processing")
                    self.engine.hook("ocr_claimed", job)
                    if not self.valid(epoch):
                        self.store.release_ocr(job)
                        continue
                    threads = self.engine.config.ocr_threads
                    if self.backend is None or self.threads != threads:
                        self._release_backend()
                        self.backend = self.backend_factory(threads)
                        self.threads = threads
                        self._status(model_loaded=True)
                    document = self.backend.recognize(read_pixels(self.store, job))
                    self.engine.hook("ocr_recognized", job)
                    with self.engine._commit:
                        if self.valid(epoch) and self.engine.monitor.desktop_available():
                            self.engine.hook("ocr_committing", job)
                            if not self.store.finish_ocr(job, document):
                                self.store.release_ocr(job)
                                self.store.recover()
                        else:
                            self.store.release_ocr(job)
                    self._status(last_error="")
                except Exception as exc:
                    code = "ocr_" + type(exc).__name__
                    self._status(state="error", last_error=code)
                    if job is not None:
                        try:
                            self.store.release_ocr(job, code if self.valid(epoch) else None)
                        except Exception:
                            pass  # The durable running lease is reclaimed on next startup.
                    if self.valid(epoch):
                        self.engine._event("error", code)
                    stop.wait(1)
        finally:
            self._release_backend()
            self._status(state="closed")


def process_pending(store, backend_factory=RapidBackend):
    """Explicit CLI backlog pass; processes currently eligible jobs, never captures."""
    backend = None
    processed = failed = 0
    started = time.perf_counter()
    try:
        while job := store.claim_ocr():
            try:
                if backend is None:
                    backend = backend_factory(store.config.ocr_threads)
                result = backend.recognize(read_pixels(store, job))
                if store.finish_ocr(job, result):
                    processed += 1
                else:
                    store.release_ocr(job)
                    store.recover()
            except BaseException as exc:
                store.release_ocr(job, "ocr_" + type(exc).__name__)
                if not isinstance(exc, Exception):
                    raise
                failed += 1
    finally:
        backend = None
    return {
        "processed": processed,
        "failed": failed,
        "seconds": time.perf_counter() - started,
        "jobs": store.ocr_stats(),
    }
