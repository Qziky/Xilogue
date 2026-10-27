"""Model discovery reads metadata only; no inference, capture or secret persistence."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from test_api_provider import api_server as api_server_fixture
from test_web import web as web_fixture

from xilogue.config import Config
from xilogue.processing import model_catalog

api_server = api_server_fixture
web = web_fixture


def draft(base, **changes):
    return {"role": "analysis", "provider": "openai_compatible", "base_url": base, **changes}


def test_draft_discovery_uses_get_and_never_saves_credentials_or_sends_evidence(web, api_server):
    client, runtime, _ = web
    server, base = api_server
    original = runtime.engine.config
    response = client.post("/api/ai/models", json=draft(base, api_key="draft-synthetic-key"))
    assert response.status_code == 200
    assert response.json()["models"] == ["fixture/chat", "fixture/embed"]
    assert server.calls == [("/v1/models", "Bearer draft-synthetic-key", None)]
    assert runtime.engine.config == original and not runtime.credentials.path.exists()
    assert "draft-synthetic-key" not in response.text
    assert runtime.snapshot()["state"] == "stopped" and runtime.snapshot()["model_queries"] == 0
    assert runtime.engine.snapshot()["counts"].get("saved", 0) == 0
    client.headers.pop("Authorization")
    assert client.post("/api/ai/models", json=draft(base)).status_code == 401
    assert len(server.calls) == 1


def test_discovery_reuses_only_the_selected_endpoint_and_role_key(web, api_server, monkeypatch):
    client, runtime, _ = web
    server, base = api_server
    runtime.credentials.update(
        [("analysis", base, "analysis-key"), ("embedding", base, "embedding-key")]
    )
    for body, key in (
        (draft(base), "Bearer analysis-key"),
        (draft(base, role="embedding"), "Bearer embedding-key"),
        (draft(base + "/other"), None),
        (draft(base, api_key=None), None),
    ):
        assert client.post("/api/ai/models", json=body).json()["ok"]
        assert server.calls[-1][1] == key and server.calls[-1][2] is None
    monkeypatch.setenv("CATALOG_TEST_KEY", "environment-key")
    assert client.post(
        "/api/ai/models", json=draft(base, api_key=None, api_key_env="CATALOG_TEST_KEY")
    ).json()["ok"]
    assert server.calls[-1][1] == "Bearer environment-key"
    count = len(server.calls)
    rejected = client.post("/api/ai/models", json=draft("https://other.example/v1"))
    assert rejected.json()["error"] == "api_key_missing" and len(server.calls) == count


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"role": "bad", "provider": "ollama"},
        {"role": "analysis", "provider": "other"},
        {"role": "analysis", "provider": "openai_compatible", "base_url": "http://remote.example"},
        {"role": "analysis", "provider": "openai_compatible", "base_url": "https://key:pass@host"},
        {"role": "analysis", "provider": "ollama", "api_key": "unexpected"},
        {"role": "analysis", "provider": "ollama", "evidence": "must not be accepted"},
        {
            "role": "analysis",
            "provider": "openai_compatible",
            "base_url": "https://host/v1",
            "api_key": "invalid\r\nkey",
        },
    ],
)
def test_catalog_rejects_invalid_requests_without_changing_config(web, body):
    client, runtime, _ = web
    assert client.post("/api/ai/models", json=body).status_code == 400
    assert runtime.engine.config == Config() and not runtime.credentials.path.exists()


@pytest.mark.parametrize(
    "status,code",
    [
        (404, "model_list_unavailable"),
        (302, "api_redirect_rejected"),
        (401, "api_authentication_failed"),
        (429, "api_rate_limited"),
        (500, "api_request_failed"),
    ],
)
def test_catalog_errors_are_safe_and_never_trigger_fallback(web, api_server, status, code):
    client, _, _ = web
    server, base = api_server
    server.status = status
    server.get_reply = lambda path: {"error": "private-upstream-detail"}
    response = client.post("/api/ai/models", json=draft(base))
    assert response.json() == {"ok": False, "models": [], "error": code}
    assert "private-upstream-detail" not in response.text and len(server.calls) == 1


def test_catalog_deduplicates_bounds_and_filters_unusable_ids(web, api_server):
    client, _, _ = web
    server, base = api_server
    server.get_reply = lambda path: {
        "data": [
            {"id": "z-model"},
            {"id": "a-model"},
            {"id": "a-model"},
            {"id": "<script>"},
            {"id": "x" * 121},
            {"not_id": "missing"},
            None,
        ]
    }
    assert client.post("/api/ai/models", json=draft(base)).json()["models"] == [
        "a-model",
        "z-model",
    ]
    server.get_reply = lambda path: {"data": [{"id": f"fixture-{i}"} for i in range(2001)]}
    result = client.post("/api/ai/models", json=draft(base)).json()
    assert result["truncated"] and len(result["models"]) == 2000
    server.get_reply = lambda path: {"data": "not-an-array"}
    assert client.post("/api/ai/models", json=draft(base)).json()["error"] == "api_response_invalid"


def test_ollama_catalog_reads_tags_without_provider_initialization(web, monkeypatch):
    client, _, _ = web
    calls = []

    def metadata(base, key, timeout, path, **options):
        calls.append((base, key, path, options))
        return {
            "models": [
                {"name": "fixture-local:latest"},
                {"name": "fixture:cloud"},
                {"name": "remote", "remote_host": "cloud"},
            ]
        }

    monkeypatch.setattr(model_catalog, "request_json", metadata)
    response = client.post("/api/ai/models", json={"role": "analysis", "provider": "ollama"})
    assert response.json()["models"] == ["fixture-local:latest"]
    assert calls == [("http://127.0.0.1:11434", "", "/api/tags", {"method": "GET"})]


def test_managed_embedding_catalog_reads_only_cache_names_without_starting_a_service(
    web, tmp_path, monkeypatch
):
    client, _, _ = web
    cache = tmp_path / "catalog-cache"
    manifest = cache / "manifests/registry.ollama.ai/library/fixture-embed/latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("not weights; discovery must not parse this", encoding="utf-8")
    monkeypatch.setattr(model_catalog, "request_json", lambda *a, **k: pytest.fail("no-network"))
    response = client.post(
        "/api/ai/models",
        json={
            "role": "embedding",
            "provider": "ollama",
            "embedding_port": 0,
            "embedding_models_dir": str(cache),
        },
    )
    assert response.json() == {
        "ok": True,
        "models": ["fixture-embed:latest"],
        "source": "project_cache",
        "truncated": False,
    }


def test_catalog_requests_are_bounded_and_shutdown_waits_without_reporting_model_loading(
    web, monkeypatch
):
    _, runtime, _ = web
    entered, release = threading.Event(), threading.Event()

    def metadata(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"data": []}

    monkeypatch.setattr(model_catalog, "request_json", metadata)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(runtime.list_ai_models, draft("http://127.0.0.1:1/v1"))
        assert entered.wait(2)
        second = pool.submit(runtime.list_ai_models, draft("http://127.0.0.1:1/v1"))
        from test_engine import wait_for

        wait_for(lambda: runtime._catalog_tasks == 2)
        assert runtime.snapshot()["model_queries"] == 0
        with pytest.raises(HTTPException) as error:
            runtime.list_ai_models(draft("http://127.0.0.1:1/v1"))
        assert error.value.status_code == 429
        shutdown = pool.submit(runtime.wait_model_operations)
        assert not shutdown.done()
        release.set()
        assert first.result(3)["ok"] and second.result(3)["ok"]
        shutdown.result(3)
        assert runtime._catalog_tasks == 0
