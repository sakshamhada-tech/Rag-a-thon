"""Shared run context for graph nodes: printing, events, caching, timings."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..sandbox import Sandbox
from ..config import Settings
from ..events import EventEmitter
from ..security import redact_text, redact_value

_MUTATING_TOOLS = {"write_file", "apply_patch"}
_READ_CACHE_MAX = 24000  # chars of already-read file contents to replay into prompts


@dataclass
class RunContext:
    sandbox: Sandbox
    settings: Settings
    llm: object                      # any LLM client from devmate.llm
    tools: dict                      # name -> devmate.tools.Tool
    printer: callable = print
    emitter: EventEmitter = field(default_factory=EventEmitter)

    steps: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    read_files: dict[str, str] = field(default_factory=dict)   # path -> content (truncated)
    stats: dict = field(default_factory=lambda: {
        "llm_calls": 0, "llm_failures": 0, "tool_calls": 0,
        "cached_hits": 0, "retries": 0})
    _cache: dict = field(default_factory=dict, init=False)
    _cache_generation: int = field(default=0, init=False)

    # ------------------------------------------------------------------ log
    def log_step(self, node: str, message: str, **data):
        entry = {"ts": time.strftime("%H:%M:%S"), "node": node,
                 "message": redact_text(message), **redact_value(data)}
        self.steps.append(entry)
        self.emitter.emit("step", node=node, message=message, **data)

    def tool_call(self, node: str, name: str, args: dict, result: str,
                  duration_ms: float = 0.0, cached: bool = False):
        result = redact_text(result)
        args = redact_value(args)
        tag = (f"[CACHED {name}({_fmt_args(args)})]" if cached
               else f"[TOOL CALL {name}({_fmt_args(args)})]")
        self.printer(tag)
        self.steps.append({"ts": time.strftime("%H:%M:%S"), "node": node,
                           "tool": name, "args": args, "cached": cached,
                           "duration_ms": round(duration_ms, 1),
                           "result": result[:2000]})
        self.emitter.emit("tool_call", node=node, tool=name, args=args, cached=cached)
        self.emitter.emit("tool_result", node=node, tool=name, cached=cached,
                          duration_ms=round(duration_ms, 1), result=result[:2000])

    def timed_llm(self, fn):
        """Run an LLM call, recording duration/failures into stats and events."""
        t0 = time.perf_counter()
        try:
            resp = fn()
        except Exception:
            self.stats["llm_failures"] += 1
            self.emitter.emit("llm_error", duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            raise
        dt = (time.perf_counter() - t0) * 1000
        self.stats["llm_calls"] += 1
        self.emitter.emit("llm_call", duration_ms=round(dt, 1))
        return resp, dt

    # --------------------------------------------------------------- tools
    def execute_tool(self, node: str, name: str, args: dict) -> str:
        args = dict(args or {})
        cache_key = (self._cache_generation, name,
                     json.dumps(args, sort_keys=True, default=str))
        if cache_key in self._cache:
            self.stats["cached_hits"] += 1
            result = self._cache[cache_key]
            self.tool_call(node, name, args, result, duration_ms=0.0, cached=True)
            return result

        tool = self.tools.get(name)
        dt = 0.0
        if tool is None:
            result = json.dumps({"error": f"unknown tool: {name}"})
        else:
            t0 = time.perf_counter()
            result = redact_text(tool.run(args))
            dt = (time.perf_counter() - t0) * 1000
        self.stats["tool_calls"] += 1
        if name in _MUTATING_TOOLS:
            try:
                ok = json.loads(result).get("ok") is True
            except (ValueError, AttributeError):
                ok = False
            if ok:
                self._cache_generation += 1   # invalidate reads/tests after a change
                self.read_files.pop(str(args.get("path", "")), None)
        self._cache[cache_key] = result
        self.tool_call(node, name, args, result, duration_ms=dt)
        if name == "read_file" and not result.startswith('{"error"'):
            self._remember_read(args, result)
        return result

    def _remember_read(self, args: dict, result: str):
        path = str(args.get("path", ""))
        if not path or path in self.read_files:
            return
        total = sum(len(v) for v in self.read_files.values())
        content = result[:4000]
        if total + len(content) > min(self.settings.read_cache_chars, _READ_CACHE_MAX):
            return
        self.read_files[path] = content

    def already_read_block(self) -> str:
        if not self.read_files:
            return ""
        parts = [f"--- {p} ---\n{c}" for p, c in self.read_files.items()]
        return "\n\nFiles already read (do NOT re-read them):\n" + "\n\n".join(parts)

    def timings(self) -> dict:
        return {"total_s": round(time.time() - self.started_at, 2), **self.stats}


def _fmt_args(args: dict, limit: int = 120) -> str:
    try:
        s = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
    except Exception:
        s = str(args)
    return s if len(s) <= limit else s[:limit] + "…"


def _short(v) -> str:
    s = str(v)
    return s if len(s) <= 60 else s[:60] + "…"
