"""Explicit-control local Web UI and offline OCR/search commands."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from logging.handlers import RotatingFileHandler

from xilogue.config import Config, ConfigError, load_config, runtime_paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="昔录 · Xilogue — 本地 Web UI，默认停止采集")
    parser.add_argument("--data-dir", help="应用专用数据目录")
    parser.add_argument("--config", help="现有 JSON 配置文件")
    parser.add_argument("--port", type=int, default=0, help="本机端口，默认自动分配")
    parser.add_argument(
        "--no-browser", action="store_true", help="仅输出本次启动链接，不自动打开浏览器"
    )
    mode = parser.add_mutually_exclusive_group()
    parser.add_argument("--no-tray", action="store_true", help="不显示 Windows 托盘")
    mode.add_argument("--check-config", action="store_true", help="只校验配置，不启动服务或采集")
    mode.add_argument("--ocr-once", action="store_true", help="仅处理当前可领取 OCR 任务，不采集")
    mode.add_argument("--ocr-status", action="store_true", help="查看本地 OCR 任务状态，不采集")
    mode.add_argument("--search", help="按字面检索已有 OCR 文本，不采集")
    mode.add_argument("--backup", metavar="ZIP", help="离线备份数据到新 ZIP，不采集")
    mode.add_argument("--restore", metavar="ZIP", help="校验备份并恢复到新的 --data-dir")
    mode.add_argument(
        "--startup", choices=("enable", "disable", "status"), help="当前用户登录启动，仍默认停止"
    )
    parser.add_argument("--retry-ocr", action="store_true", help="配合 --ocr-once 重置失败任务")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--app", help="搜索时按进程名筛选")
    parser.add_argument("--after", help="搜索起始时间，ISO 8601 含时区")
    parser.add_argument("--before", help="搜索结束时间，ISO 8601 含时区，不含边界")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port 必须在 0–65535 之间")
    if args.retry_ocr and not args.ocr_once:
        parser.error("--retry-ocr 需要 --ocr-once")
    data, path = runtime_paths(args.data_dir, args.config)
    try:
        if args.startup:
            from xilogue.desktop import startup

            print(json.dumps(startup(args.startup, data, path), ensure_ascii=True, indent=2))
            return
        if args.restore:
            if not args.data_dir or args.config:
                parser.error("--restore 需要新的 --data-dir，不接受 --config")
            from xilogue.storage.backup import restore_backup

            print(json.dumps(restore_backup(args.restore, data), ensure_ascii=True, indent=2))
            return
        config = load_config(path) if args.config or path.exists() else Config()
        if args.check_config:
            print(json.dumps(config.to_dict(), ensure_ascii=True, indent=2))
            return
        from xilogue.storage.store import Store

        store = Store(data, config)
        try:
            if args.backup:
                from xilogue.storage.backup import export_backup

                print(json.dumps(export_backup(store, args.backup), ensure_ascii=True, indent=2))
                return
            if args.ocr_once or args.ocr_status or args.search is not None:
                if args.ocr_once:
                    from xilogue.processing.ocr import process_pending

                    if args.retry_ocr:
                        store.retry_ocr()
                    result = process_pending(store)
                elif args.ocr_status:
                    result = store.ocr_stats()
                else:
                    result = store.search(
                        args.search,
                        limit=args.limit,
                        app=args.app,
                        after=args.after,
                        before=args.before,
                    )
                print(json.dumps(result, ensure_ascii=True, indent=2))
                if args.ocr_once and result["failed"]:
                    parser.exit(1)
                return
            if not args.config and not path.exists():
                path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
            logs = store._inside(data / "logs")
            logs.mkdir(exist_ok=True)
            handler = RotatingFileHandler(
                store._inside(logs / "events.log"), maxBytes=262144, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger = logging.getLogger("xilogue")
            logger.addHandler(handler)
            engine = None
            try:
                from xilogue.collector.engine import Engine
                from xilogue.collector.windows import WindowsMonitor
                from xilogue.processing.credentials import Credentials
                from xilogue.processing.ocr import RapidBackend
                from xilogue.processing.providers import ProviderFactory
                from xilogue.web import serve

                engine = Engine(
                    config,
                    store,
                    WindowsMonitor(),
                    restrict=lambda t: t.pid != os.getpid(),
                    ocr_backend_factory=RapidBackend,
                    analysis_backend_factory=ProviderFactory(Credentials(store.root)),
                )
                serve(
                    engine,
                    store,
                    path,
                    port=args.port,
                    open_browser=not args.no_browser,
                    tray=not args.no_tray,
                )
            finally:
                if engine is not None:
                    engine.close().wait()
                logger.removeHandler(handler)
                handler.close()
        finally:
            store.close()
    except (ConfigError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"无法启动: {exc}\n")
