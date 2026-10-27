"""Authenticated loopback Web UI, separate from capture and inference workers."""

from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import threading
import webbrowser
from collections import deque
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from xilogue.config import Config, ConfigError, load_config

MAX_BODY = 65536
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


class LocalGuard:
    def __init__(self, app, runtime):
        self.app, self.runtime = app, runtime

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {}
        for key, value in scope["headers"]:
            headers.setdefault(key, []).append(value)
        style_nonce = secrets.token_urlsafe(24) if scope["path"] == "/" else None
        if style_nonce:
            scope.setdefault("state", {})["style_nonce"] = style_nonce
        error = None
        extension = scope["path"].startswith("/api/extension/")
        extension_origin = headers.get(b"origin", [])
        paired_origin = extension_origin[0].decode("latin1") if len(extension_origin) == 1 else ""
        preflight = extension and scope["method"] == "OPTIONS"
        host = self.runtime.origin.removeprefix("http://").encode()
        if headers.get(b"host") != [host]:
            error = (403, "host_rejected")
        elif scope.get("client", ("",))[0] not in ("127.0.0.1", "::1"):
            error = (403, "client_rejected")
        elif scope["path"].startswith("/api/"):
            auth = headers.get(b"authorization", [])
            expected = ("Bearer " + self.runtime.token).encode()
            origin = headers.get(b"origin", [])
            if preflight:
                requested = headers.get(b"access-control-request-headers", [b""])
                allowed = len(requested) == 1 and set(
                    requested[0].decode("latin1").lower().replace(" ", "").split(",")
                ) <= {"authorization", "content-type"}
                if (
                    not self.runtime.browser.allowed_origin(paired_origin)
                    or not allowed
                    or headers.get(b"access-control-request-method") not in ([b"POST"], [b"GET"])
                ):
                    error = (403, "extension_preflight_rejected")
            elif extension and (
                len(auth) != 1
                or not self.runtime.browser.authenticated(paired_origin, auth[0].decode("latin1"))
            ):
                error = (401, "extension_authorization_required")
            elif not extension and (
                len(auth) != 1 or not secrets.compare_digest(auth[0], expected)
            ):
                error = (401, "authorization_required")
            elif not extension and origin and origin != [self.runtime.origin.encode()]:
                error = (403, "origin_rejected")
            elif not extension and headers.get(b"sec-fetch-site") == [b"cross-site"]:
                error = (403, "cross_site_rejected")
            elif (
                not extension
                and scope["method"] == "POST"
                and origin != [self.runtime.origin.encode()]
            ):
                error = (403, "origin_required")
            elif scope["method"] == "POST" and headers.get(b"content-type") != [
                b"application/json"
            ]:
                error = (415, "json_required")
        if error:
            return await JSONResponse({"error": error[1]}, error[0], headers=SECURITY_HEADERS)(
                scope, receive, send
            )
        cors = (
            {
                "Access-Control-Allow-Origin": paired_origin,
                "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST",
                "Access-Control-Allow-Headers": "authorization, content-type",
            }
            if extension
            else {}
        )
        if preflight:
            return await Response(status_code=204, headers=SECURITY_HEADERS | cors)(
                scope, receive, send
            )
        if scope["method"] == "POST":
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > (
                    3 * 1024 * 1024 if scope["path"] == "/api/extension/capture" else MAX_BODY
                ):
                    return await JSONResponse(
                        {"error": "body_too_large"}, 413, headers=SECURITY_HEADERS
                    )(scope, receive, send)
                chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    break
            body = b"".join(chunks)
            delivered = False
            original_receive = receive

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()

            receive = replay

        async def secured(message):
            if message["type"] == "http.response.start":
                security = SECURITY_HEADERS | cors
                if style_nonce:
                    security = security | {
                        "Content-Security-Policy": SECURITY_HEADERS[
                            "Content-Security-Policy"
                        ].replace("style-src 'self';", f"style-src 'self' 'nonce-{style_nonce}';")
                    }
                message["headers"] += [
                    (k.lower().encode(), v.encode()) for k, v in security.items()
                ]
            await send(message)

        await self.app(scope, receive, secured)


class WebRuntime:
    def __init__(self, engine, store, config_path, origin, *, token=None, notice=None):
        self.engine, self.store, self.config_path = engine, store, Path(config_path)
        self.origin, self.token = origin, token or secrets.token_urlsafe(32)
        self.marker = "[XILOGUE-" + secrets.token_hex(8) + "]"
        self.notice = notice
        self.config_error = ""
        self._commands, self._events_lock = threading.Lock(), threading.Lock()
        self._events, self._sequence = deque(maxlen=300), 0
        self._model_condition = threading.Condition()
        self._model_tasks = 0
        self._closing = False
        self._failed_providers = []
        self.desktop = None
        from xilogue.web_browser import BrowserBridge

        self.browser = BrowserBridge(self)
        previous = engine.restrict
        self._previous_restrict = previous
        if engine.snapshot()["state"] not in ("stopped", "paused"):
            raise RuntimeError("web_ui_requires_stopped_engine")
        engine.restrict = lambda target: (
            target.pid != os.getpid()
            and self.marker not in target.title
            and (previous is None or previous(target))
        )

    @property
    def url(self):
        return self.origin + "/#token=" + self.token

    def snapshot(self, after=0):
        snap = self.engine.snapshot()
        with self.store.lock:
            latest = self.store.observation(snap["latest"]["id"]) if snap["latest"] else None
            if latest is None:
                latest = self.store.latest_observation()
            snap["latest"] = (
                {k: latest[k] for k in ("id", "captured_at_utc", "capture_reason")}
                if latest
                else None
            )
        snap["target"] = asdict(snap["target"]) if snap["target"] is not None else None
        snap["timings"] = {
            key: {"count": len(values), "mean_ms": sum(values) * 1000 / len(values)}
            for key, values in snap["timings"].items()
            if values
        }
        with self._events_lock:
            for _ in range(100):
                try:
                    event = self.engine.events.get_nowait()
                except queue.Empty:
                    break
                self._sequence += 1
                self._events.append(
                    {"seq": self._sequence, **{k: event[k] for k in ("at", "kind", "reason")}}
                )
            snap["events"] = [event for event in self._events if event["seq"] > after]
        snap["config_error"] = self.config_error
        snap["notice"] = self.notice() if self.notice else None
        snap["desktop"] = self.desktop.snapshot() if self.desktop else None
        with self._model_condition:
            snap["model_queries"] = self._model_tasks
            snap["model_cleanup_pending"] = len(self._failed_providers)
        return snap

    def control(self, action):
        if action == "pause":
            self.engine.pause()
            return {"state": self.engine.snapshot()["state"]}
        if action == "exit":
            with self._model_condition:
                self._closing = True
            self.engine.close()
            return {"state": "closing"}
        if not self._commands.acquire(blocking=False):
            raise HTTPException(409, "command_busy")
        try:
            if action in ("start", "process-ocr", "process-analysis"):
                if self.config_error:
                    raise HTTPException(409, "configuration_invalid")
                self.engine.start(
                    mode={"process-ocr": "ocr_only", "process-analysis": "analysis_only"}.get(
                        action, "capture"
                    )
                )
            elif action == "reload":
                try:
                    self.engine.reload(load_config(self.config_path))
                    self.config_error = ""
                except ConfigError:
                    self.config_error = "configuration_invalid"
                    raise
            elif action == "open-data":
                os.startfile(self.store.root)
            elif action == "retry-ocr":
                if self.engine.snapshot()["state"] not in ("stopped", "paused"):
                    raise HTTPException(409, "retry_requires_pause")
                self.store.retry_ocr()
            else:
                raise HTTPException(400, "unknown_action")
        finally:
            self._commands.release()
        return {"state": self.engine.snapshot()["state"]}

    def save_config(self, data):
        if not self._commands.acquire(blocking=False):
            raise HTTPException(409, "command_busy")
        temporary = None
        try:
            if self.engine.snapshot()["state"] not in ("stopped", "paused"):
                raise HTTPException(409, "save_requires_pause")
            config = Config.from_dict(data)
            temporary = self.config_path.with_name(
                "." + self.config_path.name + "." + secrets.token_hex(8)
            )
            with temporary.open("x", encoding="utf-8") as file:
                file.write(json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.config_path)
            try:
                self.engine.reload(config)
            except Exception:
                self.config_error = "configuration_reload_required"
                raise
            self.config_error = ""
            return config.to_dict()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self._commands.release()

    def image(self, ident):
        with self.store.lock:
            row = self.store.observation(ident)
            if row is None:
                raise HTTPException(404, "image_unavailable")
            path = self.store.image_path(row["image_path"])
            if path.stat().st_size > 64 * 1024 * 1024:
                raise HTTPException(413, "image_too_large")
            return path.read_bytes()

    def related_query(self, q, semantic=False, app=None):
        if not semantic:
            return self.store.related(q, app=app)
        if not isinstance(q, str) or not q.strip() or len(q) > 400:
            raise ValueError("semantic_query_requires_1_to_400_characters")
        with self._model_condition:
            if self._closing:
                raise RuntimeError("application_closing")
            self._model_tasks += 1
        provider = None
        try:
            if self.engine.analysis is None:
                raise RuntimeError("analysis_worker_unavailable")
            factory = self.engine.analysis.factory
            provider = getattr(factory, "for_embedding", factory)(self.engine.config)
            with self._model_condition:
                if self._closing:
                    raise RuntimeError("application_closing")
            vectors = provider.embed([q])
            if len(vectors) != 1:
                raise ValueError("invalid_query_embedding")
            return self.store.related(
                q,
                vector=vectors[0],
                model=self.engine.config.embedding_model,
                digest=provider.embedding_digest,
                app=app,
            )
        except Exception as exc:
            result = self.store.related(q, app=app)
            result["notice"] = getattr(exc, "code", "semantic_unavailable")
            return result
        finally:
            try:
                if provider is not None:
                    provider.close()
            except Exception:
                with self._model_condition:
                    self._failed_providers.append(provider)
                self.engine._event("error", "model_release_unconfirmed")
                raise RuntimeError("model_release_unconfirmed") from None
            finally:
                with self._model_condition:
                    self._model_tasks -= 1
                    self._model_condition.notify_all()

    def wait_model_operations(self):
        with self._model_condition:
            self._closing = True
            while self._model_tasks:
                self._model_condition.wait(0.2)
            failed, self._failed_providers = self._failed_providers, []
        for provider in failed:
            try:
                provider.close()
            except Exception:
                self.engine._event("error", "model_release_unconfirmed")

    def record_action(self, ident, action):
        if not self._commands.acquire(blocking=False):
            raise HTTPException(409, "command_busy")
        try:
            if action == "delete":
                result = self.store.delete_observation(ident)
                self.engine.refresh_storage()
                return result
            if action == "retry-ocr":
                if self.engine.snapshot()["state"] not in ("stopped", "paused"):
                    raise HTTPException(409, "retry_requires_pause")
                with self.store.lock:
                    if self.store.observation(ident) is None:
                        raise HTTPException(404, "document_unavailable")
                    if self.store.retry_ocr(ident) != 1:
                        raise HTTPException(409, "ocr_not_failed")
                return {"status": "pending"}
            raise HTTPException(400, "unknown_action")
        finally:
            self._commands.release()


def create_app(runtime):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalGuard, runtime=runtime)
    assets = files("xilogue") / "static"

    @app.exception_handler(Exception)
    async def error_handler(request, exc):
        return JSONResponse(
            {"error": "operation_failed", "code": type(exc).__name__}, 500, headers=SECURITY_HEADERS
        )

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": "invalid_request", "code": type(exc).__name__}, 400)

    @app.exception_handler(RuntimeError)
    async def conflict(request, exc):
        return JSONResponse({"error": "operation_unavailable", "code": type(exc).__name__}, 409)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return (
            (assets / "index.html")
            .read_text(encoding="utf-8")
            .replace("__XILOGUE_MARKER__", runtime.marker)
            .replace("__STYLE_NONCE__", request.state.style_nonce)
        )

    @app.get("/assets/{name}")
    def asset(name: str):
        types = {
            "app.js": "application/javascript",
            "app.css": "text/css",
            "logo.svg": "image/svg+xml",
        }
        if name not in types:
            raise HTTPException(404)
        return Response((assets / name).read_bytes(), media_type=types[name])

    @app.get("/api/status")
    def status(after: int = 0):
        return runtime.snapshot(after)

    @app.post("/api/control")
    async def control(request: Request):
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) != {"action"}
            or not isinstance(body["action"], str)
        ):
            raise HTTPException(400, "invalid_action")
        return await run_in_threadpool(runtime.control, body["action"])

    @app.get("/api/config")
    def configuration():
        return {
            "config": runtime.engine.config.to_dict(),
            "data_dir": str(runtime.store.root),
            "config_path": str(runtime.config_path),
        }

    @app.post("/api/config")
    async def save_configuration(request: Request):
        return await run_in_threadpool(runtime.save_config, await request.json())

    @app.get("/api/search")
    def search(
        q: str,
        limit: int = 100,
        app: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ):
        return {
            "results": runtime.store.search(q, limit=limit, app=app, after=after, before=before)
        }

    @app.get("/api/observations/{ident}")
    def detail(ident: str):
        with runtime.store.lock:
            row = runtime.store.document(ident)
            ready = row is not None
            row = row or runtime.store.observation(ident)
            if row is None:
                raise HTTPException(404, "document_unavailable")
            job = runtime.store.db.execute(
                "SELECT status,attempts FROM processing_jobs WHERE observation_id=?", (ident,)
            ).fetchone()
            return {k: row[k] for k in ("id", "app_name", "window_title", "captured_at_utc")} | {
                "text": row.get("text", ""),
                "engine": row.get("engine"),
                "ocr_ready": ready,
                "ocr_status": job["status"] if job else None,
                "ocr_attempts": job["attempts"] if job else 0,
                "lines": json.loads(row["lines_json"]) if ready else [],
                "browser": (
                    dict(page)
                    if (
                        page := runtime.store.db.execute(
                            "SELECT url,title,truncated,nearest_desktop_id FROM browser_pages "
                            "WHERE observation_id=?",
                            (ident,),
                        ).fetchone()
                    )
                    else None
                ),
            }

    @app.get("/api/observations")
    def observations(
        q: str = "",
        limit: int = 30,
        app: str | None = None,
        after: str | None = None,
        before: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ):
        return runtime.store.observations_page(
            query=q, limit=limit, app=app, after=after, before=before, status=status, cursor=cursor
        )

    @app.post("/api/observations/{ident}/actions")
    async def record_action(ident: str, request: Request):
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) != {"action"}
            or not isinstance(body["action"], str)
        ):
            raise HTTPException(400, "invalid_action")
        result = await run_in_threadpool(runtime.record_action, ident, body["action"])
        return JSONResponse(
            result, status_code=202 if result["status"] == "cleanup_pending" else 200
        )

    @app.get("/api/observations/{ident}/image")
    def image(ident: str):
        return Response(runtime.image(ident), media_type="image/webp")

    from xilogue.web_memory import install_memory_routes

    install_memory_routes(app, runtime)
    from xilogue.web_browser import install_browser_routes

    install_browser_routes(app, runtime)
    from xilogue.web_backup import install_backup_routes

    install_backup_routes(app, runtime)
    return app


def serve(
    engine, store, config_path, *, port=0, open_browser=True, notice=None, on_ready=None, tray=False
):
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    watcher = None
    runtime = None
    desktop_tray = None
    try:
        sock.bind(("127.0.0.1", port))
        origin = "http://127.0.0.1:" + str(sock.getsockname()[1])
        runtime = WebRuntime(engine, store, config_path, origin, notice=notice)
        if tray:
            from xilogue.desktop import Tray

            desktop_tray = Tray(runtime)
            runtime.desktop = desktop_tray
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(runtime),
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="critical",
                proxy_headers=False,
                limit_concurrency=16,
                backlog=16,
                timeout_keep_alive=2,
                timeout_graceful_shutdown=10,
            )
        )

        def watch():
            while not server.started:
                if engine.closed.wait(0.05) or server.should_exit:
                    return
            print("Web UI: " + runtime.url, flush=True)
            if on_ready:
                on_ready(runtime)
            if open_browser:
                try:
                    webbrowser.open(runtime.url)
                except OSError:
                    print("Open the Web UI link above in your browser.", flush=True)
            engine.closed.wait()
            runtime.wait_model_operations()
            server.should_exit = True

        watcher = threading.Thread(target=watch, name="memory-web-lifecycle", daemon=True)
        watcher.start()
        server.run(sockets=[sock])
    finally:
        engine.close().wait()
        if desktop_tray is not None:
            desktop_tray.close()
        if runtime is not None:
            runtime.wait_model_operations()
        from xilogue.processing.ollama import OllamaProvider

        OllamaProvider.close_managed_services()
        for thread in engine._threads:
            thread.join()
        if watcher is not None:
            watcher.join(2)
        sock.close()
