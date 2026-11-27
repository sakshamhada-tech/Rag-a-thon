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
from ..reasoning import select_reasoning_effort

_PHASE_ROLES = {"analyse": "Developer", "plan": "Developer", "fix": "Developer", "review": "Reviewer"}

_SYSTEM = """PHASE: {phase}
You are Fixora's {role} agent working on a Python repo copied into a sandbox.
Work only with the provided tools. Never invent file contents or test results.
Be terse. Paths are relative to the repo root. When your task is done, reply
with your final answer as plain text and do NOT call any more tools.
Do not re-read files you already have.
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
            "if unsure. If the failure diagnosis is dependency_missing and the repo declares "
            "the missing package, call install_dependencies FIRST, then re-check the tests. "
            "install_dependencies installs only the repo's own declared dependencies and cannot "
            "take package names; it is safe to call when unsure.",
            ["read_file", "search_code", "list_tree", "get_dependencies",
             "write_file", "apply_patch", "install_dependencies"]),
    "review": ("You are the Reviewer. Inspect changed files with read-only tools if needed. "
               "Never call tools named json, commentary, output_final or similar pseudo-tools; "
               "if no read-only tool is needed, reply directly with the JSON object. Output "
               "STRICT JSON only — no markdown, no prose outside the JSON — matching: "
               '{"verdict": "approve"|"reject", "issues": ["..."], "risk": "low"|"medium"|"high", '
               '"summary": "..."}. Check for a root-cause fix vs hack, weakened/deleted tests, '
               "unrelated changes and possible regressions. If anything is off, verdict must be reject.",
               ["list_tree", "read_file", "search_code", "get_dependencies"]),
}

READONLY = _PHASE_DETAILS["analyse"][1]


def run_developer(ctx, phase: str, state: dict, instruction: str,
                  reasoning_effort: str | None = None) -> tuple[str, list[dict]]:
    """Run a capped tool-calling loop; returns (final_text, new_messages)."""
    detail, allowed = _PHASE_DETAILS[phase]
    allowed_list = ", ".join(allowed)
    system = _SYSTEM.format(
        phase=phase,
        role=_PHASE_ROLES.get(phase, "Developer"),
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
            resp, llm_ms = ctx.timed_llm(lambda: ctx.llm.chat(new_messages, schemas,
                                                        reasoning_effort=reasoning_effort))
        except LLMError as e:
            if _is_unavailable_tool_error(e) and validation_retries < 2:
                validation_retries += 1
                correction = (
                    f"The provider rejected a tool call: {e}\n"
                    f"Only these tools exist in this phase: {allowed_list}. "
                    "Do not call any other tool — 'json', 'commentary', 'output_final' "
                    "and similar pseudo-tools are not tools. If you meant to emit JSON, "
                    "write the JSON object as your reply text. "
                    "If you meant to edit code, describe the edit instead of calling it now.")
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


def _diagnosis_block(state: dict) -> str:
    """Render the structured diagnosis for prompt context (truncated)."""
    r = state.get("last_test_result") or {}
    diag = r.get("diagnosis") or {}
    if not diag:
        return ""
    lines = ["", "", "Failure diagnosis: " + str(diag.get("label") or diag.get("category")),
             "Summary: " + str(diag.get("summary", ""))]
    if diag.get("module"):
        lines.append(f"Missing/offending module: {diag['module']}")
    if diag.get("files"):
        lines.append("Files involved: " + ", ".join(str(f) for f in diag["files"]))
    if diag.get("exceptions"):
        lines.append("Exceptions seen: " + ", ".join(str(e) for e in diag["exceptions"]))
    lines.append("Guidance: " + str(diag.get("guidance", "")))
    return "\n".join(lines)


def _test_context(state: dict) -> str:
    """Render the last test result for prompt context (truncated)."""
    r = state.get("last_test_result")
    if not r:
        return ""
    failing = ", ".join(r.get("failing_tests", [])) or "none"
    tail = r.get("output_tail", "")[-2500:]
    return (f"\n\nLast test run: {r.get('passed', 0)} passed, {r.get('failed', 0)} failed, "
            f"{r.get('errors', 0)} errors. Failing: {failing}{_diagnosis_block(state)}\n"
            f"Traceback tail:\n{tail}")


def analyse_node(ctx, state: dict) -> dict:
    instruction = (f"Task: {state['task']}\n\nExplore the repo and write a short repo_summary: "
                   "layout, entry points, dependencies, where the tests live.")
    effort = select_reasoning_effort("analyse", state)
    summary, msgs = run_developer(ctx, "analyse", state, instruction + ctx.already_read_block(),
                                  reasoning_effort=effort)
    ctx.log_step("analyse", "repo summary ready")
    return {"repo_summary": summary, "messages": state.get("messages", []) + msgs}


def plan_node(ctx, state: dict) -> dict:
    instruction = (f"Task: {state['task']}\n\nRepo summary:\n{state.get('repo_summary', '')}\n\n"
                   "State the suspected root cause and the step-by-step fix plan. Do not edit yet."
                   + ctx.already_read_block())
    effort = select_reasoning_effort("plan", state)
    plan, msgs = run_developer(ctx, "plan", state, instruction, reasoning_effort=effort)
    ctx.log_step("plan", "plan ready", plan=plan)
    return {"plan": plan, "messages": state.get("messages", []) + msgs}


def fix_node(ctx, state: dict) -> dict:
    attempts = state.get("attempts", 0) + 1
    if attempts > 1:
        ctx.printer(f"[RETRY {attempts - 1}] re-entering fix with failing output in context")
        ctx.emitter.emit("retry", attempt=attempts, retry_index=attempts - 1)
        ctx.log_step("fix", f"retry {attempts - 1}", retry_index=attempts - 1)
    ctx.stats["retries"] = max(ctx.stats.get("retries", 0), attempts - 1)
    instruction = (f"Task: {state['task']}\n\nRepo summary:\n{state.get('repo_summary', '')}\n\n"
                   f"Plan:\n{state.get('plan', '')}\n"
                   f"{_test_context(state)}\n\n"
                   f"Attempt {attempts} of {state.get('max_attempts', 3)}. "
                   "Fix the bug now. Do not modify tests."
                   + ctx.already_read_block())
    effort = select_reasoning_effort("fix", state, attempt_number=attempts)
    _, msgs = run_developer(ctx, "fix", state, instruction, reasoning_effort=effort)
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
