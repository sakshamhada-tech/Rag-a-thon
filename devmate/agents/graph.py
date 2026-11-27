"""LangGraph state machine:

  baseline_test -> analyse -> plan -> fix -> test -> decide -+-> fix (retry)
                                                             |
                                                          report -> END

`decide` routes on the real test result: pass -> report, or fail with attempts
remaining -> fix (failing output is already in the shared messages), else report.
"""
from __future__ import annotations

import json
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from .common import RunContext
from .developer import analyse_node, plan_node, fix_node
from .tester import baseline_node, test_node


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
    g.add_node("report", lambda s: report_node(ctx, s))
    g.add_edge(START, "baseline")
    g.add_edge("baseline", "analyse")
    g.add_edge("analyse", "plan")
    g.add_edge("plan", "fix")
    g.add_edge("fix", "test")
    g.add_conditional_edges("test", decide, {"fix": "fix", "report": "report"})
    g.add_edge("report", END)
    return g.compile()


def decide(state: AgentState) -> str:
    """Conditional edge: pass -> report; fail with budget left -> fix; else report."""
    r = state.get("last_test_result") or {}
    passed_all = (r.get("status") == "passed" and r.get("failed", 0) == 0
                  and r.get("errors", 0) == 0)
    if passed_all:
        return "report"
    if state.get("attempts", 0) < state.get("max_attempts", 3):
        return "fix"
    return "report"


def report_node(ctx: RunContext, state: AgentState) -> dict:
    r = state.get("last_test_result") or {}
    passed_all = (r.get("status") == "passed" and r.get("failed", 0) == 0
                  and r.get("errors", 0) == 0)
    status = "success" if passed_all else "failed"
    diff = ctx.sandbox.diff()
    base = state.get("baseline_test_result") or {}
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
        "diff": diff,
    }
    ctx.printer("[REPORT] " + json.dumps({k: v for k, v in report.items() if k != "diff"},
                                         indent=2, default=str))
    ctx.printer("[REPORT] diff (original repo -> fixed workspace):\n" + (diff or "(no changes)"))
    ctx.log_step("report", f"status={status}", status=status)
    return {"status": status, "diff": diff, "report": report}


def initial_state(task: str, max_attempts: int) -> AgentState:
    return AgentState(task=task, attempts=0, max_attempts=max_attempts,
                      edits_made=[], messages=[], status="pending")
