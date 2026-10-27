"""Explicit safe-window real-WGC acceptance and separate 1h/8h soak runs.

uv run --locked python tests/manual/windows_acceptance.py --seconds 60
uv run --locked python tests/manual/windows_acceptance.py --seconds 180 --interactive
uv run --locked python tests/manual/windows_acceptance.py --soak-suite

By default only the two synthetic HWNDs can pass the extra test restriction.
An explicit --external-title additionally admits one verified window identity.
This does not replace production policy, real lock/UAC tests or application tests.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import time
import tkinter as tk
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import psutil

from xilogue.collector.engine import Engine
from xilogue.collector.windows import WindowsMonitor, target_for, user
from xilogue.config import Config
from xilogue.storage.store import Store


def environment():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    return {
        "revision": revision,
        "dirty_worktree": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True)
        ),
        "source_sha256": {
            p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path("src/xilogue").rglob("*.py")
        },
        "python": platform.python_version(),
        "windows": platform.platform(),
        "cpu": platform.processor(),
        "memory_bytes": psutil.virtual_memory().total,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("windows-capture", "numpy", "opencv-python", "psutil")
        },
    }


def reduced_snapshot(engine):
    snap = engine.snapshot()
    snap["target"] = (
        {"app": snap["target"].app_name, "hwnd": snap["target"].hwnd} if snap["target"] else None
    )
    snap["timings"] = {
        key: {
            "count": len(items),
            "mean": float(np.mean(items)),
            "p95": float(np.percentile(items, 95)),
            "max": max(items),
        }
        for key, items in snap["timings"].items()
        if items
    }
    return snap


def external_targets(titles):
    from ctypes import wintypes

    targets = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    for title in titles:
        found = []

        @callback_type
        def inspect(hwnd, param, title=title, found=found):
            target = target_for(hwnd)
            if target is not None and target.title == title:
                found.append(target)
            return True

        user.EnumWindows(inspect, 0)
        if len(found) != 1:
            raise ValueError("external_title_requires_one_window")
        targets.extend(found)
    return targets


def allowed_window(target, allowed):
    # Production identity deliberately ignores title; a test allowlist must not.
    return any(target == item and target.title == item.title for item in allowed)


def close_with_events(engine, root, timeout=20):
    """Native WGC teardown can require messages on its target's UI thread."""
    done = engine.close()
    deadline = time.perf_counter() + timeout
    while not done.wait(0.01):
        if time.perf_counter() >= deadline:
            return False
        try:
            root.update()
        except tk.TclError:
            pass
    return True


def run(seconds, parent, interactive=False, fast=False, external_titles=()):
    external = external_targets(external_titles)
    output = parent / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
    output.mkdir(parents=True)
    config = Config(idle_policy="ignore")
    if fast:
        config = replace(
            config,
            sample_interval_seconds=0.25,
            foreground_stabilize_seconds=0.1,
            poll_interval_seconds=0.05,
            maintenance_seconds=2,
        )
    store = Store(output / "app", config)
    (output / "config-snapshot.json").write_text(
        json.dumps(config.to_dict(), indent=2), encoding="utf-8"
    )
    root = tk.Tk()
    if not interactive:
        root.withdraw()
    windows, canvases, text_items, blocks = [], [], [], []
    for index, name in enumerate(() if external else ("ALPHA", "BETA")):
        window = tk.Toplevel(root)
        window.title("昔录 · Xilogue SAFE " + name)
        window.geometry(f"740x480+{40 + index * 200}+{50 + index * 120}")
        canvas = tk.Canvas(window, bg="white", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            30, 30, text="SYNTHETIC ACCEPTANCE / " + name, anchor="w", font=("Segoe UI", 18)
        )
        for x, color in [(30, "red"), (210, "green"), (390, "blue")]:
            canvas.create_rectangle(x, 80, x + 140, 240, fill=color, outline=color)
        item = canvas.create_text(
            30, 290, text="SAFE frame 0 / code A17", anchor="w", font=("Consolas", 16)
        )
        block = canvas.create_rectangle(30, 340, 600, 410, fill="#ddeeff", outline="")
        windows.append(window)
        canvases.append(canvas)
        text_items.append(item)
        blocks.append(block)
    root.update()
    allowed = {target_for(int(w.frame(), 0)) for w in windows} | set(external)
    monitor = WindowsMonitor()
    engine = Engine(config, store, monitor, restrict=lambda t: allowed_window(t, allowed))
    if interactive:
        from tk_fixture_ui import FixtureControls

        FixtureControls(root, engine, store, output / "config-snapshot.json")
    report = {
        "environment": environment(),
        "requested_seconds": seconds,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "config": config.to_dict(),
        "fixture_hwnds": sorted(t.hwnd for t in allowed),
        "external_apps": [t.app_name for t in external],
        "interactive": interactive,
        "checks": [],
        "samples": [],
        "issues": [],
    }
    started, next_sample, old_phase = time.perf_counter(), 0.0, None
    process = psutil.Process()
    process.cpu_percent()
    pause_saved = None
    pending_idle = None
    pending_pause = None
    samples_file = (output / "samples.jsonl").open("a", encoding="utf-8")

    def sample(elapsed):
        snap = reduced_snapshot(engine)
        item = {
            "elapsed": elapsed,
            "at": datetime.now(UTC).isoformat(),
            "cpu_percent": process.cpu_percent(),
            "rss": process.memory_info().rss,
            "private_bytes": process.memory_info().private,
            "handles": process.num_handles(),
            "threads": process.num_threads(),
            "idle_seconds": monitor.idle_seconds(),
            **snap,
        }
        report["samples"].append(item)
        samples_file.write(json.dumps(item) + "\n")
        samples_file.flush()
        print(
            json.dumps(
                {
                    "run": output.name,
                    "elapsed": round(elapsed, 1),
                    "state": snap["state"],
                    "saved": snap["counts"].get("saved", 0),
                    "rss": item["rss"],
                    "errors": snap["counts"].get("errors", 0),
                }
            ),
            flush=True,
        )

    def configure_idle(policy):
        nonlocal pending_idle
        engine.pause()
        pending_idle = (policy, time.perf_counter() + 5)

    def tick():
        nonlocal next_sample, old_phase, pause_saved, pending_idle, pending_pause
        elapsed = time.perf_counter() - started
        if engine.closed.is_set() or elapsed >= seconds:
            engine.close()
            if engine.closed.is_set():
                root.quit()
            else:
                root.after(100, tick)
            return
        try:
            if pending_pause is not None:
                if engine.paused.is_set():
                    report["checks"].append({"check": "pause_barrier", "passed": True})
                    pause_saved = engine.snapshot()["counts"].get("saved", 0)
                    pending_pause = None
                elif time.perf_counter() >= pending_pause:
                    raise RuntimeError("pause_timeout")
                else:
                    root.after(50, tick)
                    return
            if pending_idle is not None:
                if engine.paused.is_set():
                    engine.reload(replace(engine.config, idle_policy=pending_idle[0]))
                    engine.start()
                    pending_idle = None
                elif time.perf_counter() >= pending_idle[1]:
                    raise RuntimeError("pause_timeout")
                else:
                    root.after(50, tick)
                    return
            if elapsed >= next_sample:
                sample(elapsed)
                next_sample = elapsed + (5 if seconds < 120 else 60)
                (output / "progress.json").write_text(
                    json.dumps(report["samples"][-1]), encoding="utf-8"
                )
            # A short run compresses phases; soak runs retain true wall-clock timing.
            phase_time = elapsed * (300 / seconds) if fast else elapsed % 300
            phase = (
                "alpha"
                if phase_time < 80
                else "static"
                if phase_time < 100
                else "beta"
                if phase_time < 180
                else "pause"
                if phase_time < 200
                else "idle"
                if phase_time < 240
                else "resume"
            )
            if external and engine.state == "stopped":
                engine.start()
            if not interactive and not external and phase != old_phase:
                if old_phase == "pause" and pause_saved is not None:
                    valid = engine.snapshot()["counts"].get("saved", 0) == pause_saved
                    report["checks"].append({"check": "pause_no_new_observations", "passed": valid})
                if phase == "pause":
                    engine.pause()
                    pending_pause = time.perf_counter() + 5
                elif phase == "idle":
                    configure_idle("pause")
                elif old_phase in ("pause", "idle"):
                    configure_idle("ignore")
                elif engine.state in ("stopped", "paused"):
                    engine.start()
                active = 1 if phase == "beta" else 0
                windows[active].lift()
                windows[active].focus_force()
                old_phase = phase
            if phase != "static":
                frame_number = int(elapsed / (1 if fast else 7))
                for i, canvas in enumerate(canvases):
                    canvas.itemconfigure(
                        text_items[i], text=f"SAFE frame {frame_number} / code A17"
                    )
                    colors = ("#ddeeff", "#ffeecc", "#ccffdd")
                    canvas.itemconfigure(blocks[i], fill=colors[frame_number % 3])
            if not interactive and not external and int(elapsed) % 30 == 15:
                windows[0].geometry("780x520+40+50")
            elif not interactive and not external and int(elapsed) % 30 == 0:
                windows[0].geometry("740x480+40+50")
        except Exception as exc:
            report["issues"].append(type(exc).__name__)
            engine.close()
        root.after(250, tick)

    try:
        root.after(100, tick)
        root.mainloop()
    finally:
        if not close_with_events(engine, root):
            report["issues"].append("close_timeout")
        for thread in engine._threads:
            thread.join(2)
        report.update(
            elapsed_seconds=time.perf_counter() - started,
            ended_at_utc=datetime.now(UTC).isoformat(),
            final=reduced_snapshot(engine),
            storage=store.stats(),
            managed_threads_alive=[t.name for t in engine._threads if t.is_alive()],
            observations=[
                {
                    key: row[key]
                    for key in (
                        "app_name",
                        "captured_at_utc",
                        "capture_reason",
                        "width",
                        "height",
                        "image_path",
                    )
                }
                for row in store.rows()
            ],
        )
        sample(report["elapsed_seconds"])
        samples_file.close()
        report["checks"].extend(
            [
                {
                    "check": "capture_produced_observations",
                    "passed": store.stats()["observations"] > 0,
                },
                {
                    "check": "managed_threads_released",
                    "passed": not report["managed_threads_alive"],
                },
                {
                    "check": "queue_bound",
                    "passed": engine.snapshot()["counts"].get("queue_peak", 0)
                    <= config.candidate_capacity,
                },
                {
                    "check": "no_engine_errors",
                    "passed": not engine.snapshot()["counts"].get("errors", 0),
                },
                {
                    "check": "full_requested_duration",
                    "passed": report["elapsed_seconds"] >= seconds,
                },
            ]
        )
        # Persist only sanitized report metadata; source images remain in ignored app/.
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        store.close()
        try:
            root.destroy()
        except tk.TclError:
            pass
    passed = not report["issues"] and all(x["passed"] for x in report["checks"])
    print(json.dumps({"report": str(output / "report.json"), "passed": passed}), flush=True)
    return passed, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--soak-suite", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument(
        "--external-title",
        action="append",
        default=[],
        help="仅允许额外捕获此精确标题的已准备安全窗口；须匹配唯一目标",
    )
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("seconds 必须为有限正数")
    if args.soak_suite and (args.fast or args.interactive or args.external_title):
        parser.error("soak-suite 不与 fast、interactive 或 external-title 混用")
    parent = Path("data/windows-acceptance").resolve()
    if args.soak_suite:
        suite = {"started_at": datetime.now(UTC).isoformat(), "runs": []}
        parent.mkdir(parents=True, exist_ok=True)
        status = parent / "soak-suite.json"
        for seconds in (3600, 28800):
            passed, output = run(seconds, parent)
            suite["runs"].append({"seconds": seconds, "passed": passed, "output": str(output)})
            status.write_text(json.dumps(suite, indent=2), encoding="utf-8")
            if not passed:
                raise SystemExit(1)
        suite["completed_at"] = datetime.now(UTC).isoformat()
        status.write_text(json.dumps(suite, indent=2), encoding="utf-8")
    else:
        passed, _ = run(args.seconds, parent, args.interactive, args.fast, args.external_title)
        raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
