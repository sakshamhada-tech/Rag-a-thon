"""Deterministic reasoning-effort selection for LLM calls.

The heuristic is intentionally explainable and static (no extra LLM call just
to pick effort). It considers the task text, failing-test count, retry number
and diff size. Providers that do not support reasoning_effort ignore it.

Levels map to OpenAI-style values: low | medium | high.

FIXORA_REASONING_EFFORT forces a single level for every call (useful for
smoke-testing or latency-sensitive deployments); values are case-insensitive
and invalid values are ignored.
"""
from __future__ import annotations

import os
import re

_FORCED = (os.environ.get("FIXORA_REASONING_EFFORT") or "").strip().lower()

_SIMPLE_RE = re.compile(
    r"\b(typo|rename|comment|format|formatting|lint|docs?|readme|spelling|wording|"
    r"dead[- ]code|version bump|bump version|cleanup)\b",
    re.IGNORECASE)

_COMPLEX_RE = re.compile(
    r"\b(architecture|architectural|refactor|rewrite|migrate|migration|distributed|"
    r"concurrent\w*|concurrency|race|deadlock|locking|performance|security|"
    r"vulnerability|auth(?:entication|orization)?|schema|transaction|payment|"
    r"invoice|billing|compatib\w*|legacy|flaky|regression|cache\w*|consensus|"
    r"root cause)\b",
    re.IGNORECASE)

_FILE_RE = re.compile(
    r"(?:^|\s)[\w./-]+\.(?:py|pyi|js|jsx|ts|tsx|java|go|rs|toml|yaml|yml|json|md)\b",
    re.IGNORECASE)

_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def complexity_score(task: str | None, state: dict | None = None,
                     diff: str | None = None, *, attempt_number: int | None = None) -> int:
    state = state or {}
    task = task if task is not None else state.get("task", "")
    score = 0

    if _SIMPLE_RE.search(task or ""):
        score -= 2

    complex_hits = len(_COMPLEX_RE.findall(task or ""))
    score += min(complex_hits * 2, 6)

    words = len((task or "").split())
    if words >= 160:
        score += 2
    elif words >= 60:
        score += 1

    if len(_FILE_RE.findall(task or "")) > 3:
        score += 1

    last_tests = state.get("last_test_result") or {}
    failing = len(last_tests.get("failing_tests", []) or [])
    errors = int(last_tests.get("errors", 0) or 0)
    if failing >= 8:
        score += 2
    elif failing >= 4:
        score += 1
    if errors:
        score += 1

    if diff:
        changed_files = len(set(_DIFF_FILE_RE.findall(diff)))
        changed_lines = sum(
            1 for line in diff.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )
        if changed_files >= 10:
            score += 2
        elif changed_files >= 5:
            score += 1
        if changed_lines >= 500:
            score += 2
        elif changed_lines >= 200:
            score += 1

    return score


def select_reasoning_effort(phase: str, state: dict | None = None,
                            *, task: str | None = None, diff: str | None = None,
                            attempt_number: int | None = None) -> str:
    """Pick low/medium/high for a phase from observable task complexity."""
    if _FORCED in ("low", "medium", "high"):
        return _FORCED
    state = state or {}
    attempt_number = int(attempt_number if attempt_number is not None
                         else state.get("attempts", 0))
    retries = max(0, attempt_number - 1)

    bias = {"analyse": -1, "plan": 0, "fix": 0, "review": 1}.get(phase, 0)
    score = complexity_score(task, state, diff, attempt_number=attempt_number) + bias
    effort = "low" if score <= 0 else "medium" if score <= 2 else "high"

    # A failed previous attempt/reviewer rejection is evidence the operation is
    # at least one level harder than the task description suggested.
    if retries > 0:
        effort = _bump(effort)
    return effort


def _bump(level: str) -> str:
    return {"low": "medium", "medium": "high", "high": "high"}[level]
