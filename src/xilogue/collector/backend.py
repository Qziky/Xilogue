"""HWND-only WGC adapter. All lifecycle calls belong to one supervisor thread."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import numpy as np

from xilogue.collector.models import CaptureError, CaptureFrame


def owned_bgr(buffer: np.ndarray, width: int, height: int) -> np.ndarray:
    if buffer.dtype != np.uint8 or buffer.shape != (height, width, 4):
        raise ValueError("invalid_pixel_layout")
    if width < 2 or height < 2:
        raise ValueError("invalid_dimensions")
    return buffer[:, :, :3].copy(order="C")


class WgcBackend:
    def __init__(self):
        self.control = None
        self.capture = None
        self.closed = threading.Event()
        self.closed.set()
        self.utc_offset = time.time() - time.perf_counter()

    def start(self, target, generation, on_frame, on_error, accept_frame=lambda: True):
        from windows_capture import WindowsCapture

        if self.control is not None:
            raise RuntimeError("capture_already_started")
        self.closed.clear()
        self.capture = WindowsCapture(
            window_hwnd=target.hwnd,
            cursor_capture=False,
            draw_border=True,
            minimum_update_interval=100,
        )

        @self.capture.event
        def on_frame_arrived(frame, capture_control):
            try:
                if not accept_frame():
                    return
                received = time.perf_counter()
                seconds = frame.timespan / 10_000_000
                if abs(seconds - received) > 60:
                    raise ValueError("invalid_capture_timestamp")
                pixels = owned_bgr(frame.frame_buffer, frame.width, frame.height)
                on_frame(
                    CaptureFrame(
                        pixels,
                        target,
                        generation,
                        datetime.fromtimestamp(self.utc_offset + seconds, UTC),
                        received,
                        copy_seconds=time.perf_counter() - received,
                        source_row_pitch=frame.frame_buffer.strides[0],
                    )
                )
            except Exception as exc:
                # Never forward exception text containing application context.
                on_error(CaptureError("frame_" + type(exc).__name__))
                capture_control.stop()

        @self.capture.event
        def on_closed():
            self.closed.set()

        try:
            self.control = self.capture.start_free_threaded()
        except Exception:
            self.capture = None
            self.closed.set()
            raise

    def finished(self):
        return self.control is None or self.control.is_finished()

    def stop(self):
        if self.control is not None:
            self.control.stop()  # Joins native thread; never call from its callback.
        # A failed stop retains its control object for a confirmed retry.
        self.control = None
        self.capture = None
        self.closed.set()
