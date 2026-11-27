"""Classify why a test run failed, from real pytest output only.

This module exists so the agent (and the human reviewing the run) can tell
*what kind* of problem it is looking at before asking the model to fix it:

  syntax_error       A file does not parse. Nothing runs until it is fixed.
  dependency_missing A module required by the code/tests is not importable.
  import_error       The code's own import is wrong (bad name/path/circular).
  collection_error   pytest failed to collect the suite for another reason.
  no_tests           The suite runs but collects zero tests (pytest exit 5).
  assertion_failure  Tests ran; assertions failed (typical logic bug).
  runtime_error      Tests ran; an exception (TypeError, KeyError, ...) was raised.
  timeout            The command hit its timeout.
  unknown            Output did not match any known signature.

The classifier never executes anything and never imports the failing code; it
only reads captured output. Anything it cannot classify safely stays "unknown".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORY_LABELS = {
    "syntax_error": "Syntax error",
    "dependency_missing": "Missing dependency",
    "import_error": "Import error",
    "collection_error": "Collection error",
    "no_tests": "No tests collected",
    "assertion_failure": "Assertion failure",
    "runtime_error": "Runtime error",
    "timeout": "Timeout",
    "unknown": "Unknown",
}

_GUIDANCE = {
    "syntax_error": (
        "A file does not parse, so pytest cannot even import the suite. "
        "Read the offending file at the reported line, fix the syntax itself "
        "(do not restructure the code), then re-run the tests."),
    "dependency_missing": (
        "A module is not importable. If it is declared in requirements.txt or "
        "pyproject.toml, call the install_dependencies tool (installs only the "
        "repo's own declared dependencies, into an isolated directory), then "
        "re-run the tests. If it is not declared, either add the exact missing "
        "dependency to requirements.txt, or fix the import/typo if the module "
        "is internal."),
    "import_error": (
        "An import failed for a reason other than a missing third-party module "
        "(wrong name, bad relative path, partial import, circular import). "
        "Read the importing module and the imported module; fix the import "
        "statement or the name mismatch itself."),
    "collection_error": (
        "pytest could not collect the suite. Look at the ERROR lines in the "
        "output: the cause is usually a crash at import/collection time. Fix "
        "whatever crashes during import, then re-run."),
    "no_tests": (
        "pytest collected zero tests (exit code 5). If a package is failing to "
        "import, fix that first. If the repo genuinely has no tests, there is "
        "no failing test to satisfy — say so in the review instead of claiming "
        "the repo is fixed."),
    "assertion_failure": (
        "Tests ran and assertions failed. Trace the failing assertion back to "
        "the production code path that produces the wrong value, fix the root "
        "cause, then re-run. Do not touch the tests or loosen assertions."),
    "runtime_error": (
        "Tests ran and raised an exception (see the exception lines in the "
        "output). Find the production code path raising it, fix the root cause "
        "(type handling, missing key, wrong contract), then re-run. Do not "
        "touch the tests."),
    "timeout": (
        "The test command hit its timeout. Reduce the workload (narrower test "
        "path, shorter fixture) rather than disabling checks, or report that "
        "the suite cannot complete in the time budget."),
    "unknown": (
        "The failure did not match a known signature. Read the output tail "
        "carefully, identify the first real error line (not the summary), and "
        "fix that. Do not touch the tests."),
}


@dataclass
class Diagnosis:
    category: str
    summary: str
    files: list[str] = field(default_factory=list)
    module: str | None = None
    exceptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "label": CATEGORY_LABELS.get(self.category, "Unknown"),
            "summary": self.summary,
            "files": self.files[:5],
            "module": self.module,
            "exceptions": self.exceptions[:6],
            "guidance": _GUIDANCE.get(self.category, _GUIDANCE["unknown"]),
        }


_SYNTAX_RE = re.compile(r"\b(SyntaxError|IndentationError|TabError)\b")
_MODULE_MISSING_RE = re.compile(
    r"ModuleNotFoundError:\s*No module named\s+['\"]([\w\.]+)['\"]")
_MODULE_FROM_RE = re.compile(
    r"ImportError:\s*cannot import name\s+['\"]?([\w]+)['\"]?\s+from\s+['\"]([\w\.]+)['\"]")
_IMPORT_OTHER_RE = re.compile(r"\bImportError\b")
_NO_TESTS_RE = re.compile(r"no tests ran|no tests collected", re.IGNORECASE)
_ASSERT_RE = re.compile(r"\bassert\b|AssertionError", re.IGNORECASE)
_EXCEPTION_RE = re.compile(r"^E\s+([\w\.]*(?:Error|Exception))\b", re.MULTILINE)
_FAILED_RE = re.compile(r"^FAILED\s+\S+", re.MULTILINE)
_ERROR_LINE_RE = re.compile(r"^(?:ERROR|ERRORS)\s+\S+", re.MULTILINE)
_COLLECTION_RE = re.compile(r"errors? during collection|Interrupted: during collection", re.IGNORECASE)
_FILE_LINE_RE = re.compile(r'File "([^"\n]+?\.py)", line (\d+)')


def classify_pytest_output(output: str, timed_out: bool = False,
                           returncode: int | None = None) -> Diagnosis:
    """Best-effort classification; never raises."""
    try:
        return _classify(output or "", timed_out, returncode)
    except Exception:
        return Diagnosis(category="unknown", summary="Could not classify the test output.")


def _classify(output: str, timed_out: bool, returncode: int | None) -> Diagnosis:
    text = output

    if timed_out:
        return Diagnosis(category="timeout", summary="The test command exceeded its time budget.")

    syntax = _SYNTAX_RE.search(text)
    if syntax:
        files = _FILE_LINE_RE.findall(text)
        file_list = [f"{p}:{n}" for p, n in files[:5]]
        first = file_list[0] if file_list else "unknown file"
        message = _first_line(text, _SYNTAX_RE) or f"{syntax.group(1)} in {first}"
        return Diagnosis(category="syntax_error",
                         summary=message,
                         files=[f for f, _ in files[:5]])

    missing = _MODULE_MISSING_RE.search(text)
    if missing:
        module = missing.group(1)
        return Diagnosis(category="dependency_missing",
                         summary=f"ModuleNotFoundError: no module named '{module}'",
                         module=module,
                         files=[p for p, _ in _FILE_LINE_RE.findall(text)[:5]])

    from_import = _MODULE_FROM_RE.search(text)
    if from_import:
        name, package = from_import.group(1), from_import.group(2)
        return Diagnosis(category="import_error",
                         summary=f"ImportError: cannot import name '{name}' from '{package}'",
                         module=package,
                         files=[p for p, _ in _FILE_LINE_RE.findall(text)[:5]])

    if _IMPORT_OTHER_RE.search(text):
        message = _first_line(text, _IMPORT_OTHER_RE) or "ImportError during collection"
        return Diagnosis(category="import_error", summary=message,
                         files=[p for p, _ in _FILE_LINE_RE.findall(text)[:5]])

    if _NO_TESTS_RE.search(text) or returncode == 5:
        return Diagnosis(category="no_tests",
                         summary="pytest collected zero tests (exit code 5).")

    collection = _COLLECTION_RE.search(text)
    if collection:
        exceptions = sorted(set(_EXCEPTION_RE.findall(text)))
        summary = "pytest failed to collect the suite."
        if exceptions:
            summary += f" Exceptions seen: {', '.join(exceptions)}"
        return Diagnosis(category="collection_error", summary=summary,
                         exceptions=exceptions,
                         files=[p for p, _ in _FILE_LINE_RE.findall(text)[:5]])

    failed_assert = _ASSERT_RE.search(text)
    has_failures = bool(_FAILED_RE.search(text))
    exceptions = sorted(set(_EXCEPTION_RE.findall(text)))
    if failed_assert and has_failures:
        return Diagnosis(category="assertion_failure",
                         summary="Assertion failures in the suite"
                                + (f" ({len(_EXCEPTION_RE.findall(text))} raised)" if exceptions else ""),
                         exceptions=exceptions)

    if exceptions:
        return Diagnosis(category="runtime_error",
                         summary=f"Exceptions raised in tests: {', '.join(exceptions)}",
                         exceptions=exceptions)

    if has_failures:
        return Diagnosis(category="runtime_error",
                         summary="Tests failed without a recognised exception signature.")

    if _ERROR_LINE_RE.search(text):
        return Diagnosis(category="collection_error",
                         summary="pytest reported collection errors.")

    return Diagnosis(category="unknown", summary="Unrecognised failure output.")


def _first_line(text: str, rx: re.Pattern[str]) -> str | None:
    for line in text.splitlines():
        if rx.search(line):
            cleaned = re.sub(r"^E\s+", "", line.strip())
            return cleaned[:200]
    return None


def guidance_for(category: str) -> str:
    return _GUIDANCE.get(category, _GUIDANCE["unknown"])
