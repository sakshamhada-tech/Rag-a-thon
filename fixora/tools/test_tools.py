"""Test runner tool: run_tests. The result is derived from real pytest output,
never from an LLM's claim about the tests.
"""
from __future__ import annotations

import json
import re

from ..config import MAX_OUTPUT_CHARS


def run_tests(sandbox, settings, test_path: str | None = None) -> str:
    argv = ["python", "-m", "pytest", "--tb=short", "-rf", "-q"]
    if test_path:
        argv.append(test_path)
    try:
        result = sandbox.run(argv, timeout=settings.command_timeout)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e),
                           "passed": 0, "failed": 0, "errors": 0,
                           "failing_tests": [], "output_tail": ""})
    out = result.combined
    counts = _parse_counts(out)
    failing = _parse_failing(out)
    status = "timeout" if result.timed_out else ("passed" if result.returncode == 0 else "failed")
    return json.dumps({
        "status": status,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "errors": counts.get("error", 0),
        "failing_tests": failing,
        "output_tail": out[-MAX_OUTPUT_CHARS:],
        "timed_out": result.timed_out,
    })


def _parse_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    # e.g. "2 failed, 7 passed, 1 error in 0.42s"
    summary = re.search(r"=+\s*([^=]+)\s*in\s+[\d.]+s", output) or re.search(
        r"((?:\d+ \w+(?:, )?)+)\s+in\s+[\d.]+s", output)
    text = summary.group(1) if summary else output[-500:]
    for n, word in re.findall(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)", text):
        key = "error" if word.startswith("error") else word
        counts[key] = int(n)
    return counts


def _parse_failing(output: str) -> list[str]:
    names = re.findall(r"^FAILED\s+(\S+)", output, re.MULTILINE)
    # Fallback: harvest "___ test_name ___" section headers
    if not names:
        names = [m.group(1).strip() for m in re.finditer(r"_{3,}\s+([\w\[\]\.:-]+)\s+_{3,}", output)]
    return names[:20]
