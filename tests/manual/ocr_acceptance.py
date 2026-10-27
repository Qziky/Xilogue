"""Explicit local OCR acceptance on generated text images; no GUI or capture."""

import argparse
import gc
import hashlib
import importlib.metadata
import json
import socket
import time
import weakref
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from windows_acceptance import environment

from xilogue.collector.engine import Engine
from xilogue.collector.models import CaptureFrame, Target
from xilogue.config import Config
from xilogue.processing.change import Decision
from xilogue.processing.ocr import RapidBackend, process_pending
from xilogue.storage.store import Store, encode_webp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-pass", action="store_true", help="验证仅 OCR 模式及自动暂停/会话释放"
    )
    args = parser.parse_args()
    output = Path("data/ocr-acceptance") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    )
    output.mkdir(parents=True)
    config = Config(ocr_enabled=not args.engine_pass)
    store = Store(output / "app", config)
    report = {
        "environment": environment(),
        "config": config.to_dict(),
        "cases": [],
        "capture_performed": False,
        "python_network_attempts": 0,
        "mode": "engine_ocr_only" if args.engine_pass else "cli",
        "capture_backend_created": 0,
        "foreground_checks": 0,
    }
    cases = [
        (
            "chinese",
            "C:/Windows/Fonts/msyh.ttc",
            ["个人记忆 测试", "本地保存 中文检索", "数据库恢复 观察记录"],
            ["个人记忆", "本地保存", "中文检索", "数据库", "恢复", "中", "数据"],
        ),
        (
            "english",
            "C:/Windows/Fonts/consola.ttf",
            ["昔录 · Xilogue OCR", "HELLO Windows 2026", "Atomic commit local only"],
            ["Personal", "Memory", "Windows", "2026", "Atomic", "commit"],
        ),
        (
            "code",
            "C:/Windows/Fonts/consola.ttf",
            [
                "def capture_frame(window_id):",
                "    if changed_ratio > 0.04:",
                "        save_webp(frame)",
                "    return generation + 1",
            ],
            ["capture_frame", "window_id", "changed_ratio", "save_webp", "generation"],
        ),
    ]
    ids = []
    backend_refs = []
    session_refs = []
    engine = None

    def factory(threads):
        backend = RapidBackend(threads)
        backend_refs.append(weakref.ref(backend))
        sessions = [
            backend.engine.text_det.infer.session,
            backend.engine.text_cls.infer.session,
            backend.engine.text_rec.session.session,
        ]
        session_refs.extend(weakref.ref(session) for session in sessions)
        report["providers"] = [session.get_providers() for session in sessions]
        report["session_threads"] = [
            session.get_session_options().intra_op_num_threads for session in sessions
        ]
        return backend

    class NoCaptureMonitor:
        def foreground(self):
            report["foreground_checks"] += 1
            raise AssertionError("ocr_only_must_not_inspect_foreground")

        def desktop_available(self):
            return True  # Generated images only; no user desktop or native APIs.

        def idle_seconds(self):
            raise AssertionError("ocr_only_must_not_inspect_input")

    def forbid_capture():
        report["capture_backend_created"] += 1
        raise AssertionError("ocr_only_must_not_capture")

    def deny_network(*args, **kwargs):
        report["python_network_attempts"] += 1
        raise AssertionError("network_forbidden_in_local_ocr_acceptance")

    try:
        for name, font_path, lines, _expected in cases:
            image = Image.new("RGB", (1500, 440), "white")
            draw, font = ImageDraw.Draw(image), ImageFont.truetype(font_path, 40)
            for index, line in enumerate(lines):
                draw.text((40, 30 + 85 * index), line, fill="black", font=font)
            pixels = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            frame = CaptureFrame(
                pixels,
                Target(1, 1, 1, "synthetic-ocr.exe", name),
                1,
                datetime.now(UTC),
                time.perf_counter(),
            )
            item = store.save(frame, encode_webp(pixels, 90), Decision(True, "fixture", {}))
            ids.append(item["id"])
        with (
            patch.object(socket.socket, "connect", deny_network),
            patch.object(socket.socket, "connect_ex", deny_network),
            patch.object(socket, "create_connection", deny_network),
        ):
            if args.engine_pass:
                engine = Engine(
                    config, store, NoCaptureMonitor(), forbid_capture, ocr_backend_factory=factory
                )
                started = time.perf_counter()
                engine.start(mode="ocr_only")
                if not engine.paused.wait(45):
                    raise AssertionError("ocr_pass_did_not_finish")
                report["snapshot_after_pass"] = engine.snapshot()
                stats = store.ocr_stats()
                report["processing"] = {
                    "processed": stats["done"],
                    "failed": stats["failed"],
                    "seconds": time.perf_counter() - started,
                    "jobs": stats,
                }
                assert engine.snapshot()["counts"].get("saved", 0) == 0
                assert engine.snapshot()["ocr"]["pass_finished"]
            else:
                report["processing"] = process_pending(store, factory)
        report["onnxruntime"] = importlib.metadata.version("onnxruntime")
        models = Path(str(files("rapidocr_onnxruntime") / "models"))
        report["model_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in models.glob("*.onnx")
        }
        for ident, (name, _, _, expected) in zip(ids, cases, strict=True):
            row = store.document(ident)
            checks = {
                query: any(hit["id"] == ident for hit in store.search(query)) for query in expected
            }
            report["cases"].append(
                {
                    "name": name,
                    "text": row["text"] if row else None,
                    "lines": json.loads(row["lines_json"]) if row else [],
                    "search_checks": checks,
                    "passed": all(checks.values()),
                }
            )
        gc.collect()
        report["backend_released"] = all(ref() is None for ref in backend_refs)
        report["sessions_released"] = all(ref() is None for ref in session_refs)
        report["passed"] = (
            all(case["passed"] for case in report["cases"])
            and report["processing"]["failed"] == 0
            and report["python_network_attempts"] == 0
            and all(p == ["CPUExecutionProvider"] for p in report["providers"])
            and report["backend_released"]
            and report["sessions_released"]
            and report["capture_backend_created"] == 0
            and report["foreground_checks"] == 0
        )
    finally:
        if engine is not None:
            assert engine.close().wait(15)
            for thread in engine._threads:
                thread.join(1)
            report["alive_threads"] = [t.name for t in engine._threads if t.is_alive()]
        store.close()
        report["ended_at_utc"] = datetime.now(UTC).isoformat()
        path = output / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(path.resolve()), flush=True)
    print(json.dumps({"passed": report["passed"], "processing": report["processing"]}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
