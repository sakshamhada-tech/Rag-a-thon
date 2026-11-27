"""DevMate CLI entry point.

  python -m devmate.cli --repo ./demo_repo --task "Find and fix the failing tests"

Prints each step live ([ANALYSE] [PLAN] [TOOL CALL ...] [TEST] [RETRY n] [REPORT]),
saves a full JSON run log to logs/<timestamp>.json, exits 0 if tests pass else 1.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import load_settings
from .llm import get_llm, LLMError
from .sandbox import Sandbox, SandboxError
from .tools import build_tools
from .agents.common import RunContext
from .agents.graph import build_graph, initial_state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DevMate Phase 1 — autonomous fix agent")
    parser.add_argument("--repo", required=True, help="Path to the target Python repo")
    parser.add_argument("--task", required=True, help="What the agent should do")
    parser.add_argument("--max-attempts", type=int, default=None,
                        help="Override MAX_ATTEMPTS from .env (default 3)")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.max_attempts:
        settings.max_attempts = args.max_attempts

    try:
        sandbox = Sandbox(args.repo)
    except SandboxError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    ctx = None
    report: dict = {}
    exit_code = 1
    try:
        try:
            llm = get_llm(settings)
        except LLMError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            report, exit_code = {"status": "error", "error": str(e)}, 2
            llm = None
        if llm is not None:
            tools = build_tools(sandbox, settings)
            ctx = RunContext(sandbox=sandbox, settings=settings, llm=llm, tools=tools)

            print(f"[START] provider={settings.llm_provider} repo={args.repo} "
                  f"workspace={sandbox.workspace} max_attempts={settings.max_attempts}")
            graph = build_graph(ctx)
            final = graph.invoke(initial_state(args.task, settings.max_attempts))
            report = final.get("report", {})
            exit_code = 0 if final.get("status") == "success" else 1
    except KeyboardInterrupt:
        print("\n[ABORTED] interrupted by user", file=sys.stderr)
        report, exit_code = {"status": "interrupted"}, 130
    except Exception as e:  # never die without writing a log
        print(f"[ERROR] agent loop crashed: {e}", file=sys.stderr)
        report, exit_code = {"status": "error", "error": str(e)}, 1
    finally:
        log_path = _save_log(ctx, report, sandbox)
        print(f"[LOG] run log saved to {log_path}")
        print(f"[DONE] exit={exit_code}")

    return exit_code


def _save_log(ctx: RunContext | None, report: dict, sandbox: Sandbox) -> str:
    logs_dir = Path(__file__).resolve().parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = logs_dir / f"{stamp}.json"
    payload = {
        "task_completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "workspace": str(sandbox.workspace),
        "steps": ctx.steps if ctx else [],
        "report": report or {},
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    sys.exit(main())
