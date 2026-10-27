import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest


@pytest.mark.parametrize("entry", ["fixture", "production"])
def test_real_loopback_process_starts_stopped_exits_and_releases_port(tmp_path, entry):
    root = Path(__file__).parents[2]
    output = tmp_path / "run"
    if entry == "fixture":
        args = ["tests/manual/web_fixture.py", "--output", str(output), "--timeout", "30"]
    else:
        args = ["-m", "xilogue", "--data-dir", str(output), "--no-browser"]
    stdout, stderr = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        process = subprocess.Popen([sys.executable, *args], cwd=root, stdout=out, stderr=err)
    try:
        deadline = time.monotonic() + 20
        url = None
        while time.monotonic() < deadline:
            for line in stdout.read_text(encoding="utf-8").splitlines():
                if line.startswith("Web UI: "):
                    url = line.removeprefix("Web UI: ")
            if url:
                break
            assert process.poll() is None, stderr.read_text(encoding="utf-8")
            time.sleep(0.02)
        assert url is not None
        parsed = urlsplit(url)
        assert parsed.hostname == "127.0.0.1"
        origin = f"http://127.0.0.1:{parsed.port}"
        token = parse_qs(parsed.fragment)["token"][0]
        headers = {
            "Authorization": "Bearer " + token,
            "Origin": origin,
            "Content-Type": "application/json",
        }

        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(origin + path, data=data, headers=headers), timeout=5) as response:
                return json.loads(response.read())

        before = request("/api/status")
        assert before["state"] == "stopped"
        assert before["counts"].get("saved", 0) == 0
        assert before["storage"]["observations"] == (2 if entry == "fixture" else 0)
        request("/api/control", {"action": "exit"})
        assert process.wait(timeout=15) == 0
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", parsed.port), timeout=1)
        if entry == "fixture":
            report = json.loads((output / "report.json").read_text())
            assert report["state"] == "closed" and report["alive_threads"] == []
        else:
            assert not list(output.rglob("*.webp"))
    finally:
        if process.poll() is None:
            process.kill()  # Only the test child created above.
            process.wait(timeout=5)
