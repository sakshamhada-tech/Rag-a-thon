"""Reasoning-effort selection tests."""
from devmate.reasoning import complexity_score, select_reasoning_effort


def _state_with_failures(*names, errors=0):
    return {"last_test_result": {"failing_tests": list(names), "errors": errors},
            "attempts": 0}


def test_simple_task_uses_low_effort():
    state = {"task": "Fix typo in README", "attempts": 0}
    assert select_reasoning_effort("analyse", state) == "low"
    assert select_reasoning_effort("plan", state) == "low"
    assert select_reasoning_effort("fix", state, attempt_number=1) == "low"
    assert select_reasoning_effort("review", state, diff="") == "low"


def test_complex_operation_uses_high_effort():
    task = ("Refactor the distributed transaction pipeline: concurrent writers, "
            "schema migration, deadlock-safe locking, and performance regression checks "
            "across the payment and invoice services.")
    state = {"task": task, "attempts": 0}
    assert complexity_score(task, state) >= 6
    assert select_reasoning_effort("plan", state) == "high"
    assert select_reasoning_effort("fix", state, attempt_number=1) == "high"
    assert select_reasoning_effort("review", state, diff="") == "high"


def test_failing_tests_make_operation_moderate():
    names = [f"tests/test_x.py::test_{i}" for i in range(5)]
    state = _state_with_failures(*names)
    state["task"] = "Fix the failing tests"
    assert select_reasoning_effort("plan", state) == "medium"
    assert select_reasoning_effort("fix", state, attempt_number=1) == "medium"


def test_failed_attempt_escalates_one_level():
    state = _state_with_failures(*(f"tests/test_x.py::test_{i}" for i in range(5)))
    state["task"] = "Fix the failing tests"
    assert select_reasoning_effort("fix", state, attempt_number=2) == "high"

    simple = {"task": "Fix typo in README", "attempts": 0,
              "last_test_result": {"failing_tests": [], "errors": 0}}
    assert select_reasoning_effort("fix", simple, attempt_number=2) == "medium"


def test_large_diff_scores_higher_for_review():
    diff = "diff --git a/a.py b/a.py\n" + "".join(f"+line {i}\n" for i in range(220))
    for name in [f"b/file{i}.py" for i in range(6)]:
        diff += f"diff --git a/{name[2:]} {name}\n+++ {name}\n"
    state = {"task": "Fix the bug", "attempts": 1}
    effort = select_reasoning_effort("review", state, diff=diff)
    assert effort == "high"
    assert complexity_score("Fix the bug", state, diff) >= 2
