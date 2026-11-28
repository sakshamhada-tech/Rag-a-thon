"""Provider failover and checkpoint-resume: the agentic workflow must not
start from the beginning when one API key hits its limit.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

import fixora.runner as runner_module
from fixora.llm import FallbackLLMClient, LLMError, MockClient, LLMResponse, ToolCall
from fixora.runner import ACTIVE, RunManager

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "demo_repo_import"   # meridian: mock client can repair this


def _copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "meridian"
    shutil.copytree(FIXTURE, dest,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".venv"))
    return dest


def _noop_printer(*args, **kwargs):
    return None


def _wait_for_terminal(record, timeout: float = 240.0):
    deadline = time.time() + timeout
    while record.status in ACTIVE:
        assert time.time() < deadline, "run did not finish in time"
        time.sleep(0.05)
    return record


class RateLimitedMock:
    """MockClient whose first `fail_times` calls always raise a retryable 429,
    then works — or fails forever when fail_times is huge."""

    def __init__(self, fail_times: int = 0, label: str = "limited"):
        self.inner = MockClient()
        self.fail_times = fail_times
        self.provider_label = label
        self.supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise LLMError(f"Error code: 429 - rate limit reached for model openai/gpt-oss-120b ({self.provider_label})")
        return self.inner.chat(messages, tools, reasoning_effort=reasoning_effort)


@pytest.fixture(autouse=True)
def _no_memory_writes(monkeypatch):
    monkeypatch.setattr(runner_module.memory, "save_run", lambda *a, **k: None)


def _count_events(record, type_, node=None):
    events = record.emitter.history
    return sum(1 for e in events if e.get("type") == type_ and (node is None or e.get("node") == node))


def test_mid_run_provider_switch_does_not_restart_workflow(monkeypatch, tmp_path):
    repo = _copy_fixture(tmp_path)
    failing = RateLimitedMock(fail_times=10**9, label="groq-a")   # this key is exhausted
    working = MockClient()                                        # second key serves the run
    monkeypatch.setattr(runner_module, "get_llm",
                        lambda settings: FallbackLLMClient([failing, working]))
    manager = RunManager()
    record = manager.start(str(repo), "Fix the failing tests", printer=_noop_printer)
    record = _wait_for_terminal(record)

    assert record.status == "awaiting_approval"
    # The workflow ran exactly once (control with the mock client emits 3
    # test_result events for the meridian fixture: baseline + 2 fix attempts).
    assert _count_events(record, "test_result") == 3
    # And the failover actually happened (logged for the operator).
    assert any("Switching to next API key" in str(step.get("message", ""))
               for step in getattr(record.ctx, "steps", []))


def test_resume_after_provider_failure_keeps_completed_phases(monkeypatch, tmp_path):
    repo = _copy_fixture(tmp_path)
    failing = RateLimitedMock(fail_times=10**9, label="groq-a")
    monkeypatch.setattr(runner_module, "get_llm", lambda settings: failing)
    manager = RunManager()
    record = manager.start(str(repo), "Fix the failing tests", printer=_noop_printer)
    record = _wait_for_terminal(record)

    assert record.status == "error"
    assert record.error                     # friendly, dashboard-safe string
    assert record.resumable is True         # checkpoint + sandbox kept
    assert _count_events(record, "test_result") == 1   # only the baseline ran

    # Quota comes back / new key dropped into .env: resume instead of restart.
    failing.fail_times = 0
    resumed = manager.resume(record.run_id)
    record = _wait_for_terminal(resumed)

    assert record.status == "awaiting_approval"
    # Baseline did NOT re-run: count matches a single full pass (3), a restart
    # from the beginning would have produced 4 (baseline ran twice).
    assert _count_events(record, "test_result") == 3
    assert _count_events(record, "start") == 1        # no second run started


def test_resume_rejected_for_non_failed_runs(monkeypatch, tmp_path):
    repo = _copy_fixture(tmp_path)
    working = MockClient()
    monkeypatch.setattr(runner_module, "get_llm", lambda settings: working)
    manager = RunManager()
    record = manager.start(str(repo), "Fix the failing tests", printer=_noop_printer)
    record = _wait_for_terminal(record)
    assert record.status == "awaiting_approval"
    with pytest.raises(runner_module.RunConflictError):
        manager.resume(record.run_id)


def test_resume_after_failure_in_later_phase(monkeypatch, tmp_path):
    repo = _copy_fixture(tmp_path)
    working = MockClient()
    state = {"starve_fix": True}
    original_chat = working.chat

    def chat(messages, tools, reasoning_effort=None):
        is_fix = any("PHASE: fix" in str(m.get("content", ""))
                     for m in messages if m.get("role") == "system")
        if is_fix and state["starve_fix"]:
            raise LLMError("Error code: 429 - rate limit reached for model openai/gpt-oss-120b (fix)")
        return original_chat(messages, tools, reasoning_effort=reasoning_effort)

    working.chat = chat
    monkeypatch.setattr(runner_module, "get_llm", lambda settings: working)
    manager = RunManager()
    record = manager.start(str(repo), "Fix the failing tests", printer=_noop_printer)
    record = _wait_for_terminal(record)

    assert record.status == "error"          # died at fix, after analyse and plan
    assert record.resumable is True
    assert _count_events(record, "test_result") == 1   # only baseline so far

    state["starve_fix"] = False              # quota restored
    resumed = manager.resume(record.run_id)
    record = _wait_for_terminal(resumed)

    assert record.status == "awaiting_approval"
    assert _count_events(record, "start") == 1          # no second run
    assert _count_events(record, "test_result") == 3    # baseline not re-run


def test_resume_rebuilds_llm_chain_from_fresh_env(monkeypatch, tmp_path):
    """Resume must pick up keys edited in .env since the failure — the fresh
    chain is built from fresh settings, not the exhausted one."""
    repo = _copy_fixture(tmp_path)
    built = []

    def fake_get_llm(settings):
        client = RateLimitedMock(fail_times=10**9, label=f"gen{len(built)}") if not built else MockClient()
        built.append(client)
        return client

    monkeypatch.setattr(runner_module, "get_llm", fake_get_llm)
    manager = RunManager()
    record = manager.start(str(repo), "Fix the failing tests", printer=_noop_printer)
    record = _wait_for_terminal(record)
    assert record.status == "error"
    assert len(built) == 1

    resumed = manager.resume(record.run_id)
    record = _wait_for_terminal(resumed)
    assert record.status == "awaiting_approval"
    assert len(built) == 2                      # chain rebuilt at resume
    assert record.ctx.llm is built[1]           # fresh client in use


class NeverEndingFix:
    """Delegates to MockClient, except the fix phase requests tools forever
    (cap_starve=True) until told to stop — simulating a model that burns its
    tool-step budget without converging."""

    provider_label = "never-ending-fix"
    supports_reasoning = False

    def __init__(self):
        self.inner = MockClient()
        self.cap_starve = True
        self.calls = 0

    def chat(self, messages, tools, reasoning_effort=None):
        is_fix = any("PHASE: fix" in str(m.get("content", ""))
                     for m in messages if m.get("role") == "system")
        if is_fix and self.cap_starve:
            self.calls += 1
            return LLMResponse(text="", tool_calls=[
                ToolCall(id=f"cap-{self.calls}", name="read_file",
                         arguments={"path": "README.md"})])
        return self.inner.chat(messages, tools, reasoning_effort=reasoning_effort)


def test_resume_after_fix_tool_step_cap_keeps_completed_work(monkeypatch, tmp_path):
    """A fix phase that exhausts its tool-step budget must stop the run in the
    resumable 'error' state — NOT silently truncate and NOT force a restart
    from the beginning. Resume re-runs only the fix node with a fresh budget.
    """
    repo = _copy_fixture(tmp_path)
    client = NeverEndingFix()
    monkeypatch.setattr(runner_module, "get_llm", lambda settings: client)
    manager = RunManager()
    record = manager.start(str(repo), "Fix the failing tests", printer=_noop_printer)
    record = _wait_for_terminal(record)

    assert record.status == "error"
    assert record.resumable is True
    assert client.calls >= 1
    assert _count_events(record, "test_result") == 1   # baseline only

    client.cap_starve = False                        # the model converges now
    resumed = manager.resume(record.run_id)
    record = _wait_for_terminal(resumed)

    assert record.status == "awaiting_approval"
    assert _count_events(record, "start") == 1       # no restart
    assert _count_events(record, "test_result") == 3  # baseline not re-run
    assert client.calls >= 2                          # fix actually resumed
