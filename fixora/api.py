"""Fixora Phase 2 — FastAPI server with live SSE events.

  python -m uvicorn fixora.api:app --host 127.0.0.1 --port 8000

  FIXORA_API_TOKEN=... python -m uvicorn fixora.api:app --host 0.0.0.0 --port 8000
  # clients: Authorization: Bearer <FIXORA_API_TOKEN>

  POST /runs {"repo": "https://github.com/owner/repo", "task": "..."}  -> {run_id}
  GET  /runs/{id}/events   # SSE stream of structured events
  GET  /runs/{id}          # current state: diff, tests before/after, review, timings
  POST /runs/{id}/approve  # apply the reviewed diff (local) or write a patch (GitHub)
  POST /runs/{id}/reject   # discard the run
  GET  /runs/{id}/patch    # download the patch file (GitHub runs, after approval)

API accepts PUBLIC GITHUB URLs ONLY (validated); local paths are rejected here —
use the CLI for local repos. One run at a time per server (429 otherwise).

Security: local use should bind 127.0.0.1. If FIXORA_API_TOKEN is set, every
route except /health requires Authorization: Bearer <token>. CORS origins
default to common localhost dev servers; override with CORS_ORIGINS (comma-
separated). No credentials are allowed cross-origin.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import queue
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .demos import list_demos, prepare_demo
from .github import RepoInputError
from .runner import RunBusyError, RunConflictError, RunManager, RunNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(title="Fixora Phase 2", version="0.2.0")

_cors_origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS",
    "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5173,http://localhost:5173",
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    token = os.getenv("FIXORA_API_TOKEN")
    path = request.url.path
    static_shell = request.method == "GET" and (
        path == "/" or path == "/favicon.ico" or path.startswith("/assets/")
    )
    if token and path != "/health" and not static_shell:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not supplied or not hmac.compare_digest(supplied, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authorization: Bearer <FIXORA_API_TOKEN> is required"},
            )
    return await call_next(request)

manager = RunManager()
_STREAM_CLOSE_STATUSES = {"awaiting_approval", "applied", "rejected", "failed", "error", "timed_out"}


class RunRequest(BaseModel):
    repo: str | None = Field(default=None, description="https://github.com/<owner>/<repo> (public only)")
    demo_id: str | None = Field(default=None, description="Built-in demo id from GET /demos")
    task: str = Field(..., min_length=1, max_length=2000)
    max_attempts: int | None = Field(default=None, ge=1, le=10)


def _configured_model(settings) -> str:
    provider = (settings.llm_provider or "").strip().lower()
    return {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "xai": settings.xai_model,
        "grok": settings.xai_model,
        "groq": settings.groq_model,
        "mock": "mock",
    }.get(provider, "")


@app.get("/")
def index():
    index_html = FRONTEND_DIST / "index.html"
    if index_html.is_file():
        return FileResponse(index_html)
    return {
        "service": "Fixora API",
        "status": "ok",
        "message": "Frontend not built. Run `npm --prefix frontend install && npm --prefix frontend run build`, or use /docs for the API.",
        "endpoints": ["/health", "/config", "/demos", "/runs", "/memory", "/docs", "/openapi.json"],
    }


@app.get("/config")
def config():
    from .config import load_settings
    settings = load_settings()
    return {"provider": settings.llm_provider, "model": _configured_model(settings)}


@app.get("/demos")
def demos():
    return {"demos": list_demos()}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/memory")
def memory_list():
    from .memory import load_lessons
    lessons = load_lessons()
    return {"count": len(lessons), "lessons": lessons[-20:]}


@app.post("/runs", status_code=202)
def create_run(req: RunRequest):
    repo = (req.repo or "").strip()
    demo_id = (req.demo_id or "").strip()
    if bool(repo) == bool(demo_id):
        raise HTTPException(status_code=400, detail="provide exactly one of repo or demo_id")
    try:
        if demo_id:
            source, display_repo = prepare_demo(demo_id)
            record = manager.start(str(source), req.task,
                                   max_attempts=req.max_attempts,
                                   allow_local=True,
                                   printer=print,
                                   display_repo=display_repo,
                                   patch_only=True)
        else:
            record = manager.start(repo, req.task,
                                   max_attempts=req.max_attempts,
                                   allow_local=False,
                                   printer=print)  # structured steps are streamed via SSE events
    except RepoInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RunBusyError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    return {"run_id": record.run_id, "status": record.status}


@app.get("/runs")
def list_runs():
    return {"runs": manager.list_runs()}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    try:
        return manager.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from None


@app.get("/runs/{run_id}/events")
def stream_events(run_id: str, request: Request):
    try:
        manager.events(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from None

    async def generate():
        emitter = manager.events(run_id)
        history, subscription = emitter.begin_stream()
        try:
            # Replay what already happened so late subscribers see the full run.
            try:
                last_seen = int(request.headers.get("last-event-id", "0"))
            except ValueError:
                last_seen = 0
            for event in history:
                seq = int(event.get("seq", 0) or 0)
                if seq <= last_seen:
                    continue
                yield _sse(event)
            last_ping = time.time()
            while True:
                try:
                    event = await asyncio.to_thread(subscription.get, True, 0.5)
                    yield _sse(event)
                except queue.Empty:
                    status = manager.get(run_id)["status"]
                    if status in _STREAM_CLOSE_STATUSES and subscription.empty():
                        yield _sse({"type": "stream_end", "run_id": run_id, "status": status})
                        return
                    if time.time() - last_ping > 15:
                        yield ": keepalive\n\n"
                        last_ping = time.time()
        finally:
            emitter.unsubscribe(subscription)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/runs/{run_id}/approve")
def approve_run(run_id: str):
    try:
        return manager.approve(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from None
    except RunConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.post("/runs/{run_id}/reject")
def reject_run(run_id: str):
    try:
        return manager.reject(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from None
    except RunConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.get("/runs/{run_id}/patch")
def download_patch(run_id: str):
    record_public = get_run(run_id)
    patch_file = record_public.get("patch_file")
    if not patch_file:
        raise HTTPException(status_code=404,
                            detail="no patch file (GitHub runs produce one after approval)")
    return FileResponse(patch_file, media_type="text/x-diff",
                        filename=f"{run_id}.patch")


def _sse(event: dict) -> str:
    event_type = event.get("type", "message")
    lines = []
    seq = event.get("seq")
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(event, default=str)}")
    return "\n".join(lines) + "\n\n"


if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_fallback(full_path: str):
    reserved = ("runs", "docs", "redoc", "openapi.json", "health", "config", "demos")
    if full_path == "" or full_path.split("/", 1)[0] in reserved:
        raise HTTPException(status_code=404, detail="Not Found")
    index_html = FRONTEND_DIST / "index.html"
    candidate = (FRONTEND_DIST / full_path).resolve()
    dist_real = FRONTEND_DIST.resolve()
    if dist_real in candidate.parents or candidate == dist_real:
        if candidate.is_file():
            return FileResponse(candidate)
    if index_html.is_file():
        return FileResponse(index_html)
    raise HTTPException(status_code=404, detail="Not Found")
