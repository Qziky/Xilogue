"""Render the bundled extension popup against fake Chrome APIs and synthetic HTTP responses."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ASSETS = Path("src/xilogue/browser_extension")
TOKEN = "a" * 43
MOCK = b"""
let stored = {};
window.chrome = {
  storage: {session: {set: async value => { stored = value; }, get: async () => stored}},
  tabs: {
    query: async () => [{id:1, windowId:1, incognito:false,
      url:'https://example.test/', title:'SYNTHETIC'}],
    captureVisibleTab: async () => 'data:image/png;base64,' +
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLTTAAAAABJRU5ErkJggg=='
  },
  scripting: {executeScript: async () => [{result:{url:'https://example.test/', title:'SYNTHETIC',
    blocks:[{text:'SYNTHETIC ONLY',rect:[0,0,1,1]}], viewport:[1,1], truncated:false}}]}
};
"""
PAGE = b"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="popup.css"><script src="mock.js"></script>
<script type="module" src="popup.js"></script></head><body><div id="root"></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        paths = {
            "/": (PAGE, "text/html; charset=utf-8"),
            "/mock.js": (MOCK, "application/javascript"),
            "/popup.js": ((ASSETS / "popup.js").read_bytes(), "application/javascript"),
            "/popup.css": ((ASSETS / "popup.css").read_bytes(), "text/css"),
            "/icons/48.png": ((ASSETS / "icons/48.png").read_bytes(), "image/png"),
        }
        if self.path not in paths:
            self.send_error(404)
            return
        body, content_type = paths[self.path]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if (
            self.headers.get("Authorization") != "Bearer " + TOKEN
            or self.path not in ("/api/extension/ticket", "/api/extension/capture")
            or length > 100_000
        ):
            self.send_error(400)
            return
        body = json.loads(self.rfile.read(length))
        if self.path.endswith("ticket"):
            result = {"ticket": "synthetic-ticket"}
        else:
            assert body["ticket"] == "synthetic-ticket"
            assert body["blocks"][0]["text"] == "SYNTHETIC ONLY"
            self.server.report["capture_requests"] += 1
            (self.server.output / "report.json").write_text(
                json.dumps(self.server.report, indent=2), encoding="utf-8"
            )
            result = {"id": "synthetic-result"}
        encoded = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.output = args.output
    server.report = {"capture_requests": 0, "real_extension": False, "real_screenshot": False}
    origin = f"http://127.0.0.1:{server.server_port}"
    (args.output / "ready.json").write_text(
        json.dumps({"url": origin, "pair": {"endpoint": origin, "token": TOKEN}}), encoding="utf-8"
    )
    timer = threading.Timer(300, server.shutdown)
    timer.start()
    try:
        print(origin, flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
        (args.output / "report.json").write_text(
            json.dumps(server.report, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
