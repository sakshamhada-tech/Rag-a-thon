"""DevMate Phase 2 — FastAPI server with live SSE events.

  python -m uvicorn devmate.api:app --host 127.0.0.1 --port 8000

  DEVMATE_API_TOKEN=... python -m uvicorn devmate.api:app --host 0.0.0.0 --port 8000
  # clients: Authorization: Bearer <DEVMATE_API_TOKEN>

  POST /runs {"repo": "https://github.com/owner/repo", "task": "..."}  -> {run_id}
  GET  /runs/{id}/events   # SSE stream of structured events
  GET  /runs/{id}          # current state: diff, tests before/after, review, timings
  POST /runs/{id}/approve  # apply the reviewed diff (local) or write a patch (GitHub)
  POST /runs/{id}/reject   # discard the run
  GET  /runs/{id}/patch    # download the patch file (GitHub runs, after approval)

API accepts PUBLIC GITHUB URLs ONLY (validated); local paths are rejected here —
use the CLI for local repos. One run at a time per server (429 otherwise).

Security: local use should bind 127.0.0.1. If DEVMATE_API_TOKEN is set, every
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from .github import RepoInputError
from .runner import RunBusyError, RunConflictError, RunManager, RunNotFoundError

app = FastAPI(title="DevMate Phase 2", version="0.2.0")

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
    token = os.getenv("DEVMATE_API_TOKEN")
    if token and request.url.path != "/health":
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not supplied or not hmac.compare_digest(supplied, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authorization: Bearer <DEVMATE_API_TOKEN> is required"},
            )
    return await call_next(request)

manager = RunManager()
_STREAM_CLOSE_STATUSES = {"awaiting_approval", "applied", "rejected", "failed", "error", "timed_out"}


class RunRequest(BaseModel):
    repo: str = Field(..., description="https://github.com/<owner>/<repo> (public only)")
    task: str = Field(..., min_length=1, max_length=2000)
    max_attempts: int | None = Field(default=None, ge=1, le=10)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/runs", status_code=202)
def create_run(req: RunRequest):
    try:
        record = manager.start(req.repo, req.task,
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
def stream_events(run_id: str):
    try:
        manager.events(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}") from None

    async def generate():
        emitter = manager.events(run_id)
        history, subscription = emitter.begin_stream()
        try:
            # Replay what already happened so late subscribers see the full run.
            for event in history:
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
    return f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"
