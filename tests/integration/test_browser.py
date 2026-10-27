import base64
import io
import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from PIL import Image
from test_engine import wait_for
from test_storage import frame
from test_web import web as web_fixture

from xilogue.collector.models import Target
from xilogue.processing.change import Decision
from xilogue.storage.store import encode_webp
from xilogue.web_browser import clean_url

EXTENSION = "chrome-extension://" + "a" * 32
web = web_fixture


def pair(client):
    response = client.post("/api/browser/pair", json={"extension_id": "a" * 32})
    assert response.status_code == 200, response.text
    return {
        "Origin": EXTENSION,
        "Authorization": "Bearer " + response.json()["token"],
        "Sec-Fetch-Site": "cross-site",
    }


def ready(client, runtime):
    headers = pair(client)
    runtime.engine.monitor.target = Target(1, 2, 3, "msedge.exe", "Safe example - Microsoft Edge")
    runtime.engine.start()
    wait_for(lambda: runtime.engine.snapshot()["target"] is not None)
    return headers


def payload(client, headers):
    ticket = client.post(
        "/api/extension/ticket",
        json={"url": "https://example.com/?token=private&a=1#secret", "title": "Safe example"},
        headers=headers,
    )
    assert ticket.status_code == 200, ticket.text
    raw = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(raw, "JPEG")
    return {
        "ticket": ticket.json()["ticket"],
        "image": "data:image/jpeg;base64," + base64.b64encode(raw.getvalue()).decode(),
        "blocks": [{"text": "可见证据", "rect": [0, 0, 99, 99]}],
        "viewport": [100, 100],
        "truncated": False,
        "captured_at": datetime.now(UTC).isoformat(),
    }


def test_pair_scope_preflight_revoke_and_no_automatic_capture(web):
    client, runtime, _ = web
    headers = pair(client)
    assert client.get("/api/extension/status", headers=headers).json()["state"] == "stopped"
    assert client.get("/api/status", headers=headers).status_code == 401
    preflight = {
        "Origin": EXTENSION,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type",
    }
    assert (
        client.options("/api/extension/capture", headers=preflight).headers[
            "access-control-allow-origin"
        ]
        == EXTENSION
    )
    assert client.options("/api/control", headers=preflight).status_code == 403
    assert (
        client.options(
            "/api/extension/capture",
            headers=preflight | {"Origin": "chrome-extension://" + "b" * 32},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/extension/ticket",
            json={"url": "https://example.com", "title": "Safe example"},
            headers=headers,
        ).status_code
        == 409
    )
    assert client.post("/api/browser/revoke", json={}).status_code == 200
    assert client.get("/api/extension/status", headers=headers).status_code == 401
    assert runtime.engine.snapshot()["state"] == "stopped"
    import zipfile

    with zipfile.ZipFile(io.BytesIO(client.get("/api/browser/extension.zip").content)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "popup.html",
            "popup.css",
            "popup.js",
            "icons/16.png",
            "icons/32.png",
            "icons/48.png",
            "icons/128.png",
        }
        assert b'"incognito": "not_allowed"' in archive.read("manifest.json")


def test_atomic_browser_document_and_delete_propagation(web):
    client, runtime, _ = web
    headers = ready(client, runtime)
    desktop = replace(frame(), target=runtime.engine.monitor.target)
    native = runtime.store.save(
        desktop, encode_webp(desktop.pixels, 78), Decision(True, "test", {})
    )
    body = payload(client, headers)
    response = client.post("/api/extension/capture", json=body, headers=headers)
    assert response.status_code == 200, response.text
    ident = response.json()["id"]
    detail = client.get("/api/observations/" + ident).json()
    assert detail["text"] == "可见证据" and detail["ocr_status"] == "done"
    assert detail["browser"]["url"] == "https://example.com/?a=1"
    assert detail["browser"]["nearest_desktop_id"] == native["id"]
    runtime.store.delete_observation(native["id"])
    assert client.get("/api/observations/" + ident).json()["browser"]["nearest_desktop_id"] is None
    assert runtime.store.observation(ident)["source_type"] == "browser_extension"
    assert client.post("/api/extension/capture", json=body, headers=headers).status_code == 409
    runtime.store.delete_observation(ident)
    assert not runtime.store.db.execute(
        "SELECT * FROM browser_pages WHERE observation_id=?", (ident,)
    ).fetchall()
    assert runtime.store.document(ident) is None


def test_pause_during_encoding_rejects_old_page(web):
    client, runtime, _ = web
    headers = ready(client, runtime)
    body = payload(client, headers)
    encoded, release = threading.Event(), threading.Event()
    runtime.engine.hook = lambda stage, frame: (
        (encoded.set(), release.wait(5)) if stage == "browser_encoded" else None
    )
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            client.post("/api/extension/capture", json=body, headers=headers)
        )
    )
    thread.start()
    try:
        assert encoded.wait(3)
        assert runtime.engine.pause().wait(3)
    finally:
        release.set()
        thread.join(5)
    assert result[0].status_code == 409
    assert len(runtime.store.rows()) == 1


def test_browser_transaction_failure_has_no_orphan_document(web):
    client, runtime, _ = web
    headers = ready(client, runtime)
    body = payload(client, headers)

    def fail(stage):
        if stage == "before_db_commit":
            raise OSError("injected")

    runtime.store.fault = fail
    assert client.post("/api/extension/capture", json=body, headers=headers).status_code == 500
    assert not runtime.store.db.execute("SELECT * FROM browser_pages").fetchall()
    assert len(runtime.store.rows()) == 1


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "http://localhost/", "http://u:p@example.com", "chrome://settings"],
)
def test_page_urls_are_controlled(url):
    with pytest.raises(ValueError):
        clean_url(url)
