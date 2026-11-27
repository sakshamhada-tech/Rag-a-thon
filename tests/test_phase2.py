"""Phase 2 unit tests: aliases, caching, approval gate, GitHub validation, fallback."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from fixora.config import Settings
from fixora.github import RepoInputError, validate_github_url
from fixora.llm import FallbackLLMClient, LLMError, LLMResponse
from fixora.runner import RunManager
from fixora.sandbox import Sandbox
from fixora.tools import build_tools
from fixora.agents.common import RunContext

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_HARD = REPO_ROOT / "demo_repo_hard"


def _settings(**overrides):
    values = dict(
        llm_provider="mock",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-5",
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        xai_api_key=None,
        xai_model="grok-4-latest",
        xai_base_url="https://api.x.ai/v1",
        groq_api_key=None,
        groq_model="openai/gpt-oss-120b",
        groq_base_url="https://api.groq.com/openai/v1",
        max_attempts=3,
        command_timeout=60,
        max_tool_steps=12,
        llm_providers="",
        run_timeout=900,
        clone_timeout=120,
        max_repo_mb=50,
        read_cache_chars=24000,
    )
    values.update(overrides)
    return Settings(**values)


def _sandbox_with_settings(tmp_path, source=DEMO_HARD):
    dest = tmp_path / Path(source).name
    shutil.copytree(source, dest,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc"))
    settings = _settings()
    sandbox = Sandbox(dest)
    tools = build_tools(sandbox, settings)
    ctx = RunContext(sandbox=sandbox, settings=settings, llm=object(),
                     tools=tools, printer=lambda *a, **k: None)
    return sandbox, ctx


# ----------------------------------------------------------------- GitHub

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/owner/repo", "https://github.com/owner/repo"),
    ("https://github.com/owner/repo/", "https://github.com/owner/repo"),
    ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
    ("https://github.com/a-b/c.d-e_f", "https://github.com/a-b/c.d-e_f"),
])
def test_github_url_accepts_public_shapes(url, expected):
    assert validate_github_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo",
    "https://gitlab.com/owner/repo",
    "git@github.com:owner/repo.git",
    "file:///tmp/repo",
    "/Users/someone/repo",
    "https://github.com/owner",
    "https://github.com/owner/repo/issues",
    "https://github.com/-bad/repo",
    "https://github.com/owner/repo?tab=readme",
    "https://github.com/owner/repo with space",
])
def test_github_url_rejects_everything_else(url):
    with pytest.raises(RepoInputError):
        validate_github_url(url)


# ---------------------------------------------------------------- aliases

@pytest.mark.parametrize("start_key,end_key", [
    ("start_line", "end_line"), ("line_start", "line_end"), ("start", "end"),
])
def test_read_file_line_aliases(tmp_path, start_key, end_key):
    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        out = ctx.tools["read_file"].run(
            {"path": "ledgerline/money.py", start_key: 1, end_key: 3})
        assert "1:" in out and "2:" in out and "3:" in out
    finally:
        sandbox.cleanup()


def test_tool_argument_type_error_is_data_not_exception(tmp_path):
    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        out = json.loads(ctx.tools["read_file"].run(
            {"path": "ledgerline/money.py", "start_line": "not-an-integer"}))
        assert "error" in out and "start_line" in out["error"]
    finally:
        sandbox.cleanup()


def test_tool_missing_required_argument_is_data(tmp_path):
    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        out = json.loads(ctx.tools["apply_patch"].run({"path": "x.py"}))
        assert out["error"].startswith("apply_patch: missing required argument")
    finally:
        sandbox.cleanup()


def test_unknown_tool_is_data_not_exception(tmp_path):
    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        out = json.loads(ctx.execute_tool("test", "delete_everything", {}))
        assert out["error"] == "unknown tool: delete_everything"
    finally:
        sandbox.cleanup()


# ---------------------------------------------------------------- caching

def test_identical_tool_call_is_cached(tmp_path):
    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        args = {"pattern": "def ", "path": "."}
        first = ctx.execute_tool("test", "search_code", args)
        second = ctx.execute_tool("test", "search_code", args)
        assert first == second
        assert ctx.stats["cached_hits"] == 1
        assert ctx.stats["tool_calls"] == 1
    finally:
        sandbox.cleanup()


def test_mutation_invalidates_cache(tmp_path):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    settings = _settings()
    sandbox = Sandbox(src)
    try:
        tools = build_tools(sandbox, settings)
        ctx = RunContext(sandbox=sandbox, settings=settings, llm=object(),
                         tools=tools, printer=lambda *a, **k: None)
        ctx.execute_tool("test", "read_file", {"path": "a.py"})
        ctx.execute_tool("test", "apply_patch",
                         {"path": "a.py", "old_str": "VALUE = 1", "new_str": "VALUE = 2"})
        ctx.execute_tool("test", "read_file", {"path": "a.py"})
        assert ctx.stats["cached_hits"] == 0
        assert "VALUE = 2" in (sandbox.workspace / "a.py").read_text(encoding="utf-8")
    finally:
        sandbox.cleanup()


# ------------------------------------------------------------ approval gate

@pytest.fixture
def local_hard_copy(tmp_path):
    dest = tmp_path / "demo_repo_hard"
    shutil.copytree(DEMO_HARD, dest,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
    yield dest


def test_reject_does_not_mutate_original(monkeypatch, local_hard_copy):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDERS", "")
    monkeypatch.setenv("MAX_ATTEMPTS", "3")
    manager = RunManager()
    record = manager.run_blocking(str(local_hard_copy), "Fix the failing tests",
                                  max_attempts=3, printer=lambda *a, **k: None)
    assert record.status == "awaiting_approval"
    assert "ROUND_HALF_EVEN" in (local_hard_copy / "ledgerline" / "money.py").read_text()
    assert "if quantity > 25" in (local_hard_copy / "ledgerline" / "discounts.py").read_text()
    result = manager.reject(record.run_id)
    assert result["status"] == "rejected"
    assert "ROUND_HALF_EVEN" in (local_hard_copy / "ledgerline" / "money.py").read_text()


def test_approve_applies_only_after_approval(monkeypatch, local_hard_copy):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDERS", "")
    monkeypatch.setenv("MAX_ATTEMPTS", "3")
    manager = RunManager()
    record = manager.run_blocking(str(local_hard_copy), "Fix the failing tests",
                                  max_attempts=3, printer=lambda *a, **k: None)
    assert record.status == "awaiting_approval"
    result = manager.approve(record.run_id)
    assert result["status"] == "applied"
    assert "ROUND_HALF_UP" in (local_hard_copy / "ledgerline" / "money.py").read_text()
    assert "if quantity >= 25" in (local_hard_copy / "ledgerline" / "discounts.py").read_text()


# ------------------------------------------------------------- fallback


def test_github_approval_writes_patch_not_clone(tmp_path, monkeypatch):
    from fixora.runner import RunRecord

    monkeypatch.setattr("fixora.runner.LOGS_DIR", tmp_path)
    manager = RunManager()
    record = RunRecord(run_id="patchrun123", repo_input="https://github.com/a/b",
                       task="fix", is_github=True, max_attempts=1,
                       status="awaiting_approval")
    record.report = {"diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n"}
    manager._runs[record.run_id] = record
    manager._active = record.run_id
    result = manager.approve(record.run_id)
    assert result["status"] == "applied"
    assert Path(result["patch_file"]).read_text(encoding="utf-8") == record.report["diff"]
    assert record.status == "applied"

class _RateLimited:
    provider_label = "limited"
    supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        raise LLMError("429 rate limit exceeded")


class _Healthy:
    provider_label = "healthy"
    supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        return LLMResponse(text="ok-from-healthy", tool_calls=[])


class _BadRequest:
    provider_label = "bad-request"
    supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        raise LLMError("400 invalid request schema")


def test_fallback_switches_on_retryable_error(capsys):
    client = FallbackLLMClient([_RateLimited(), _Healthy()])
    resp = client.chat([{"role": "user", "content": "hi"}], [])
    assert resp.text == "ok-from-healthy"
    assert "[LLM FALLBACK]" in capsys.readouterr().out


def test_fallback_does_not_mask_non_retryable_errors():
    client = FallbackLLMClient([_BadRequest(), _Healthy()])
    with pytest.raises(LLMError, match="400"):
        client.chat([{"role": "user", "content": "hi"}], [])


class _PseudoToolReviewer:
    provider_label = "pseudo-tool"
    supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        if tools:
            raise LLMError("400 tool call validation failed: attempted to call tool "
                           "'json' which was not in request.tools")
        return LLMResponse(text='{"verdict":"approve","issues":[],"risk":"low",'
                                ' "summary":"minimal fix"}', tool_calls=[])


class _ToolInVerdictMode:
    provider_label = "tool-in-verdict"
    supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        if tools:
            raise LLMError("400 attempted to call tool 'commentary' which was not in request.tools")
        return LLMResponse(text='{"verdict":"approve","issues":[],"risk":"low","summary":"ok"}',
                           tool_calls=[__import__("fixora.llm", fromlist=["ToolCall"]).ToolCall(
                               id="x", name="json", arguments={})])


def test_review_verdict_only_fallback_approves_when_tools_are_hallucinated(tmp_path):
    from fixora.agents.reviewer import review_node

    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        ctx.llm = _PseudoToolReviewer()
        state = {"task": "Fix", "plan": "P", "last_test_result": {"passed": 14, "failed": 0},
                 "messages": []}
        result = review_node(ctx, state)
        assert result["review"]["verdict"] == "approve"
        assert result["review"]["fallback_used"] is True
    finally:
        sandbox.cleanup()


def test_verdict_only_mode_does_not_trust_tool_calls_for_approval(tmp_path):
    from fixora.agents.reviewer import review_node

    sandbox, ctx = _sandbox_with_settings(tmp_path)
    try:
        ctx.llm = _ToolInVerdictMode()
        state = {"task": "Fix", "plan": "P", "last_test_result": {"passed": 14, "failed": 0},
                 "messages": []}
        result = review_node(ctx, state)
        assert result["review"]["verdict"] == "reject"
        assert result["review"]["risk"] == "high"
    finally:
        sandbox.cleanup()


def test_provider_plan_redacts_explicit_keys():
    from fixora.cli import _provider_plan

    settings = _settings(llm_providers=(
        "groq1|https://api.groq.com/openai/v1|secret-key-1|openai/gpt-oss-120b,"
        "groq2|https://api.groq.com/openai/v1|secret-key-2|openai/gpt-oss-120b"))
    plan = _provider_plan(settings)
    assert plan == "groq1,groq2"
    assert "secret-key" not in plan
    assert "gpt-oss-120b" not in plan


def test_explicit_provider_entry_requires_model_without_echoing_key():
    from fixora import llm

    settings = _settings(llm_providers=
        "groq1|https://api.groq.com/openai/v1|secret-key-should-not-appear|")
    with pytest.raises(LLMError) as excinfo:
        llm.get_llm(settings)
    assert "secret-key-should-not-appear" not in str(excinfo.value)
    assert "model" in str(excinfo.value)
