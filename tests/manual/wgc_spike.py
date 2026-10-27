"""Explicit safe-window WGC experiment: uv run python tests/manual/wgc_spike.py."""

import json
import threading
import time
import tkinter as tk
from pathlib import Path

import cv2

from xilogue.collector.backend import WgcBackend
from xilogue.collector.windows import WindowsMonitor, target_for


def main():
    root = tk.Tk()
    root.title("昔录 · Xilogue SAFE WGC SPIKE")
    root.geometry("643x400+50+50")
    canvas = tk.Canvas(root, bg="white", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    for x, color in [(20, "red"), (180, "green"), (340, "blue")]:
        canvas.create_rectangle(x, 70, x + 120, 230, fill=color, outline=color)
    canvas.create_text(20, 30, text="SAFE synthetic content / RGB / 643 px", anchor="w")
    root.update()
    # Tk's wrapper window is the actual top-level HWND, not its child widget HWND.
    hwnd = int(root.frame(), 0)
    target = target_for(hwnd)
    backend = WgcBackend()
    frames, errors, stages = [], [], []
    report = {"desktop_available": WindowsMonitor().desktop_available()}
    started = time.perf_counter()
    backend.start(
        target,
        1,
        lambda f: frames.append(f) if len(frames) < 10 else None,
        lambda e: errors.append(e.code),
    )

    def finish():
        before = time.perf_counter()
        backend.stop()
        report.update(stop_seconds=time.perf_counter() - before, finished=backend.finished())
        root.after(50, root.destroy)

    def stop_async():
        threading.Thread(target=finish, name="spike-stop").start()

    def update(i=0):
        canvas.create_text(20, 300 + i * 15, text=f"SAFE update {i}", anchor="w")
        if i == 2:
            root.geometry("777x450+50+50")
        stages.append(
            {"i": i, "size": root.geometry(), "frames": len(frames), "finished": backend.finished()}
        )
        if i < 8:
            root.after(500, lambda: update(i + 1))

    root.after(500, update)
    root.after(6000, stop_async)
    root.mainloop()
    report.update(
        duration_seconds=time.perf_counter() - started,
        errors=errors,
        stages=stages,
        frames=[
            {
                "shape": f.pixels.shape,
                "row_pitch": f.source_row_pitch,
                "utc": f.captured_at.isoformat(),
                "copy": f.copy_seconds,
            }
            for f in frames
        ],
    )
    output = Path("data/wgc-spike")
    output.mkdir(parents=True, exist_ok=True)
    if frames:
        cv2.imwrite(str(output / "synthetic.png"), frames[0].pixels)
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    assert len(frames) > 2 and not errors and report["finished"] and report["desktop_available"]
    assert len({f.pixels.shape for f in frames}) > 1


if __name__ == "__main__":
    main()
