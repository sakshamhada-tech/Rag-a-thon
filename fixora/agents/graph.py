"""LangGraph state machine (Phase 2):

  baseline_test -> analyse -> plan -> fix -> test -+-> fix (retry / attempts left)
                                                   |
                                                   +-> review -> fix (rejected, attempts left)
                                                   |
                                                   +-> report -> END

`report` ends the run in `awaiting_approval` when tests pass and the reviewer
approves — NOTHING is written to the original repo until a human approves.
"""
from __future__ import annotations

import json
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from .common import RunContext
from .developer import analyse_node, plan_node, fix_node
from .tester import baseline_node, test_node
from .reviewer import review_node


class AgentState(TypedDict, total=False):
    task: str
    repo_summary: str
    plan: str
    attempts: int
    max_attempts: int
    baseline_test_result: dict
    last_test_result: dict
    edits_made: list
    diff: str
    review: dict
    status: str
    report: dict
    messages: list


def build_graph(ctx: RunContext):
    g = StateGraph(AgentState)
    g.add_node("baseline", lambda s: baseline_node(ctx, s))
    g.add_node("analyse", lambda s: analyse_node(ctx, s))
    g.add_node("plan", lambda s: plan_node(ctx, s))
    g.add_node("fix", lambda s: fix_node(ctx, s))
    g.add_node("test", lambda s: test_node(ctx, s))
    g.add_node("review", lambda s: review_node(ctx, s))
    g.add_node("report", lambda s: report_node(ctx, s))
    g.add_edge(START, "baseline")
    g.add_edge("baseline", "analyse")
    g.add_edge("analyse", "plan")
    g.add_edge("plan", "fix")
    g.add_edge("fix", "test")
    g.add_conditional_edges("test", after_test, {"fix": "fix", "review": "review", "report": "report"})
    g.add_conditional_edges("review", after_review, {"fix": "fix", "report": "report"})
    g.add_edge("report", END)
    return g.compile()


def _tests_pass(state: AgentState) -> bool:
    r = state.get("last_test_result") or {}
    return (r.get("status") == "passed" and r.get("failed", 0) == 0
            and r.get("errors", 0) == 0)


def after_test(state: AgentState) -> str:
    if _tests_pass(state):
        return "review"
    if state.get("attempts", 0) < state.get("max_attempts", 3):
        return "fix"
    return "report"


def after_review(state: AgentState) -> str:
    review = state.get("review") or {}
    if review.get("verdict") == "approve":
        return "report"
    if state.get("attempts", 0) < state.get("max_attempts", 3):
        return "fix"
    return "report"


def report_node(ctx: RunContext, state: AgentState) -> dict:
    passed = _tests_pass(state)
    review = state.get("review") or {}
    approved = review.get("verdict") == "approve"
    if passed and approved:
        status = "awaiting_approval"
    elif passed and not review:
        status = "failed"  # passed tests can't happen without review in this graph
    else:
        status = "failed"
    diff = state.get("diff") or ctx.sandbox.diff()
    base = state.get("baseline_test_result") or {}
    r = state.get("last_test_result") or {}
    report = {
        "status": status,
        "task": state.get("task"),
        "plan": state.get("plan"),
        "attempts": state.get("attempts", 0),
        "max_attempts": state.get("max_attempts", 3),
        "edits_made": state.get("edits_made", []),
        "tests_before": {k: base.get(k) for k in
                         ("status", "passed", "failed", "errors", "failing_tests")},
        "tests_after": {k: r.get(k) for k in
                        ("status", "passed", "failed", "errors", "failing_tests")},
        "baseline_diagnosis": (base.get("diagnosis") or {}).get("label") if base else None,
        "diagnosis": (r.get("diagnosis") or {}).get("label") if r else None,
        "review": review or None,
        "diff": diff,
        "timings": ctx.timings(),
    }
    ctx.printer("[REPORT] " + json.dumps({k: v for k, v in report.items() if k != "diff"},
                                         indent=2, default=str))
    ctx.printer("[REPORT] diff (original repo -> fixed workspace):\n" + (diff or "(no changes)"))
    if status == "awaiting_approval":
        ctx.printer("[AWAITING APPROVAL] tests pass and reviewer approved — the original repo "
                    "is unchanged until a human approves (CLI --yes, or API POST /runs/{id}/approve)")
        ctx.emitter.emit("awaiting_approval", diff=diff, review=review,
                         tests_after=report["tests_after"])
    else:
        ctx.printer(f"[DONE] status={status}")
        ctx.emitter.emit("done", status=status, review=review or None)
    ctx.log_step("report", f"status={status}", status=status)
    return {"status": status, "diff": diff, "report": report}


def initial_state(task: str, max_attempts: int) -> AgentState:
    return AgentState(task=task, attempts=0, max_attempts=max_attempts,
                      edits_made=[], messages=[], status="pending")
