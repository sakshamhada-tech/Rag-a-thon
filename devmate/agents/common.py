"""Shared run context for graph nodes: printing and the JSON step log."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..sandbox import Sandbox
from ..config import Settings


@dataclass
class RunContext:
    sandbox: Sandbox
    settings: Settings
    llm: object                      # any LLM client from devmate.llm
    tools: dict                      # name -> devmate.tools.Tool
    printer: callable = print

    steps: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def log_step(self, node: str, message: str, **data):
        entry = {"ts": time.strftime("%H:%M:%S"), "node": node,
                 "message": message, **data}
        self.steps.append(entry)

    def tool_call(self, node: str, name: str, args: dict, result: str):
        self.printer(f"[TOOL CALL {name}({_fmt_args(args)})]")
        self.steps.append({"ts": time.strftime("%H:%M:%S"), "node": node,
                           "tool": name, "args": args,
                           "result": result[:2000]})

    def execute_tool(self, node: str, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            result = f'{{"error": "unknown tool: {name}"}}'
        else:
            result = tool.run(args)
        self.tool_call(node, name, args, result)
        return result


def _fmt_args(args: dict, limit: int = 120) -> str:
    try:
        s = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
    except Exception:
        s = str(args)
    return s if len(s) <= limit else s[:limit] + "…"


def _short(v) -> str:
    s = str(v)
    return s if len(s) <= 60 else s[:60] + "…"
