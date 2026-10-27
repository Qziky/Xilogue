"""Activity and derived-memory routes share the authenticated local UI boundary."""

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool


def install_memory_routes(app, runtime):
    @app.get("/api/related")
    def related(q: str, semantic: bool = False, app: str | None = None):
        return runtime.related_query(q, semantic, app)

    @app.get("/api/topics")
    def topics(timezone: str = "UTC"):
        return {"topics": runtime.store.topics(timezone)}

    @app.post("/api/embeddings")
    async def index_embeddings(request: Request):
        if await request.json() != {}:
            raise HTTPException(400, "invalid_index_request")

        def index():
            if not runtime._commands.acquire(blocking=False):
                raise HTTPException(409, "command_busy")
            try:
                if (
                    runtime.engine.snapshot()["state"] not in ("stopped", "paused")
                    or runtime.engine.analysis is None
                ):
                    raise HTTPException(409, "index_requires_pause")
                if runtime.config_error:
                    raise HTTPException(409, "configuration_invalid")
                result = runtime.store.queue_embeddings()
                if result["queued"]:
                    runtime.engine.start(mode="index_only")
                return result
            finally:
                runtime._commands.release()

        return await run_in_threadpool(index)

    @app.get("/api/reviews")
    def review(day: str, timezone: str, include_hidden: bool = False, offset: int = 0):
        return runtime.store.review(day, timezone, include_hidden=include_hidden, offset=offset)

    @app.post("/api/reviews")
    async def generate_review(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"day", "timezone"}:
            raise HTTPException(400, "invalid_review_request")
        return await run_in_threadpool(runtime.store.generate_review, body["day"], body["timezone"])

    @app.post("/api/reviews/feedback")
    async def feedback(request: Request):
        body = await request.json()
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("session_id"), str)
            or set(body) - {"session_id", "kind", "hidden", "correction"}
        ):
            raise HTTPException(400, "invalid_feedback")
        return await run_in_threadpool(
            runtime.store.feedback,
            body["session_id"],
            {k: v for k, v in body.items() if k != "session_id"},
        )

    @app.get("/api/sessions")
    def sessions(before: str | None = None, app: str | None = None, limit: int = 30):
        return runtime.store.sessions_page(before=before, app=app, limit=limit)

    @app.get("/api/sessions/{ident}")
    def session(ident: str, offset: int = 0):
        result = runtime.store.session(ident, offset=offset)
        if result is None:
            raise HTTPException(404, "session_unavailable")
        return result | {"insight": runtime.store.insight(ident)}

    @app.post("/api/sessions/actions")
    async def session_action(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("action"), str):
            raise HTTPException(400, "invalid_session_action")
        return await run_in_threadpool(edit_session, body)

    def edit_session(body):
        if not runtime._commands.acquire(blocking=False):
            raise HTTPException(409, "command_busy")
        try:
            if runtime.engine.snapshot()["state"] not in ("stopped", "paused"):
                raise HTTPException(409, "session_edit_requires_pause")
            if runtime.config_error:
                raise HTTPException(409, "configuration_invalid")
            if body["action"] == "analyze" and set(body) <= {"action", "ids"}:
                if runtime.engine.analysis is None:
                    raise HTTPException(409, "analysis_worker_unavailable")
                result = runtime.store.queue_analysis(body.get("ids"))
                if result["queued"]:
                    runtime.engine.start(mode="analysis_only")
                return result
            if body["action"] == "split" and set(body) == {"action", "id", "before_id"}:
                if not all(isinstance(body[key], str) for key in ("id", "before_id")):
                    raise ValueError("invalid_session_id")
                return {"ids": runtime.store.split_session(body["id"], body["before_id"])}
            if body["action"] == "merge" and set(body) == {"action", "ids"}:
                return {"id": runtime.store.merge_sessions(body["ids"])}
            if body["action"] == "rebuild" and set(body) == {"action"}:
                return runtime.store.rebuild_sessions()
            raise ValueError("invalid_session_action")
        finally:
            runtime._commands.release()
