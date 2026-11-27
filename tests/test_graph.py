"""Unit tests for the graph's routing functions and pytest output parsing."""
from devmate.agents.graph import after_test, after_review
from devmate.tools.test_tools import _parse_counts, _parse_failing

SAMPLE = """..........FFF.                                                           [100%]
=================================== FAILURES ===================================
___ test_discount_at_ten_items_boundary ___
tests/test_discounts.py:20: in test_discount_at_ten_items_boundary
E   AssertionError: assert Decimal('0.99') == Decimal('1.00')
=========================== short test summary info ============================
FAILED tests/test_discounts.py::test_discount_at_ten_items_boundary - Asserti...
FAILED tests/test_discounts.py::test_discount_rounds_half_up_on_odd_cents - A...
3 failed, 11 passed in 0.06s
"""


def _state(passed=0, failed=0, errors=0, status="failed", attempts=1, max_attempts=3):
    return {"last_test_result": {"status": status, "passed": passed,
                                 "failed": failed, "errors": errors},
            "attempts": attempts, "max_attempts": max_attempts}


def test_after_test_passes_to_review():
    assert after_test(_state(passed=14, failed=0, status="passed", attempts=1)) == "review"


def test_after_test_retries_fix_when_budget_remains():
    assert after_test(_state(failed=3, attempts=1, max_attempts=3)) == "fix"
    assert after_test(_state(failed=3, attempts=2, max_attempts=3)) == "fix"


def test_after_test_reports_when_budget_exhausted():
    assert after_test(_state(failed=3, attempts=3, max_attempts=3)) == "report"


def test_after_test_errors_count_as_failure():
    assert after_test(_state(passed=10, failed=0, errors=1, attempts=1, max_attempts=3)) == "fix"
    assert after_test(_state(passed=10, failed=0, errors=1, attempts=3, max_attempts=3)) == "report"


def test_after_review_approval_reports():
    state = {"review": {"verdict": "approve"}, "attempts": 1, "max_attempts": 3}
    assert after_review(state) == "report"


def test_after_review_rejection_retries_then_reports():
    assert after_review({"review": {"verdict": "reject"}, "attempts": 1,
                         "max_attempts": 3}) == "fix"
    assert after_review({"review": {"verdict": "reject"}, "attempts": 3,
                         "max_attempts": 3}) == "report"


def test_parse_counts_from_pytest_q_output():
    counts = _parse_counts(SAMPLE)
    assert counts["failed"] == 3
    assert counts["passed"] == 11


def test_parse_failing_names_from_summary():
    names = _parse_failing(SAMPLE)
    assert "tests/test_discounts.py::test_discount_at_ten_items_boundary" in names
    assert len(names) == 2
