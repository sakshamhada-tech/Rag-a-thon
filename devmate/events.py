"""Thread-safe event emitter shared by the CLI and the FastAPI/SSE server.

Events are plain dicts: {"ts", "run_id", "type", ...payload}. The emitter keeps
the full history for /runs/{id} and fans out to subscribers without losing or
duplicating events.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid

from .security import redact_value


class EventEmitter:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.history: list[dict] = []
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def emit(self, type: str, **payload) -> dict:
        event = {"ts": time.strftime("%H:%M:%S"), "run_id": self.run_id,
                 "type": type, **redact_value(payload)}
        with self._lock:
            self.history.append(event)
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
        return event

    def begin_stream(self) -> tuple[list[dict], queue.Queue]:
        """Atomically snapshot history and register a subscriber."""
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            history = list(self.history)
            self._subscribers.append(q)
        return history, q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
