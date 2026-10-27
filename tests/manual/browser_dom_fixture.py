"""Serve only generated visibility cases for an isolated browser DOM extraction check."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PAGE = b"""<!doctype html><html><head><title>Synthetic DOM visibility</title>
<script src="/collector.js" defer></script>
<style>body{font:20px sans-serif;margin:25px}.covered{position:relative;width:400px;height:40px}
.cover{position:absolute;inset:0;background:white;z-index:2}.clipped{height:30px;overflow:hidden;width:400px}
.clipped p{margin-top:100px}</style></head><body>
<p>VISIBLE_SAFE</p><div style="opacity:0"><p>HIDDEN_ALPHA</p></div>
<p hidden>HIDDEN_ATTR</p><div class="clipped"><p>HIDDEN_CLIPPED</p></div>
<div class="covered">HIDDEN_COVERED<span class="cover"></span></div>
<textarea>HIDDEN_FORM</textarea><div contenteditable>HIDDEN_EDIT</div>
<p style="position:absolute;top:5000px">HIDDEN_OFFSCREEN</p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE
        content_type = "text/html; charset=utf-8"
        if self.path == "/collector.js":
            source = Path("frontend/src/extension/visibility.js").read_text(encoding="utf-8")
            collector = source[source.index("function visiblePage()") :]
            body = (
                collector
                + """\naddEventListener('load',async()=>{
const result=visiblePage();
const report={passed:result.blocks.some(b=>b.text.includes('VISIBLE_SAFE'))
 &&!result.blocks.some(b=>b.text.includes('HIDDEN_')),result};
const pre=document.createElement('pre');pre.textContent=JSON.stringify(report,null,2);
document.body.append(pre);
await fetch('/report',{method:'POST',body:JSON.stringify(report)});});"""
            ).encode()
            content_type = "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if (
            self.path != "/report"
            or length > 65536
            or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}"
        ):
            self.send_error(400)
            return
        report = json.loads(self.rfile.read(length))
        (Path("data/browser-dom-acceptance") / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    output = Path("data/browser-dom-acceptance")
    output.mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    (output / "ready.json").write_text(
        json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), encoding="utf-8"
    )
    timer = threading.Timer(180, server.shutdown)
    timer.start()
    try:
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
