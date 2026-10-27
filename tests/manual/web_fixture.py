"""Synthetic browser acceptance server. It cannot capture the user's desktop."""

import argparse
import hashlib
import json
import threading
import time
import weakref
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from windows_acceptance import environment

from xilogue.collector.engine import Engine
from xilogue.collector.models import CaptureFrame, Target
from xilogue.config import Config
from xilogue.processing.change import Decision
from xilogue.processing.ocr_types import OcrDocument, OcrLine
from xilogue.storage.store import Store, encode_webp
from xilogue.web import serve


class FixtureMonitor:
    def __init__(self):
        self.foreground_checks = 0

    def foreground(self):
        self.foreground_checks += 1
        return Target(11, 12, 13, "synthetic.exe", "SYNTHETIC ONLY")

    def desktop_available(self):
        return True

    def idle_seconds(self):
        return 0


class FixtureBackend:
    def start(self, *args):
        self.stopped = False  # No native handle or screen API is ever accessed.

    def stop(self):
        self.stopped = True

    def finished(self):
        return self.stopped


def seed(store, count=2):
    texts = ["本地记忆 数据库 中文检索", "capture_frame(window_id) / local only"]
    titles = ["合成笔记 · 数据库学习", "安全样本 <img src=x onerror=alert(1)>"]
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 38)
    images = []
    for content in texts:
        image = Image.new("RGB", (1400, 760), "#f5f7f5")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1400, 72), fill="#e1eae4")
        draw.text((35, 15), "SYNTHETIC WINDOW / WEB UI TEST", font=font, fill="#41634e")
        draw.text((70, 145), content, font=font, fill="#234831")
        for row in range(4):
            draw.rounded_rectangle(
                (70, 260 + row * 78, 1200 - row * 140, 280 + row * 78), 4, fill="#d9e5dc"
            )
        draw.text((70, 650), "Generated locally. No desktop capture.", font=font, fill="#8da696")
        pixels = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        images.append(pixels)
    prepared = []
    for index in range(count):
        content, pixels = texts[index % 2], images[index % 2]
        frame = CaptureFrame(
            pixels,
            Target(
                11,
                12,
                13,
                "synthetic.exe",
                titles[index % 2] + (f" · 合成记录 {index + 1:02}" if count > 2 else ""),
            ),
            1,
            datetime.now(UTC),
            time.perf_counter(),
        )
        item = store.save(frame, encode_webp(pixels, 78), Decision(True, "fixture", {}))
        prepared.append(item)
        job = store.claim_ocr()
        line = OcrLine(content, ((70, 140), (1200, 140), (1200, 210), (70, 210)), 0.99)
        store.finish_ocr(job, OcrDocument("synthetic-fixture/not-inference", (line,)))
    if count > 2:
        with store.lock, store.db:
            for index, item in enumerate(prepared):
                if index % 3 == 0:
                    continue
                store.db.execute("DELETE FROM ocr_results WHERE observation_id=?", (item["id"],))
                store.db.execute(
                    "UPDATE processing_jobs SET status=?,attempts=? WHERE observation_id=?",
                    (
                        "failed" if index % 3 == 1 else "pending",
                        3 if index % 3 == 1 else 0,
                        item["id"],
                    ),
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--records", type=int, choices=range(1, 201), default=2)
    parser.add_argument(
        "--memory", action="store_true", help="预置合成活动、摘要、回顾与向量，不运行模型"
    )
    parser.add_argument(
        "--ocr-pass", action="store_true", help="仅 OCR 控件测试，模拟延迟，不执行模型"
    )
    parser.add_argument(
        "--slow-control", action="store_true", help="使打开目录操作变为 3 秒的无副作用等待"
    )
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True)
    store = Store(output / "app", Config(ocr_enabled=not args.ocr_pass))
    config_path = output / "app" / "config.json"
    config_path.write_text(json.dumps(store.config.to_dict()), encoding="utf-8")
    seed(store, args.records)
    if args.memory:
        with store.db:
            yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            store.db.execute("UPDATE observations SET captured_at_utc=?", (yesterday,))
        store.queue_analysis()
        while job := store.claim_analysis():
            evidence = job["evidence"][0]
            store.finish_analysis(
                job,
                {
                    "summary": "合成材料：用于验证来源、活动和回顾。",
                    "topics": ["本地数据库"],
                    "keywords": ["本地", "数据库"],
                    "importance": 0.7,
                    "confidence": 0.8,
                    "uncertainties": ["合成夹具，不是真实模型推断"],
                    "claims": [
                        {
                            "text": "合成窗口包含本地文字",
                            "source_id": evidence["id"],
                            "quote": evidence["text"][:40],
                        }
                    ],
                },
                "synthetic/not-real-inference",
            )
        store.queue_embeddings()
        while job := store.claim_embedding():
            store.finish_embedding(job, [1.0, 0.0, 1.0], "synthetic", "fixture-digest")
        from zoneinfo import ZoneInfo

        day = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
        store.generate_review(day, "Asia/Shanghai")
    if args.ocr_pass:
        with store.lock, store.db:
            store.db.execute("DELETE FROM ocr_results")
            store.db.execute("UPDATE processing_jobs SET status='pending',attempts=0")

    refs = []

    class MockOcr:
        def recognize(self, pixels):
            time.sleep(1.2)
            line = OcrLine(
                "合成 OCR 批次 数据库 capture_frame",
                ((70, 140), (1200, 140), (1200, 210), (70, 210)),
                0.99,
            )
            return OcrDocument("mock-ocr/not-real-inference", (line,))

    def inference(threads):
        if not args.ocr_pass:
            raise AssertionError("fixture_only_uses_prepared_text")
        backend = MockOcr()
        refs.append(weakref.ref(backend))
        return backend

    report = {
        "environment": environment(),
        "desktop_capture": False,
        "real_inference": False,
        "capture_backend_created": 0,
        "assets_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path("src/xilogue/static").iterdir()
        },
    }
    monitor = FixtureMonitor()

    def capture_backend():
        report["capture_backend_created"] += 1
        return FixtureBackend()

    engine = Engine(store.config, store, monitor, capture_backend, ocr_backend_factory=inference)
    timer = threading.Timer(args.timeout, engine.close)
    timer.daemon = True
    timer.start()
    try:

        def ready(runtime):
            if args.slow_control:
                original = runtime.control

                def controlled(action):
                    if action == "open-data":
                        with runtime._commands:
                            until = time.perf_counter() + 3
                            report["pause_during_slow_control"] = False
                            while time.perf_counter() < until:
                                if runtime.engine.snapshot()["state"] in ("pausing", "paused"):
                                    report["pause_during_slow_control"] = True
                                time.sleep(0.02)
                        return {"state": runtime.engine.snapshot()["state"]}
                    return original(action)

                runtime.control = controlled
            (output / "ready.json").write_text(json.dumps({"url": runtime.url}), encoding="utf-8")

        serve(engine, store, config_path, open_browser=False, on_ready=ready)
    finally:
        timer.cancel()
        report.update(
            ended_at_utc=datetime.now(UTC).isoformat(),
            state=engine.snapshot()["state"],
            alive_threads=[t.name for t in engine._threads if t.is_alive()],
            storage=store.stats(),
            foreground_checks=monitor.foreground_checks,
            ocr_backend_released=all(ref() is None for ref in refs),
        )
        store.close()
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
