import subprocess
import sys

from test_ocr_storage import complete

from xilogue.config import Config
from xilogue.storage.store import Store


def test_cli_search_status_and_empty_backlog_do_not_open_gui_or_capture(tmp_path):
    store = Store(tmp_path, Config())
    item, _ = complete(store)
    store.close()
    for args in (("--search", "数据库"), ("--ocr-status",), ("--ocr-once",)):
        result = subprocess.run(
            [sys.executable, "-m", "xilogue", "--data-dir", str(tmp_path), *args],
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip()
    store = Store(tmp_path, Config())
    try:
        assert store.stats()["observations"] == 1
        assert store.rows()[0]["id"] == item["id"]
    finally:
        store.close()
