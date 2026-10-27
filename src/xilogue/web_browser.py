"""Explicit extension pairing, single-use capture tickets and atomic visible-page evidence."""

import base64
import io
import json
import re
import secrets
import threading
import time
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import cv2
import numpy as np
from fastapi import HTTPException, Request
from fastapi.responses import Response
from PIL import Image
from starlette.concurrency import run_in_threadpool

from xilogue.collector.models import CaptureFrame
from xilogue.collector.policy import inspect_target
from xilogue.processing.change import Decision
from xilogue.processing.ocr_types import (
    OcrDocument,
    OcrLine,
    normalize_text,
    validate_document,
)
from xilogue.storage.store import encode_webp


def clean_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("invalid_page_url")
    url = urlsplit(value)
    if (
        url.scheme not in ("https", "http")
        or not url.hostname
        or url.username
        or url.password
        or url.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        raise ValueError("page_url_rejected")
    query = [
        (k, v)
        for k, v in parse_qsl(url.query, keep_blank_values=True)
        if not any(s in k.casefold() for s in ("token", "secret", "password", "auth", "key"))
    ]
    return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), ""))


class BrowserBridge:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock, self.capture_lock = threading.RLock(), threading.Lock()
        self.token, self.origin, self.expires = "", "", 0
        self.tickets = {}
        self.last_capture = 0

    def pair(self, ident):
        if self.runtime.engine.snapshot()["state"] not in ("stopped", "paused"):
            raise HTTPException(409, "pair_requires_pause")
        if not isinstance(ident, str) or not re.fullmatch("[a-p]{32}", ident):
            raise ValueError("invalid_extension_id")
        with self.lock:
            self.token = secrets.token_urlsafe(32)
            self.origin = "chrome-extension://" + ident
            self.expires = time.monotonic() + 3600
            self.tickets.clear()
            return {"endpoint": self.runtime.origin, "token": self.token, "valid_seconds": 3600}

    def revoke(self):
        with self.lock:
            self.token, self.origin, self.expires = "", "", 0
            self.tickets.clear()
        return {"revoked": True}

    def allowed_origin(self, origin):
        with self.lock:
            return bool(self.token and self.expires > time.monotonic() and origin == self.origin)

    def authenticated(self, origin, auth):
        with self.lock:
            return self.allowed_origin(origin) and secrets.compare_digest(
                auth, "Bearer " + self.token
            )

    def target(self, expected=None):
        engine = self.runtime.engine
        target, _ = inspect_target(engine.monitor, engine.config, expected, engine.restrict)
        snap = engine.snapshot()
        if (
            target is None
            or snap["state"] != "running"
            or snap["mode"] != "capture"
            or snap["target"] != target
            or target.app_name.casefold() not in ("msedge.exe", "chrome.exe")
        ):
            raise HTTPException(409, "foreground_browser_capture_required")
        return target, snap["generation"]

    def ticket(self, body):
        if not isinstance(body, dict) or set(body) != {"url", "title"}:
            raise ValueError("invalid_ticket_request")
        if not isinstance(body["title"], str) or not 1 <= len(body["title"]) <= 1024:
            raise ValueError("invalid_page_title")
        target, generation = self.target()
        if body["title"] not in target.title:
            raise HTTPException(409, "page_title_changed")
        with self.lock:
            now = time.monotonic()
            self.tickets = {k: v for k, v in self.tickets.items() if v["expires"] > now}
            if len(self.tickets) >= 8:
                raise HTTPException(429, "ticket_limit")
            ident = secrets.token_urlsafe(24)
            self.tickets[ident] = {
                "target": target,
                "generation": generation,
                "expires": now + 20,
                "url": clean_url(body["url"]),
                "title": body["title"],
                "pair_token": self.token,
            }
            return {"ticket": ident}

    def capture(self, body):
        if not self.capture_lock.acquire(blocking=False):
            raise HTTPException(429, "capture_busy")
        try:
            if not isinstance(body, dict) or set(body) != {
                "ticket",
                "image",
                "blocks",
                "viewport",
                "truncated",
                "captured_at",
            }:
                raise ValueError("invalid_capture_request")
            with self.lock:
                ticket = self.tickets.pop(body["ticket"], None)
            if ticket is None:
                raise HTTPException(409, "ticket_expired_or_consumed")

            def valid():
                try:
                    current, generation = self.target(ticket["target"])
                    with self.lock:
                        return (
                            self.token == ticket["pair_token"]
                            and self.expires > time.monotonic()
                            and ticket["expires"] > time.monotonic()
                            and generation == ticket["generation"]
                            and current.title == ticket["target"].title
                        )
                except HTTPException:
                    return False

            if not valid():
                raise HTTPException(409, "capture_invalidated")
            if time.monotonic() - self.last_capture < 1:
                raise HTTPException(429, "capture_rate_limited")
            stamp = datetime.fromisoformat(body["captured_at"])
            if stamp.tzinfo is None or abs((datetime.now(UTC) - stamp).total_seconds()) > 30:
                raise ValueError("invalid_capture_time")
            if type(body["truncated"]) is not bool:
                raise ValueError("invalid_truncated_flag")
            viewport = body["viewport"]
            if (
                not isinstance(viewport, list)
                or len(viewport) != 2
                or any(type(x) is not int or not 1 <= x <= 20000 for x in viewport)
            ):
                raise ValueError("invalid_viewport")
            if not isinstance(body["image"], str) or not body["image"].startswith(
                "data:image/jpeg;base64,"
            ):
                raise ValueError("jpeg_required")
            raw = base64.b64decode(body["image"].split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
                if image.format != "JPEG" or min(width, height) < 2 or width * height > 12_000_000:
                    raise ValueError("image_dimensions_rejected")
                image.verify()
            pixels = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if pixels is None:
                raise ValueError("image_decode_failed")
            if not isinstance(body["blocks"], list) or len(body["blocks"]) > 1024:
                raise ValueError("too_many_visible_blocks")
            lines = []
            for block in body["blocks"]:
                if not isinstance(block, dict) or set(block) != {"text", "rect"}:
                    raise ValueError("invalid_visible_block")
                rect = block["rect"]
                if (
                    not isinstance(rect, list)
                    or len(rect) != 4
                    or any(type(x) not in (int, float) for x in rect)
                ):
                    raise ValueError("invalid_visible_rect")
                x, y, right, bottom = [
                    v * (width / viewport[0] if i % 2 == 0 else height / viewport[1])
                    for i, v in enumerate(rect)
                ]
                if right < x or bottom < y:
                    raise ValueError("invalid_visible_rect")
                lines.append(
                    OcrLine(block["text"], ((x, y), (right, y), (right, bottom), (x, bottom)), 1.0)
                )
            document = OcrDocument(
                "browser-visible-dom/1:geometry-not-ocr-confidence", tuple(lines)
            )
            validate_document(document, width, height)
            if len(document.text) > 50000:
                raise ValueError("visible_text_too_large")
            engine, store = self.runtime.engine, self.runtime.store
            frame = CaptureFrame(
                pixels,
                ticket["target"],
                ticket["generation"],
                stamp,
                time.perf_counter(),
                backend="browser-extension/1:visible-tab",
            )
            encoded = encode_webp(pixels, engine.config.image_webp_quality)

            def insert(ident, now):
                source = store.db.execute(
                    """SELECT id FROM observations
                    WHERE source_type='desktop_capture' AND storage_state='ready'
                    AND process_id=? AND app_name=? AND window_title=?
                    AND captured_at_utc>=? AND captured_at_utc<=?
                    ORDER BY captured_at_utc DESC,id DESC LIMIT 1""",
                    (
                        ticket["target"].pid,
                        ticket["target"].app_name,
                        ticket["target"].title,
                        (stamp.astimezone(UTC) - timedelta(seconds=10)).isoformat(),
                        (stamp.astimezone(UTC) + timedelta(seconds=1)).isoformat(),
                    ),
                ).fetchone()
                store.db.execute(
                    "INSERT INTO browser_pages VALUES (?,?,?,?,?,?,?)",
                    (
                        ident,
                        ticket["url"],
                        ticket["title"],
                        int(body["truncated"]),
                        *viewport,
                        source[0] if source else None,
                    ),
                )
                store.db.execute(
                    "INSERT INTO ocr_results VALUES (?,?,?,?,?,?)",
                    (
                        ident,
                        document.text,
                        normalize_text(document.text),
                        json.dumps([asdict(line) for line in lines], ensure_ascii=False),
                        document.engine,
                        now,
                    ),
                )
                store.db.execute(
                    "UPDATE processing_jobs SET status='done' WHERE observation_id=?", (ident,)
                )

            engine.hook("browser_encoded", frame)
            with engine._commit:
                result = store.save(
                    frame,
                    encoded,
                    Decision(True, "explicit_browser_capture", {}),
                    validate=valid,
                    source_type="browser_extension",
                    on_insert=insert,
                )
                self.last_capture = time.monotonic()
                with engine._lock:
                    engine.latest = result
                    engine.counts["browser_saved"] += 1
            engine.refresh_storage()
            return result
        finally:
            self.capture_lock.release()


def install_browser_routes(app, runtime):
    @app.post("/api/browser/pair")
    async def pair(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"extension_id"}:
            raise ValueError("invalid_pair_request")
        return runtime.browser.pair(body["extension_id"])

    @app.post("/api/browser/revoke")
    def revoke():
        return runtime.browser.revoke()

    @app.get("/api/browser/extension.zip")
    def extension():
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            root = files("xilogue").joinpath("browser_extension")
            for name in (
                "manifest.json",
                "popup.html",
                "popup.js",
                "popup.css",
                "icons/16.png",
                "icons/32.png",
                "icons/48.png",
                "icons/128.png",
            ):
                archive.writestr(name, root.joinpath(name).read_bytes())
        return Response(
            output.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="xilogue-extension.zip"'},
        )

    @app.get("/api/extension/status")
    def status():
        return {"state": runtime.engine.snapshot()["state"], "explicit_only": True}

    @app.post("/api/extension/ticket")
    async def ticket(request: Request):
        return await run_in_threadpool(runtime.browser.ticket, await request.json())

    @app.post("/api/extension/capture")
    async def capture(request: Request):
        return await run_in_threadpool(runtime.browser.capture, await request.json())
