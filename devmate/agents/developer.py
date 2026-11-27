"""Developer agent: tool-calling loops for the analyse, plan and fix nodes.

Each phase has a system prompt (starting with a `PHASE:` marker) and a set of
allowed tools. The loop runs until the model stops calling tools or the step
cap is hit; every call goes through the sandboxed tool registry and is logged.
"""
from __future__ import annotations

import json
import re

from ..config import MAX_OUTPUT_CHARS
from ..llm import LLMError

_SYSTEM = """PHASE: {phase}
You are DevMate's Developer agent working on a Python repo copied into a sandbox.
Work only with the provided tools. Never invent file contents or test results.
Be terse. Paths are relative to the repo root. When your task is done, reply
with your final answer as plain text and do NOT call any more tools.
{extra}"""

_PHASE_DETAILS = {
    "analyse": ("Explore the repo: list the tree, identify entry points, dependencies and "
                "where the tests live. Read only — do not modify anything. "
                "If you find the bug, do NOT patch it here; describe it in the summary.",
                ["list_tree", "read_file", "search_code", "get_dependencies"]),
    "plan": ("State the suspected root cause and a concrete fix plan. Use search/read tools "
             "to gather evidence first. Read only — do not modify anything. "
             "If you are fairly sure how to fix it, describe the exact edit instead of making it.",
             ["list_tree", "read_file", "search_code", "get_dependencies"]),
    "fix": ("Edit the code to fix the bug using write_file / apply_patch. Prefer small, exact "
            "patches with unique anchors. Do NOT touch the tests. Re-read files after editing "
            "if unsure.",
            ["read_file", "search_code", "list_tree", "get_dependencies",
             "write_file", "apply_patch"]),
}

READONLY = _PHASE_DETAILS["analyse"][1]


def run_developer(ctx, phase: str, state: dict, instruction: str) -> tuple[str, list[dict]]:
    """Run a capped tool-calling loop; returns (final_text, new_messages)."""
    detail, allowed = _PHASE_DETAILS[phase]
    allowed_list = ", ".join(allowed)
    system = _SYSTEM.format(
        phase=phase,
        extra=(f"{detail}\nAllowed tools for this phase: {allowed_list}. "
               "Do not call any other tool, even if you think it is needed later."))
    schemas = [ctx.tools[n].schema() for n in allowed]
    new_messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": instruction},
    ]
    final_text = ""
    validation_retries = 0
    for _ in range(ctx.settings.max_tool_steps):
        try:
            resp = ctx.llm.chat(new_messages, schemas)
        except LLMError as e:
            if _is_unavailable_tool_error(e) and validation_retries < 2:
                validation_retries += 1
                correction = (
                    f"The provider rejected a tool call: {e}\n"
                    f"Only these tools exist in this phase: {allowed_list}. "
                    "Do not call any other tool. If you meant to edit code, stop and "
                    "describe the edit instead of calling it now.")
                ctx.printer(f"[ERROR] {phase} tool-call rejected; retrying with correction "
                            f"({validation_retries}/2)")
                ctx.log_step(phase, "unavailable tool call rejected by provider", error=str(e))
                new_messages.append({"role": "user", "content": correction})
                continue
            # Fatal for this run (bad key/model/base URL/tool schema): stop.
            ctx.printer(f"[ERROR] {phase} failed: {e}")
            ctx.log_step(phase, f"llm error: {e}")
            raise
        assistant_msg = {"role": "assistant", "content": resp.text}
        if resp.tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in resp.tool_calls]
        new_messages.append(assistant_msg)
        if resp.text:
            ctx.printer(f"[{phase.upper()}] {resp.text.strip()[:500]}")
        if not resp.tool_calls:
            final_text = resp.text.strip()
            break
        for tc in resp.tool_calls:
            result = ctx.execute_tool(phase, tc.name, tc.arguments)
            new_messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "name": tc.name, "content": result[:MAX_OUTPUT_CHARS]})
    else:
        final_text = final_text or "[stopped: tool-step cap reached]"
        ctx.log_step(phase, final_text)
    return final_text, new_messages


def _is_unavailable_tool_error(error: Exception) -> bool:
    text = str(error).lower()
    return bool(re.search(r"(not in request\.tools|tool call validation failed|attempted to call tool|unknown tool)", text))


def _test_context(state: dict) -> str:
    """Render the last test result for prompt context (truncated)."""
    r = state.get("last_test_result")
    if not r:
        return ""
    failing = ", ".join(r.get("failing_tests", [])) or "none"
    tail = r.get("output_tail", "")[-2500:]
    return (f"\n\nLast test run: {r.get('passed', 0)} passed, {r.get('failed', 0)} failed, "
            f"{r.get('errors', 0)} errors. Failing: {failing}\nTraceback tail:\n{tail}")


def analyse_node(ctx, state: dict) -> dict:
    instruction = (f"Task: {state['task']}\n\nExplore the repo and write a short repo_summary: "
                   "layout, entry points, dependencies, where the tests live.")
    summary, msgs = run_developer(ctx, "analyse", state, instruction)
    ctx.log_step("analyse", "repo summary ready")
    return {"repo_summary": summary, "messages": state.get("messages", []) + msgs}


def plan_node(ctx, state: dict) -> dict:
    instruction = (f"Task: {state['task']}\n\nRepo summary:\n{state.get('repo_summary', '')}\n\n"
                   "State the suspected root cause and the step-by-step fix plan. Do not edit yet.")
    plan, msgs = run_developer(ctx, "plan", state, instruction)
    ctx.log_step("plan", "plan ready", plan=plan)
    return {"plan": plan, "messages": state.get("messages", []) + msgs}


def fix_node(ctx, state: dict) -> dict:
    attempts = state.get("attempts", 0) + 1
    if attempts > 1:
        ctx.printer(f"[RETRY {attempts - 1}] re-entering fix with failing output in context")
    instruction = (f"Task: {state['task']}\n\nRepo summary:\n{state.get('repo_summary', '')}\n\n"
                   f"Plan:\n{state.get('plan', '')}\n"
                   f"{_test_context(state)}\n\n"
                   f"Attempt {attempts} of {state.get('max_attempts', 3)}. "
                   "Fix the bug now. Do not modify tests.")
    _, msgs = run_developer(ctx, "fix", state, instruction)
    edits = state.get("edits_made", []) + _edits_from(msgs)
    ctx.log_step("fix", f"attempt {attempts} complete", edits=len(edits))
    return {"attempts": attempts, "edits_made": edits,
            "messages": state.get("messages", []) + msgs}


def _edits_from(messages: list[dict]) -> list[dict]:
    """Extract successful file edits from tool messages for the report."""
    edits = []
    for m in messages:
        if m.get("role") != "tool" or m.get("name") not in ("apply_patch", "write_file"):
            continue
        try:
            payload = json.loads(m["content"])
        except (ValueError, TypeError):
            continue
        if payload.get("ok"):
            edits.append({"tool": m["name"], "path": payload.get("path")})
    return edits
