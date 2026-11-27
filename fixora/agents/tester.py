"""Tester node: runs the test suite via the run_tests tool and records the
real result. Never trusts an LLM's claim about whether tests pass.
"""
from __future__ import annotations

import json


def baseline_node(ctx, state: dict) -> dict:
    """Run the suite once BEFORE any edits to record the baseline failure."""
    ctx.printer("[TEST] baseline run (before any edits)")
    return test_node(ctx, {**state, "baseline_test_result": None})


def test_node(ctx, state: dict) -> dict:
    result_json = ctx.execute_tool("test", "run_tests", {})
    ctx.log_step("test", "run_tests executed", result=result_json[:1500])
    try:
        result = json.loads(result_json)
    except ValueError:
        result = {"status": "error", "passed": 0, "failed": 0, "errors": 0,
                  "failing_tests": [], "output_tail": result_json}
    p, f_, e = result.get("passed", 0), result.get("failed", 0), result.get("errors", 0)
    ctx.printer(f"[TEST] status={result.get('status')} passed={p} failed={f_} errors={e}"
                + (f" failing={', '.join(result.get('failing_tests', []))}" if f_ or e else ""))
    ctx.emitter.emit("test_result", **{k: result.get(k) for k in
                                       ("status", "passed", "failed", "errors", "failing_tests")})
    diagnosis = result.get("diagnosis") or {}
    if diagnosis:
        ctx.printer(f"[DIAGNOSIS] {diagnosis.get('label', diagnosis.get('category'))} — "
                    f"{diagnosis.get('summary', '')}")
        ctx.emitter.emit("diagnosis", node="test", **{k: diagnosis.get(k) for k in
                                                     ("category", "label", "summary", "module", "exceptions")})
    if not state.get("baseline_test_result"):
        return {"last_test_result": result, "baseline_test_result": result}
    return {"last_test_result": result}
