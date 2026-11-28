"""Failure-taxonomy classifier: syntax, dependency, import, collection,
assertion, runtime, no-tests, timeout, unknown. Classification only reads
output; it never imports or executes the failing code.
"""
from __future__ import annotations

from fixora.diagnostics import classify_pytest_output, guidance_for


def test_syntax_error_with_file_and_line():
    out = ('E   File "/repo/skylark/pricing.py", line 11\n'
           'E       return f"${total:.2f}\n'
           'E              ^\n'
           'E   SyntaxError: unterminated string literal (detected at line 11)\n'
           'Interrupted: 1 error during collection\n')
    d = classify_pytest_output(out, returncode=2)
    assert d.category == "syntax_error"
    assert "pricing.py" in d.files[0]
    assert "unterminated" in d.summary
    assert guidance_for(d.category)


def test_indentation_error_is_syntax():
    out = 'E   IndentationError: expected an indented block after function definition on line 3\n'
    d = classify_pytest_output(out)
    assert d.category == "syntax_error"
    assert "IndentationError" in d.summary


def test_missing_module_reports_module_name():
    out = ('E   ModuleNotFoundError: No module named \'requests\'\n'
           'ERROR tests/test_cart.py - ModuleNotFoundError: No module named \'requests\'\n')
    d = classify_pytest_output(out, returncode=2)
    assert d.category == "dependency_missing"
    assert d.module == "requests"


def test_cannot_import_name_is_import_error():
    out = "E   ImportError: cannot import name 'stock_level' from 'meridian.inventory'\n"
    d = classify_pytest_output(out, returncode=2)
    assert d.category == "import_error"
    assert d.module == "meridian.inventory"
    assert "stock_level" in d.summary


def test_value_error_during_collection():
    out = ('________________ ERROR collecting tests/test_app.py ________________\n'
           'E   ValueError: bad config\n'
           'errors during collection\n'
           '1 error in 0.04s\n')
    d = classify_pytest_output(out, returncode=2)
    assert d.category == "collection_error"
    assert "ValueError" in d.exceptions


def test_no_tests_collected_exit_code():
    d = classify_pytest_output("", returncode=5)
    assert d.category == "no_tests"


def test_no_tests_collected_text():
    d = classify_pytest_output("================ no tests ran in 0.01s ================")
    assert d.category == "no_tests"


def test_assertion_failure():
    out = ('FAILED tests/test_cart.py::test_total - assert 9.5 == 10.0\n'
           'E   AssertionError: assert 9.5 == 10.0\n'
           '1 failed, 2 passed in 0.03s\n')
    d = classify_pytest_output(out, returncode=1)
    assert d.category == "assertion_failure"


def test_runtime_error_typeerror():
    out = ('FAILED tests/test_cart.py::test_total - TypeError: unsupported operand type(s)\n'
           'E   TypeError: unsupported operand type(s) for +: \'int\' and \'str\'\n'
           '1 failed in 0.03s\n')
    d = classify_pytest_output(out, returncode=1)
    assert d.category == "runtime_error"
    assert "TypeError" in d.exceptions


def test_timeout_flag_wins():
    d = classify_pytest_output("anything", timed_out=True)
    assert d.category == "timeout"


def test_unknown_output():
    d = classify_pytest_output("some unrecognised output", returncode=1)
    assert d.category == "unknown"


def test_classifier_never_raises():
    d = classify_pytest_output(None)  # type: ignore[arg-type]
    assert d.category in {"unknown", "no_tests"}


def test_to_dict_shape():
    d = classify_pytest_output(
        "E   ModuleNotFoundError: No module named 'six'\n").to_dict()
    for key in ("category", "label", "summary", "files", "module", "exceptions", "guidance"):
        assert key in d
    assert d["label"] == "Missing dependency"


def test_classify_pytest_plugin_args_as_dependency_missing():
    from fixora.diagnostics import classify_pytest_output
    output = ('ERROR: usage: python -m pytest [options] [file_or_dir]\n'
              'python -m pytest: error: unrecognized arguments: --asyncio-mode=strict\n'
              '  inifile: /tmp/repo/pyproject.toml\n')
    d = classify_pytest_output(output)
    assert d.category == "dependency_missing"
    assert "pytest-asyncio" in d.summary
