import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_analysis import Provider
from test_engine import Backend, Monitor, wait_for
from test_ocr_storage import complete
from test_ocr_worker import LocalOcr
from test_storage import save

from xilogue.collector.engine import Engine
from xilogue.collector.models import Target
from xilogue.config import Config
from xilogue.storage.store import Store
from xilogue.web import MAX_BODY, WebRuntime, create_app

ORIGIN = "http://127.0.0.1:8811"


@pytest.fixture
def web(tmp_path, request):
    store = Store(tmp_path, Config())
    path = tmp_path / "config.json"
    path.write_text(json.dumps(store.config.to_dict()), encoding="utf-8")
    item, _ = complete(store)
    engine = Engine(
        store.config,
        store,
        Monitor(),
        Backend,
        ocr_backend_factory=(lambda threads: LocalOcr())
        if getattr(request, "param", False)
        else None,
        analysis_backend_factory=Provider
        if getattr(request, "param", False) == "analysis"
        else None,
    )
    runtime = WebRuntime(engine, store, path, ORIGIN, token="local-test-token")
    with TestClient(
        create_app(runtime),
        base_url=ORIGIN,
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        client.headers.update({"Authorization": "Bearer local-test-token", "Origin": ORIGIN})
        yield client, runtime, item
    assert engine.close().wait(5)
    for thread in engine._threads:
        thread.join(1)
        assert not thread.is_alive()
    store.close()


def test_static_assets_csp_and_default_stopped(web):
    client, runtime, _ = web
    page = client.get("/")
    assert page.status_code == 200 and runtime.marker in page.text
    assert runtime.token not in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/app.css").status_code == 200
    for path in ("/assets/config.json", "/memory.sqlite3", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404
    snap = client.get("/api/status").json()
    assert snap["state"] == "stopped" and snap["counts"].get("saved", 0) == 0
    assert snap["storage"]["observations"] == 1


def test_each_page_has_fresh_style_nonce_without_weakening_script_policy(web):
    import re

    client, _, _ = web
    first, second = client.get("/"), client.get("/")
    pattern = r'<meta name="xilogue-style-nonce" content="([\w-]+)">'
    nonce1 = re.search(pattern, first.text).group(1)
    nonce2 = re.search(pattern, second.text).group(1)
    assert nonce1 != nonce2
    for response, nonce in ((first, nonce1), (second, nonce2)):
        policy = response.headers["content-security-policy"]
        assert f"style-src 'self' 'nonce-{nonce}'" in policy
        assert "script-src 'self'" in policy
        assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy


def test_status_resolves_metadata_after_engine_saves_a_new_source(web):
    client, runtime, item = web
    # Engine.latest contains Store.save's compact result, not a full database row.
    runtime.engine.latest = dict(item)
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["latest"]["id"] == item["id"]
    assert response.json()["latest"]["capture_reason"] == "first_frame"


@pytest.mark.parametrize(
    "path",
    [
        "/api/status",
        "/api/config",
        "/api/search?q=test",
        "/api/observations",
        "/api/sessions",
        "/api/reviews?day=2026-10-27&timezone=Asia%2FShanghai",
        "/api/related?q=test",
        "/api/topics",
        "/api/browser/extension.zip",
        "/api/backup",
        "/api/observations/test",
        "/api/observations/test/image",
    ],
)
def test_every_data_endpoint_requires_bearer(web, path):
    client, _, _ = web
    assert client.get(path, headers={"Authorization": ""}).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example:8811"},
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
    ],
)
def test_host_origin_and_fetch_site_are_validated(web, headers):
    client, _, _ = web
    assert client.get("/api/status", headers=headers).status_code == 403


def test_mutations_require_post_json_origin_and_bounded_body(web):
    client, _, _ = web
    assert client.get("/api/control").status_code == 405
    assert (
        client.post("/api/control", json={"action": "pause"}, headers={"Origin": ""}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/control", content="action=start", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.post("/api/control", json={"action": "unknown"}).status_code == 400
    assert client.post("/api/control", json=[]).status_code == 400
    assert client.post("/api/control", json={"action": "start", "extra": 1}).status_code == 400
    assert (
        client.post(
            "/api/control",
            content=b"x" * (MAX_BODY + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/control",
            content=iter([b"x" * 40000, b"x" * 40000]),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )


def test_web_controls_preserve_pause_barrier_and_reject_self_window(web):
    client, runtime, _ = web
    other = Target(11, os.getpid() + 5000, 13, "msedge.exe", "ordinary page")
    assert runtime.engine.restrict(other)
    assert not runtime.engine.restrict(replace(other, title="昔录 · Xilogue " + runtime.marker))
    assert not runtime.engine.restrict(replace(other, pid=os.getpid()))
    assert client.post("/api/control", json={"action": "start"}).status_code == 200
    assert runtime.engine.snapshot()["state"] == "running"
    assert client.post("/api/control", json={"action": "pause"}).status_code == 200
    assert runtime.engine.paused.wait(3)
    assert client.get("/api/status").json()["state"] == "paused"
    assert client.post("/api/control", json={"action": "exit"}).status_code == 200
    assert runtime.engine.closed.wait(3)


def test_configuration_save_is_validated_atomic_and_requires_pause(web):
    client, runtime, _ = web
    config = client.get("/api/config").json()["config"]
    changed = config | {"sample_interval_seconds": 7.0}
    assert client.post("/api/config", json=changed).status_code == 200
    assert json.loads(runtime.config_path.read_text())["sample_interval_seconds"] == 7
    assert runtime.engine.config.sample_interval_seconds == 7
    assert (
        client.post("/api/config", json=changed | {"sample_interval_seconds": 0}).status_code == 400
    )
    assert runtime.engine.config.sample_interval_seconds == 7
    runtime.engine.start()
    assert client.post("/api/config", json=config).status_code == 409
    assert runtime.engine.pause().wait(3)
    runtime.config_path.write_text('{"sample_interval_seconds":0}')
    assert client.post("/api/control", json={"action": "reload"}).status_code == 400
    assert client.post("/api/control", json={"action": "start"}).status_code == 409
    assert client.post("/api/config", json=config).status_code == 200
    assert not runtime.config_error


def test_search_detail_and_images_are_scoped_to_live_sources(web):
    client, runtime, item = web
    response = client.get("/api/search", params={"q": "中"})
    assert response.status_code == 200 and len(response.json()["results"]) == 1
    detail = client.get(f"/api/observations/{item['id']}").json()
    assert "中文" in detail["text"] and detail["ocr_ready"] and len(detail["lines"]) == 1
    image = client.get(f"/api/observations/{item['id']}/image")
    assert image.status_code == 200 and image.content.startswith(b"RIFF")
    assert image.headers["content-type"] == "image/webp"
    assert client.get("/api/search", params={"q": "中文", "after": "2026-01-01"}).status_code == 400
    runtime.store.maintenance(now=datetime.now(UTC) + timedelta(days=8))
    assert client.get("/api/search", params={"q": "中"}).json()["results"] == []
    assert client.get(f"/api/observations/{item['id']}").status_code == 404
    assert client.get(f"/api/observations/{item['id']}/image").status_code == 404


def test_image_and_metadata_available_before_ocr_and_events_are_sanitized(web):
    client, runtime, _ = web
    pending = save(runtime.store)
    detail = client.get(f"/api/observations/{pending['id']}").json()
    assert not detail["ocr_ready"] and detail["lines"] == []
    for _ in range(1000):
        runtime.engine._event(
            "save",
            "first_frame",
            image_path="PRIVATE_SHOULD_NOT_LEAK",
            window_title="PRIVATE_SHOULD_NOT_LEAK",
        )
    response = client.get("/api/status").json()
    assert len(response["events"]) <= 100
    assert "PRIVATE_SHOULD_NOT_LEAK" not in json.dumps(response["events"])
    assert len(runtime._events) <= 300


def test_errors_do_not_disclose_exception_context(web, monkeypatch):
    client, runtime, _ = web

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_OCR_TEXT_PATH")

    monkeypatch.setattr(runtime.store, "search", fail)
    response = client.get("/api/search", params={"q": "text"})
    assert response.status_code == 500
    assert "PRIVATE" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_config_file_replace_failure_preserves_previous_config(web, monkeypatch):
    client, runtime, _ = web
    before = runtime.config_path.read_bytes()
    old = runtime.engine.config

    def fail(*args):
        raise OSError("injected")

    monkeypatch.setattr("xilogue.web.os.replace", fail)
    changed = old.to_dict() | {"sample_interval_seconds": 12}
    assert client.post("/api/config", json=changed).status_code == 500
    assert runtime.config_path.read_bytes() == before
    assert runtime.engine.config == old
    assert not list(runtime.config_path.parent.glob(".config.json.*"))


def test_browser_ui_title_is_rechecked_before_capture_commit(web):
    import threading

    _, runtime, _ = web
    entered, release = threading.Event(), threading.Event()

    def hook(stage, frame):
        if stage == "encoded":
            entered.set()
            assert release.wait(4)

    runtime.engine.hook = hook
    try:
        runtime.engine.start()
        wait_for(lambda: runtime.engine._backend is not None)
        runtime.engine._backend.send()
        assert entered.wait(3)
        runtime.engine.monitor.target = replace(
            runtime.engine.monitor.target, title="昔录 · Xilogue " + runtime.marker
        )
        release.set()
        wait_for(lambda: runtime.engine.snapshot()["counts"].get("invalidated", 0) > 0)
        assert runtime.store.stats()["observations"] == 1
        assert runtime.engine.snapshot()["counts"].get("saved", 0) == 0
    finally:
        release.set()


def test_pause_and_exit_do_not_wait_for_other_control_actions(web):
    client, runtime, _ = web
    runtime.engine.start()
    runtime._commands.acquire()
    try:
        assert client.post("/api/control", json={"action": "pause"}).status_code == 200
        assert runtime.engine.paused.wait(3)
        assert client.post("/api/control", json={"action": "start"}).status_code == 409
        assert client.post("/api/control", json={"action": "exit"}).status_code == 200
        assert runtime.engine.closed.wait(3)
    finally:
        runtime._commands.release()


def test_record_browsing_and_single_retry_require_stopped_state_and_valid_filters(web):
    client, runtime, item = web
    pending = save(runtime.store)
    job = runtime.store.claim_ocr()
    runtime.store.release_ocr(job, "ocr_failed")
    page = client.get("/api/observations", params={"limit": 1}).json()
    assert page["results"][0]["id"] == pending["id"] and page["next_cursor"]
    assert page["results"][0]["ocr_status"] == "failed"
    second = client.get("/api/observations", params={"cursor": page["next_cursor"]}).json()
    assert [r["id"] for r in second["results"]] == [item["id"]]
    assert second["next_cursor"] is None
    assert (
        client.get("/api/observations", params={"status": "failed"}).json()["results"][0]["id"]
        == pending["id"]
    )
    assert len(client.get("/api/observations", params={"q": "中文"}).json()["results"]) == 1
    assert client.get("/api/observations", params={"cursor": "bad"}).status_code == 400
    assert client.get("/api/observations", params={"status": "unknown"}).status_code == 400
    assert client.get("/api/observations", params={"after": "2026-01-01"}).status_code == 400
    route = f"/api/observations/{pending['id']}/actions"
    runtime.engine.start()
    assert client.post(route, json={"action": "retry-ocr"}).status_code == 409
    assert runtime.engine.pause().wait(3)
    assert client.post(route, json={"action": "retry-ocr"}).json() == {"status": "pending"}
    assert client.post(route, json={"action": "retry-ocr"}).status_code == 409
    detail = client.get(f"/api/observations/{pending['id']}").json()
    assert detail["ocr_status"] == "pending" and detail["ocr_attempts"] == 0


def test_single_delete_invalidates_latest_image_and_is_idempotent(web):
    client, runtime, item = web
    route = f"/api/observations/{item['id']}/actions"
    assert client.get("/api/status").json()["latest"]["id"] == item["id"]
    assert client.get(route).status_code == 405
    assert (
        client.post(route, json={"action": "delete"}, headers={"Authorization": ""}).status_code
        == 401
    )
    assert (
        client.post(
            route, json={"action": "delete"}, headers={"Origin": "http://evil.example"}
        ).status_code
        == 403
    )
    for body in ([], {}, {"action": "delete", "all": True}, {"action": 1}, {"action": "unknown"}):
        assert client.post(route, json=body).status_code == 400
    assert runtime.store.document(item["id"]) is not None
    assert client.post(route, json={"action": "delete"}).json() == {"status": "deleted"}
    snap = client.get("/api/status").json()
    assert snap["latest"] is None and snap["storage"]["observations"] == 0
    assert snap["storage"]["image_bytes"] == 0
    assert client.get(f"/api/observations/{item['id']}/image").status_code == 404
    assert client.get(f"/api/observations/{item['id']}").status_code == 404
    assert not client.get("/api/search", params={"q": "中文"}).json()["results"]
    assert client.post(route, json={"action": "delete"}).json() == {"status": "not_found"}
    assert client.post(route, json={"action": "retry-ocr"}).status_code == 404


def test_file_cleanup_failure_is_explicit_and_pausing_bypasses_pending_deletion(web):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client, runtime, item = web
    entered, release = threading.Event(), threading.Event()

    def fault(stage):
        if stage == "delete_marked":
            entered.set()
            assert release.wait(4)
            raise OSError("PRIVATE_FILE_CONTEXT")

    runtime.store.fault = fault
    runtime.engine.start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post, f"/api/observations/{item['id']}/actions", json={"action": "delete"}
        )
        try:
            assert entered.wait(3)
            assert client.post("/api/control", json={"action": "pause"}).status_code == 200
            assert runtime.engine.paused.wait(2)
        finally:
            release.set()
        result = future.result(timeout=3)
    assert result.status_code == 202 and result.json() == {"status": "cleanup_pending"}
    assert "PRIVATE" not in result.text
    assert client.get("/api/observations").json()["results"] == []
    assert client.get("/api/status").json()["latest"] is None
    runtime.store.fault = lambda _: None
    assert runtime.store.maintenance()["errors"] == 0


@pytest.mark.parametrize("web", [True], indirect=True)
def test_web_ocr_only_runs_saved_sources_with_disabled_auto_ocr_and_no_capture(web, monkeypatch):
    client, runtime, _ = web
    pending = save(runtime.store)
    runtime.engine.reload(replace(runtime.engine.config, ocr_enabled=False))

    def forbidden(*args):
        pytest.fail("ocr_only_must_not_capture_or_inspect_foreground")

    monkeypatch.setattr(runtime.engine.monitor, "foreground", forbidden)
    monkeypatch.setattr(runtime.engine, "backend_factory", forbidden)
    assert (
        client.post(
            "/api/control", json={"action": "process-ocr"}, headers={"Authorization": ""}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/control",
            json={"action": "process-ocr"},
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    assert client.post("/api/control", json={"action": "process-ocr"}).status_code == 200
    wait_for(lambda: runtime.engine.snapshot()["state"] == "paused")
    response = client.get("/api/status").json()
    assert response["mode"] == "ocr_only" and response["ocr"]["pass_finished"]
    assert not response["ocr"]["model_loaded"]
    assert response["counts"].get("saved", 0) == 0 and response["target"] is None
    assert runtime.store.document(pending["id"])["text"]
    monkeypatch.undo()
    assert client.post("/api/control", json={"action": "start"}).status_code == 200
    assert runtime.engine.snapshot()["mode"] == "capture"
    runtime.engine.pause().wait(3)


def test_ocr_only_requires_worker_and_valid_configuration(web):
    client, runtime, _ = web
    assert client.post("/api/control", json={"action": "process-ocr"}).status_code == 409
    runtime.config_error = "configuration_invalid"
    response = client.post("/api/control", json={"action": "process-ocr"})
    assert response.status_code == 409 and response.json()["detail"] == "configuration_invalid"


def test_session_routes_preserve_sources_and_require_pause_for_manual_edits(web):
    client, runtime, item = web
    other = save(runtime.store)
    page = client.get("/api/sessions").json()
    assert len(page["results"]) == 1
    ident = page["results"][0]["id"]
    assert client.get(f"/api/sessions/{ident}").json()["source_count"] == 2
    assert (
        client.post(
            "/api/sessions/actions",
            json={"action": "split", "id": ident, "before_id": other["id"]},
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    runtime.engine.start()
    assert client.post("/api/sessions/actions", json={"action": "rebuild"}).status_code == 409
    assert runtime.engine.pause().wait(3)
    response = client.post(
        "/api/sessions/actions", json={"action": "split", "id": ident, "before_id": other["id"]}
    )
    assert response.status_code == 200 and len(response.json()["ids"]) == 2
    assert runtime.store.observation(item["id"]) and runtime.store.observation(other["id"])


@pytest.mark.parametrize("web", ["analysis"], indirect=True)
def test_explicit_session_analysis_is_authenticated_and_traceable(web):
    client, runtime, item = web
    session_id = client.get("/api/sessions").json()["results"][0]["id"]
    assert (
        client.post(
            "/api/sessions/actions",
            json={"action": "analyze", "ids": [session_id]},
            headers={"Authorization": ""},
        ).status_code
        == 401
    )
    response = client.post("/api/sessions/actions", json={"action": "analyze", "ids": [session_id]})
    assert response.status_code == 200 and response.json()["queued"] == 1
    wait_for(lambda: runtime.engine.snapshot()["state"] == "paused")
    result = client.get(f"/api/sessions/{session_id}").json()
    assert result["insight"]["payload"]["claims"][0]["source_id"] == item["id"]
    assert runtime.engine.snapshot()["mode"] == "analysis_only"


@pytest.mark.parametrize("web", ["analysis"], indirect=True)
def test_review_and_feedback_routes_keep_user_corrections_local(web):
    from zoneinfo import ZoneInfo

    client, runtime, _ = web
    client.post("/api/sessions/actions", json={"action": "analyze"})
    wait_for(lambda: runtime.engine.snapshot()["state"] == "paused")
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    result = client.post("/api/reviews", json={"day": day, "timezone": "Asia/Shanghai"})
    assert result.status_code == 200 and len(result.json()["cards"]) == 1
    ident = result.json()["cards"][0]["session_id"]
    assert (
        client.post(
            "/api/reviews/feedback",
            json={"session_id": ident, "hidden": True},
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/reviews/feedback",
            json={"session_id": ident, "kind": "later", "correction": "用户修正"},
        ).status_code
        == 200
    )
    cards = client.get("/api/reviews", params={"day": day, "timezone": "Asia/Shanghai"}).json()[
        "cards"
    ]
    assert cards[0]["correction"] == "用户修正"


@pytest.mark.parametrize("web", ["analysis"], indirect=True)
def test_embedding_index_and_semantic_query_are_explicit_and_authenticated(web):
    client, runtime, _ = web
    assert client.post("/api/embeddings", json={}, headers={"Authorization": ""}).status_code == 401
    assert client.post("/api/embeddings", json={}).json()["queued"] == 1
    wait_for(lambda: runtime.engine.snapshot()["state"] == "paused")
    response = client.get("/api/related", params={"q": "无字面表达", "semantic": "true"})
    assert response.status_code == 200 and response.json()["results"][0]["cosine"] > 0.99
    assert runtime._model_tasks == 0
    assert runtime.engine.snapshot()["mode"] == "index_only"
