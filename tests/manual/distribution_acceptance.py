"""Verify frozen app with synthetic data, actual bundled OCR and a stopped native tray."""

import argparse
import http.client
import json
import queue
import struct
import subprocess
import threading
import time
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import pefile
from web_fixture import seed
from windows_acceptance import environment

from xilogue.config import Config
from xilogue.storage.store import Store


def verify_executable_icon(executable):
    """Compare actual PE icon resources with the project's multi-size ICO payloads."""
    expected = files("xilogue").joinpath("assets/icon.ico").read_bytes()
    _, kind, count = struct.unpack_from("<HHH", expected)
    assert kind == 1 and count >= 4
    expected_images = set()
    for index in range(count):
        entry = struct.unpack_from("<BBBBHHII", expected, 6 + 16 * index)
        size, offset = entry[-2:]
        expected_images.add(expected[offset : offset + size])
    image = pefile.PE(str(executable))
    try:
        types = {entry.id: entry for entry in image.DIRECTORY_ENTRY_RESOURCE.entries}

        def content(entry):
            leaf = entry.directory.entries[0].data.struct
            return image.get_data(leaf.OffsetToData, leaf.Size)

        icons = {entry.id: content(entry) for entry in types[3].directory.entries}
        group = content(types[14].directory.entries[0])
        _, group_kind, group_count = struct.unpack_from("<HHH", group)
        assert group_kind == 1 and group_count == count
        actual_images = set()
        for index in range(group_count):
            entry = struct.unpack_from("<BBBBHHIH", group, 6 + 14 * index)
            actual_images.add(icons[entry[-1]])
        assert actual_images == expected_images
        return group_count
    finally:
        image.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    output = Path("data/distribution-acceptance") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    )
    output.mkdir(parents=True)
    root = (output / "app").resolve()
    report = {
        "environment": environment(),
        "executable": str(executable),
        "capture_requested": False,
    }
    report["executable_icon_images"] = verify_executable_icon(executable)
    store = Store(root, Config())
    try:
        seed(store)
        with store.db:
            store.db.execute("DELETE FROM ocr_results")
            store.db.execute("UPDATE processing_jobs SET status='pending',attempts=0")
    finally:
        store.close()
    process = None
    try:
        result = subprocess.run(
            [str(executable), "--data-dir", str(root), "--ocr-once"],
            capture_output=True,
            timeout=90,
            text=True,
        )
        report["ocr_exit"] = result.returncode
        report["ocr"] = json.loads(result.stdout)
        assert result.returncode == 0 and report["ocr"]["processed"] == 2, result.stderr
        lines = queue.Queue()
        process = subprocess.Popen(
            [str(executable), "--data-dir", str(root), "--no-browser"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def reader():
            for line in process.stdout:
                lines.put(line)

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        deadline = time.monotonic() + 30
        url = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line.startswith("Web UI: "):
                url = urlsplit(line.removeprefix("Web UI: ").strip())
                break
        assert url is not None, "frozen_service_not_ready"
        token = url.fragment.removeprefix("token=")

        def call(path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=15)
            headers = {
                "Authorization": "Bearer " + token,
                "Origin": f"http://127.0.0.1:{url.port}",
                "Content-Type": "application/json",
            }
            try:
                connection.request(
                    "POST" if body is not None else "GET",
                    path,
                    json.dumps(body) if body else None,
                    headers,
                )
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        status, raw = call("/api/status")
        snapshot = json.loads(raw)
        assert (
            status == 200
            and snapshot["state"] == "stopped"
            and snapshot["counts"].get("saved", 0) == 0
        )
        static = files("xilogue").joinpath("static")
        for asset in ("app.js", "app.css", "logo.svg"):
            status, data = call("/assets/" + asset)
            assert status == 200 and data == static.joinpath(asset).read_bytes()
        report["offline_assets_match"] = True
        assert call("/api/browser/extension.zip")[0] == 200
        assert len(json.loads(call("/api/search?q=" + quote("数据库"))[1])["results"]) > 0
        report["default_state"] = snapshot["state"]
        assert snapshot["desktop"]["alive"] and snapshot["desktop"]["commands_alive"]
        report["native_tray"] = snapshot["desktop"]
        report["backup_bytes"] = len(call("/api/backup")[1])
        call("/api/control", {"action": "exit"})
        report["server_exit"] = process.wait(30)
        reader_thread.join(3)
        assert report["server_exit"] == 0 and not reader_thread.is_alive()
        report["passed"] = True
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(10)
            report["forced_stop"] = True
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(output.as_posix())


if __name__ == "__main__":
    main()
