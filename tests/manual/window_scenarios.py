"""Real WGC tests using only this executable's synthetic windows.

uv run --locked python tests/manual/window_scenarios.py
The fixture manipulates its own Tk windows, never other applications.
"""

import ctypes
import json
import time
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import psutil
from windows_acceptance import allowed_window, close_with_events, environment, reduced_snapshot

from xilogue.collector.engine import Engine
from xilogue.collector.windows import WindowsMonitor, bind, target_for, user
from xilogue.config import Config
from xilogue.storage.store import Store


def main():
    output = Path("data/window-scenarios") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    )
    config = Config(
        sample_interval_seconds=0.2,
        foreground_stabilize_seconds=0.15,
        poll_interval_seconds=0.05,
        idle_policy="ignore",
        maintenance_seconds=2,
    )
    store = Store(output / "app", config)
    root = tk.Tk()
    root.withdraw()
    allowed, frames = [], {}
    process = psutil.Process()
    report = {
        "environment": environment(),
        "config": config.to_dict(),
        "checks": [],
        "steps": [],
        "switches": [],
        "issues": [],
    }

    def check(name, passed, **details):
        item = {"name": name, "passed": bool(passed), **details}
        report["checks"].append(item)
        print(json.dumps(item), flush=True)

    def fixture(name, offset):
        window = tk.Toplevel(root)
        window.title("Recorder SAFE SCENARIO " + name)
        window.geometry(f"640x420+{40 + offset}+{60 + offset}")
        canvas = tk.Canvas(window, bg="#ddeeff", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(30, 30, text="SYNTHETIC / " + name, anchor="w", font=("Segoe UI", 18))
        block = canvas.create_rectangle(30, 90, 560, 320, fill="#2266aa", outline="")
        root.update()
        target = target_for(int(window.frame(), 0))
        allowed.append(target)
        frames[name] = (window, canvas, block, target)
        return frames[name]

    a, b = fixture("A", 0), fixture("B", 170)
    monitor = WindowsMonitor()
    engine = Engine(config, store, monitor, restrict=lambda t: allowed_window(t, allowed))
    started = time.perf_counter()

    def focus(item):
        item[0].deiconify()
        item[0].lift()
        item[0].focus_force()
        root.update()

    def saved():
        return engine.snapshot()["counts"].get("saved", 0)

    def pause():
        done = engine.pause()
        deadline = time.perf_counter() + 5
        while not done.is_set():
            if time.perf_counter() >= deadline:
                raise RuntimeError("pause_timeout")
            yield 0.05

    def configure(cfg):
        yield from pause()
        engine.reload(cfg)
        engine.start()

    def plan():
        focus(a)
        engine.start()
        yield 1.2
        check("first_frame", saved() >= 1)
        initial = psutil.Process().num_threads()
        for index in range(30):
            item = a if index % 2 == 0 else b
            focus(item)
            current = monitor.foreground()
            report["switches"].append(
                {"requested": item[3].hwnd, "observed": current.hwnd if current else None}
            )
            yield 0.08
        focus(a)
        yield 1
        check(
            "30_foreground_transitions",
            len(report["switches"]) == 30
            and all(x["requested"] == x["observed"] for x in report["switches"]),
        )
        check(
            "bounded_after_switches",
            engine.snapshot()["queue"] <= config.candidate_capacity,
            native_threads_before=initial,
            native_threads_after=process.num_threads(),
        )

        before = saved()
        a[0].iconify()
        yield 0.8
        check("minimized_target_invalidated", engine.snapshot()["target"] != a[3])
        focus(a)
        a[1].itemconfigure(a[2], fill="#33aa66")
        yield 1
        check("restored_window_captured", saved() > before)

        before = saved()
        a[0].geometry("780x500+40+60")
        yield 0.5
        a[1].itemconfigure(a[2], fill="#aa6622")
        yield 1
        check(
            "resize_captured",
            saved() > before
            and len(
                {(r["width"], r["height"]) for r in store.rows() if r["window_title"] == a[3].title}
            )
            > 1,
        )

        # A topmost, non-activated occluder; WGC must still capture only window A.
        cover = tk.Toplevel(root)
        cover.overrideredirect(True)
        cover.attributes("-topmost", True)
        cover.geometry("500x300+80+120")
        tk.Label(cover, text="SAFE OCCLUDER", bg="#ff00ff").pack(fill="both", expand=True)
        focus(a)
        a[1].itemconfigure(a[2], fill="#22aacc")
        before = saved()
        yield 1
        current = monitor.foreground()
        latest = engine.snapshot()["latest"]
        pixels = cv2.imread(str(store.image_path(latest["image_path"]))) if latest else None
        magenta = (
            float(
                ((pixels[:, :, 0] > 200) & (pixels[:, :, 1] < 80) & (pixels[:, :, 2] > 200)).mean()
            )
            if pixels is not None
            else 1
        )
        check(
            "occluded_window_not_composited",
            current == a[3] and saved() > before and magenta < 0.01,
            magenta_ratio=magenta,
        )
        cover.destroy()

        excluded = Config.from_dict(config.to_dict() | {"excluded_apps": [a[3].app_name.upper()]})
        yield from configure(excluded)
        focus(a)
        before = saved()
        a[1].itemconfigure(a[2], fill="#ffcc22")
        yield 1
        check(
            "case_insensitive_process_exclusion",
            saved() == before and engine.snapshot()["reason"] == "excluded",
        )
        yield from configure(config)
        focus(a)
        yield 1
        check("exclusion_removal_recovers", saved() > before)

        before = saved()
        old_target = a[3]
        a[0].destroy()
        allowed.remove(old_target)
        yield 0.8
        check("closed_target_invalidated", engine.snapshot()["target"] != old_target)
        recreated = fixture("A-RECREATED", 0)
        focus(recreated)
        yield 1
        check(
            "recreated_window_captured",
            saved() > before and any(r["window_title"] == recreated[3].title for r in store.rows()),
        )

        yield from pause()
        check("pause_completed", engine.paused.is_set())
        before = saved()
        recreated[1].itemconfigure(recreated[2], fill="#cc2222")
        yield 1
        check("pause_no_new_observations", saved() == before)
        engine.start()
        focus(recreated)
        yield 1
        check("resume_captured", saved() > before)
        check("no_unexpected_errors", not engine.snapshot()["counts"].get("errors", 0))
        bind(user, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
        report["monitor_count"] = user.GetSystemMetrics(80)
        report["elapsed_seconds"] = time.perf_counter() - started

    steps = plan()

    def tick():
        try:
            delay = next(steps)
            report["steps"].append(
                {"elapsed": time.perf_counter() - started, "snapshot": reduced_snapshot(engine)}
            )
            root.after(int(delay * 1000), tick)
        except StopIteration:
            root.quit()
        except Exception as exc:
            report["issues"].append(type(exc).__name__)
            root.quit()

    try:
        root.after(100, tick)
        root.mainloop()
    finally:
        check("close_completed", close_with_events(engine, root, 15))
        for thread in engine._threads:
            thread.join(2)
        check("managed_threads_released", all(not t.is_alive() for t in engine._threads))
        report["final"] = reduced_snapshot(engine)
        report["observations"] = store.rows()
        report["elapsed_seconds"] = time.perf_counter() - started
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        store.close()
        root.destroy()
    passed = not report["issues"] and all(c["passed"] for c in report["checks"])
    print(json.dumps({"report": str(output / "report.json"), "passed": passed}), flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
