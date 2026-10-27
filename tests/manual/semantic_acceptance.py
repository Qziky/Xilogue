"""Real embedding retrieval on generated evidence, with no desktop capture."""

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
    output = Path("data/semantic-acceptance") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    )
    output.mkdir(parents=True)
    store = Store(output / "app", Config())
    seed(store)
    store.queue_embeddings()
    report = {"passed": False, "environment": environment(), "capture_created": 0}

    class Monitor:
        def foreground(self):
            raise AssertionError("foreground_forbidden")

        def desktop_available(self):
            return True

    def capture():
        report["capture_created"] += 1
        raise AssertionError("capture_forbidden")

    engine = Engine(
        store.config, store, Monitor(), capture, analysis_backend_factory=OllamaProvider
    )
    provider = None
    try:
        started = time.perf_counter()
        engine.start(mode="index_only")
        assert engine.paused.wait(120), "index_timeout"
        report["index_seconds"] = time.perf_counter() - started
        report["index_stats"] = store.embedding_stats()
        provider = OllamaProvider.for_embedding(store.config)
        query = "保存中文内容的数据库"
        vector = provider.embed([query])[0]
        report["digest"] = provider.embedding_digest
        report["dimensions"] = len(vector)
        report["result"] = store.related(
            query,
            vector=vector,
            model=store.config.embedding_model,
            digest=provider.embedding_digest,
        )
        provider.close()
        provider = None
        inspector = OllamaProvider.for_embedding(store.config)
        report["after_models"] = list(inspector.original)
        inspector.close()
        report["passed"] = (
            report["index_stats"]["done"] == 2
            and report["capture_created"] == 0
            and bool(report["result"]["results"])
            and report["result"]["results"][0]["cosine"] is not None
            and store.config.embedding_model not in report["after_models"]
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        if provider:
            provider.close()
        OllamaProvider.close_managed_services()
        assert engine.close().wait(120)
        for thread in engine._threads:
            thread.join(1)
        report["alive_threads"] = [t.name for t in engine._threads if t.is_alive()]
        store.close()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(str((output / "report.json").resolve()), flush=True)
    print(json.dumps({"passed": report["passed"], "dimensions": report.get("dimensions")}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
