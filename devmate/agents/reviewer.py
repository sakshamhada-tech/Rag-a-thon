"""Reviewer agent: gate after a passing test run.

Input: task, plan, unified diff, test results. Uses read-only tools to inspect
changed code. Output strict JSON: {"verdict", "issues", "risk", "summary"}.

Hard rule enforced in CODE (not just the prompt): any change under tests/ is
flagged, and if assertions or test functions were removed/loosened the review is
forced to reject — the agent must never weaken the suite to make it pass.
"""
from __future__ import annotations

import json
import re

from ..llm import LLMError
from ..reasoning import select_reasoning_effort
from .developer import run_developer

def review_node(ctx, state: dict) -> dict:
    diff = ctx.sandbox.diff()
    hard_issues = _tests_weakened(diff)
    if hard_issues:
        review = {
            "verdict": "reject",
            "issues": ["HARD RULE: " + issue for issue in hard_issues],
            "risk": "high",
            "summary": ("Rejected by code-enforced rule: the diff touches tests/ and removes or "
                        "loosens assertions/tests. Fix the source, not the suite."),
        }
        ctx.printer(f"[REVIEW] verdict=reject risk=high issues={len(hard_issues)} (hard rule: tests touched)")
        ctx.emitter.emit("review_verdict", **review)
        ctx.log_step("review", "hard rule reject: tests modified", issues=hard_issues)
        return {"review": review, "diff": diff,
                "messages": state.get("messages", []) + [_issue_message(review)]}

    instruction = (
        f"Task: {state['task']}\n\nPlan:\n{state.get('plan', '')}\n\n"
        f"Last test result: passed={state.get('last_test_result', {}).get('passed')} "
        f"failed={state.get('last_test_result', {}).get('failed')}\n\n"
        f"Unified diff to review:\n```diff\n{diff}\n```\n\n"
        "Review the change and answer with the strict JSON verdict. "
        "Do not re-read files you already have.")
    effort = select_reasoning_effort("review", state, diff=diff)
    try:
        text, msgs = run_developer(ctx, "review", state, instruction,
                                   reasoning_effort=effort)
    except LLMError as inspection_error:
        ctx.printer(f"[REVIEW] tool-enabled pass failed; retrying verdict-only "
                    f"(provider issue: {inspection_error})")
        ctx.log_step("review", "tool-enabled review failed; verdict-only fallback",
                     error=str(inspection_error))
        review, fallback_msgs = _review_without_tools(ctx, state, diff, inspection_error, effort)
        if review is None:
            review = {"verdict": "reject",
                      "issues": [f"reviewer unavailable: {inspection_error}"],
                      "risk": "high",
                      "summary": "Reviewer could not produce a verdict; refusing to auto-approve."}
            ctx.printer(f"[REVIEW] verdict=reject (reviewer error: {inspection_error})")
            ctx.emitter.emit("review_verdict", **review)
            return {"review": review, "diff": diff,
                    "messages": state.get("messages", []) + [_issue_message(review)]}
        ctx.printer(f"[REVIEW] verdict={review['verdict']} risk={review['risk']} "
                    f"issues={len(review['issues'])} (verdict-only)")
        ctx.emitter.emit("review_verdict", **review)
        ctx.log_step("review", f"verdict={review['verdict']} (verdict-only)",
                     verdict=review["verdict"], risk=review["risk"],
                     issues=review["issues"], fallback_used=True)
        messages = list(state.get("messages", [])) + fallback_msgs
        if review["verdict"] == "reject":
            messages.append(_issue_message(review))
        return {"review": review, "diff": diff, "messages": messages}

    review = _parse_review(text)
    ctx.printer(f"[REVIEW] verdict={review['verdict']} risk={review['risk']} "
                f"issues={len(review['issues'])}")
    ctx.emitter.emit("review_verdict", **review)
    ctx.log_step("review", f"verdict={review['verdict']}", verdict=review["verdict"],
                 risk=review["risk"], issues=review["issues"])
    messages = list(state.get("messages", [])) + msgs
    if review["verdict"] == "reject":
        messages.append(_issue_message(review))
    return {"review": review, "diff": diff, "messages": messages}


def _review_without_tools(ctx, state: dict, diff: str, inspection_error: Exception,
                          effort: str = "medium"):
    """Ask the model for the verdict only.

    Some OpenAI-compatible providers reject a reviewer turn when the model emits
    a pseudo-tool such as "json" or "commentary". The verdict does not need
    tools: the full diff, task, plan and test result are in the prompt. This is
    a verdict attempt, not an approval bypass — it still lands on the human
    approval gate.
    """
    instruction = (
        f"Task: {state['task']}\n\nPlan:\n{state.get('plan', '')}\n\n"
        f"Last test result: passed={state.get('last_test_result', {}).get('passed')} "
        f"failed={state.get('last_test_result', {}).get('failed')}\n\n"
        f"Unified diff to review:\n```diff\n{diff}\n```\n\n"
        f"A tool-enabled review pass failed with: {inspection_error}\n"
        "Answer from the diff alone. Do not call tools of any kind. Output strict "
        "JSON only, with no markdown fences and no prose outside the JSON object.")
    messages = [
        {"role": "system", "content":
         "PHASE: review\nYou are DevMate's Reviewer agent. Output STRICT JSON only — "
         "no tools, no markdown, no extra prose — matching exactly "
         '{"verdict": "approve"|"reject", "issues": ["..."], "risk": "low"|"medium"|"high", '
         '"summary": "..."}. Check for a root-cause fix vs hack, weakened/deleted tests, '
         "unrelated changes and possible regressions. If anything is off, verdict must be reject."},
        {"role": "user", "content": instruction},
    ]
    try:
        resp, _ = ctx.timed_llm(lambda: ctx.llm.chat(messages, [],
                                                     reasoning_effort=effort))
    except LLMError:
        return None, []
    assistant_msg = {"role": "assistant", "content": resp.text}
    if resp.tool_calls:
        assistant_msg["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in resp.tool_calls]
    review = _parse_review(resp.text)
    review["fallback_used"] = True
    if resp.tool_calls and review["verdict"] == "approve":
        # A model that tries tools in verdict-only mode is not trusted to approve.
        review = {**review,
                  "verdict": "reject",
                  "risk": "high",
                  "issues": ["reviewer attempted a tool during verdict-only mode"] + review["issues"],
                  "summary": "Verdict-only review produced tool calls; refusing to auto-approve."}
    return review, messages + [assistant_msg]


# ------------------------------------------------------------------- internals

def _issue_message(review: dict) -> dict:
    issues = "\n".join(f"- {i}" for i in review.get("issues", [])) or "(no details)"
    return {"role": "user", "content":
            f"The reviewer REJECTED the previous fix. Issues:\n{issues}\n"
            "Address these in your next fix. Do not weaken or delete tests."}


_ASSERT_RE = re.compile(r"(\bassert\b|def test_|pytest\.|\.raises\b|\.warns\b)")


def _tests_weakened(diff: str) -> list[str]:
    """Detect removed/loosened assertions or deleted test files in the diff."""
    issues: list[str] = []
    current_file: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ /dev/null"):
            if current_file and _is_test_path(current_file):
                issues.append(f"test file deleted: {current_file}")
            continue
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):].strip()
            continue
        if line.startswith("--- a/"):
            current_file = current_file or line[len("--- a/"):].strip()
            continue
        if current_file and _is_test_path(current_file):
            if line.startswith("-") and not line.startswith("---"):
                content = line[1:].strip()
                if _ASSERT_RE.search(content):
                    issues.append(f"removed/loosened test code in {current_file}: {content[:140]}")
    return issues


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    return ("tests" in parts or "test" in parts
            or path.startswith("test_") or "/test_" in path)


def _parse_review(text: str) -> dict:
    """Extract and validate the reviewer's strict-JSON verdict."""
    payload = None
    candidate = (text or "").strip()
    try:
        payload = json.loads(candidate)
    except ValueError:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", candidate):
            try:
                payload, _ = decoder.raw_decode(candidate[match.start():])
                break
            except ValueError:
                continue
    if not isinstance(payload, dict):
        return {"verdict": "reject",
                "issues": ["reviewer returned no valid JSON verdict"],
                "risk": "high",
                "summary": "Could not parse reviewer output; refusing to auto-approve."}
    verdict = str(payload.get("verdict", "reject")).lower().strip()
    if verdict not in {"approve", "reject"}:
        verdict = "reject"
    issues = payload.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    risk = str(payload.get("risk", "medium")).lower().strip()
    if risk == "med":
        risk = "medium"
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    return {"verdict": verdict,
            "issues": [str(i) for i in issues][:20],
            "risk": risk,
            "summary": str(payload.get("summary", ""))[:2000]}
