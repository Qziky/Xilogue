from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass(frozen=True)
class Target:
    hwnd: int
    pid: int
    process_started: int
    app_name: str
    title: str = field(compare=False)


@dataclass(frozen=True)
class CaptureFrame:
    pixels: np.ndarray
    target: Target
    generation: int
    captured_at: datetime
    received_monotonic: float
    backend: str = "windows-capture/2.0.1:WGC"
    copy_seconds: float = 0.0
    source_row_pitch: int = 0


@dataclass(frozen=True)
class CaptureError:
    code: str
    retryable: bool = True
    backend: str = "windows-capture/2.0.1:WGC"
