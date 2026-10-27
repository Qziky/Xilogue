"""Real local Session analysis on generated evidence; never invokes window capture."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from web_fixture import seed
from windows_acceptance import environment

from xilogue.collector.engine import Engine
from xilogue.config import Config
from xilogue.processing.ollama import OllamaProvider
from xilogue.storage.store import Store


def main():
    output = Path("data/ai-acceptance") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    )
    output.mkdir(parents=True)
    config = Config()
    store = Store(output / "app", config)
    seed(store)
    store.queue_analysis()
    report = {
        "environment": environment(),
        "capture_created": 0,
        "foreground_checks": 0,
        "passed": False,
    }

    class Monitor:
        def foreground(self):
            report["foreground_checks"] += 1
            raise AssertionError("capture_forbidden")

        def desktop_available(self):
            return True

    def capture():
        report["capture_created"] += 1
        raise AssertionError("capture_forbidden")

    class RecordingProvider(OllamaProvider):
        def _request(self, method, path, body=None):
            result = super()._request(method, path, body)
            if path == "/api/chat":
                report.setdefault("responses", []).append(result)
            return result

    inspector = engine = None
    try:
        inspector = OllamaProvider(config)
        report["before_models"] = list(inspector.original)
        inspector.close()
        engine = Engine(
            config, store, Monitor(), capture, analysis_backend_factory=RecordingProvider
        )
        started = time.perf_counter()
        engine.start(mode="analysis_only")
        assert engine.paused.wait(300), "analysis_timeout"
        report["seconds"] = time.perf_counter() - started
        report["snapshot"] = engine.snapshot()
        report["insights"] = [store.insight(row["id"]) for row in store.sessions_page()["results"]]
        after = OllamaProvider(config)
        report["after_models"] = list(after.original)
        after.close()
        report["passed"] = (
            store.analysis_stats()["done"] == 2
            and report["capture_created"] == 0
            and report["foreground_checks"] == 0
            and not engine.analysis.snapshot()["provider_active"]
            and not engine.analysis.snapshot()["cleanup_error"]
            and (
                config.ai_model in report["before_models"]
                or config.ai_model not in report["after_models"]
            )
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        if engine is not None:
            assert engine.close().wait(180)
            for thread in engine._threads:
                thread.join(1)
            report["alive_threads"] = [
                thread.name for thread in engine._threads if thread.is_alive()
            ]
        if inspector is not None:
            inspector.close()
        store.close()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(str((output / "report.json").resolve()), flush=True)
    print(json.dumps({"passed": report["passed"], "seconds": report.get("seconds")}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
