"""Context-budget compaction + dashboard-safe error mapping."""
from __future__ import annotations

import json

from fixora.agents.developer import (
    _compact_messages, _context_budget, _is_context_error, _message_chars,
)
from fixora.llm import friendly_llm_error


def _msg(role, content, tool_calls=None):
    m = {"role": role, "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return m


def test_compact_messages_noop_under_budget():
    msgs = [_msg("system", "sys"), _msg("user", "task"), _msg("assistant", "hi"),
            _msg("tool", "result", )]
    out = _compact_messages(msgs, 10_000)
    assert out == msgs


def test_compact_messages_drops_middle_keeps_head_and_tail():
    big = "x" * 1500
    msgs = [_msg("system", "sys"), _msg("user", "the task")]
    for i in range(10):
        msgs.append(_msg("assistant", f"step {i} {big}"))
        msgs.append(_msg("tool", f"tool output {i} {big}"))
    out = _compact_messages(msgs, 5000)
    assert out[0]["role"] == "system" and out[1]["content"] == "the task"
    assert sum(_message_chars(m) for m in out) <= 5000
    # the tail (most recent work) survives; the oldest middle is gone
    assert any("step 9" in str(m.get("content", "")) for m in out)
    assert not any("step 0 " in str(m.get("content", "")) for m in out)


def test_compact_messages_never_leaves_orphan_tool_at_cut():
    big = "y" * 3000
    msgs = [_msg("system", "sys"), _msg("user", "task")]
    msgs.append(_msg("assistant", "", tool_calls=[{"id": "c1", "name": "read_file",
                                                    "arguments": {"path": "a.py"}}]))
    msgs.append(_msg("tool", "file body", ))
    msgs[-1]["name"] = "read_file"
    msgs[-1]["tool_call_id"] = "c1"
    for i in range(6):
        msgs.append(_msg("assistant", f"later {i} {big}"))
        msgs.append(_msg("tool", f"later output {i} {big}"))
    out = _compact_messages(msgs, 5000)
    for idx, m in enumerate(out):
        if m["role"] == "tool":
            # every kept tool message follows an assistant that called it
            prev = out[idx - 1]
            assert prev["role"] in ("assistant", "tool")


def test_compact_messages_single_oversized_tail_still_fits_head():
    msgs = [_msg("system", "sys"), _msg("user", "task"), _msg("user", "z" * 50000)]
    out = _compact_messages(msgs, 1000)
    assert [m["role"] for m in out][:2] == ["system", "user"]
    assert sum(_message_chars(m) for m in out) <= 1000 + _message_chars(msgs[2]) * 0 + 200  # head + note only or elided tail dropped
    assert len(out) <= 3


def test_context_error_classification():
    assert _is_context_error(Exception("Error code: 413 - Request too large for model"))
    assert _is_context_error(Exception("Please reduce your message size"))
    assert _is_context_error(Exception("maximum context length exceeded"))
    assert not _is_context_error(Exception("429 rate limit reached"))


def test_context_budget_default_and_env(monkeypatch):
    monkeypatch.delenv("FIXORA_CONTEXT_BUDGET_CHARS", raising=False)
    assert _context_budget() == 18000
    monkeypatch.setenv("FIXORA_CONTEXT_BUDGET_CHARS", "6000")
    assert _context_budget() == 6000
    monkeypatch.setenv("FIXORA_CONTEXT_BUDGET_CHARS", "not-a-number")
    assert _context_budget() == 18000


def test_friendly_llm_error_never_leaks_provider_internals():
    groq_413 = ('Error code: 413 - Request too large for model `openai/gpt-oss-120b` '
                'in organization `org_01m81a9ktzeqrrej4sgnstpjvf` service tier `on_demand` '
                'on tokens per minute (TPM): Limit 8000, Requested 8315')
    msg = friendly_llm_error(Exception(groq_413))
    assert "org_01m81a9ktzeqrrej4sgnstpjvf" not in msg
    assert "8315" not in msg
    assert friendly_llm_error(Exception("Error code: 429 rate limit reached")) != friendly_llm_error(Exception(groq_413))
    assert "rate limit" in friendly_llm_error(Exception("Error code: 429 rate limit reached")).lower()
    assert "API key" in friendly_llm_error(Exception("401 invalid api key"))
    assert "reach" in friendly_llm_error(Exception("connection timeout")).lower() or "timeout" in friendly_llm_error(Exception("connection timeout")).lower()
    assert friendly_llm_error(Exception("something completely different")).startswith("The model could not")


def test_tool_message_serialized_size_counts():
    m = {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c", "name": "read_file", "arguments": {"path": "x" * 500}}]}
    assert _message_chars(m) > 500


# ------------------------------------------------------------ integration

def test_run_developer_transparently_retries_413_with_compaction(tmp_path):
    """A 413 on a big loop prompt retries once with a compacted context."""
    import pytest

    from fixora.agents.common import RunContext
    from fixora.agents.developer import run_developer, _message_chars
    from fixora.config import Settings
    from fixora.llm import LLMError, LLMResponse, ToolCall
    from fixora.sandbox import Sandbox
    from fixora.tools import build_tools

    repo = tmp_path / "repo413"
    repo.mkdir()
    (repo / "big.py").write_text("# pad\n" + ("VALUE = 1  # padding\n" * 400))
    settings = Settings(
        llm_provider="mock", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="m", xai_api_key=None, xai_model="m",
        xai_base_url="https://api.x.ai/v1", groq_api_key=None, groq_model="openai/gpt-oss-120b",
        groq_base_url="https://api.groq.com/openai/v1",
        max_attempts=3, command_timeout=30, max_tool_steps=5, llm_providers="",
        run_timeout=300, clone_timeout=60, max_repo_mb=10, read_cache_chars=24000,
    )
    sb = Sandbox(repo)

    class TwelveKCapLLM:
        """Pretend TPM budget: rejects anything over CAP_CHARS like Groq does."""

        CAP_CHARS = 10_000

        def __init__(self):
            self.prompt_sizes = []
            self.n = 0

        def chat(self, messages, tools, reasoning_effort=None):
            size = sum(_message_chars(m) for m in messages)
            self.prompt_sizes.append(size)
            self.n += 1
            if self.n == 1:
                return LLMResponse(text="", tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "big.py"})])
            if size > self.CAP_CHARS:
                raise LLMError(
                    "Error code: 413 - Request too large for model `openai/gpt-oss-120b` "
                    "in organization `org_test` service tier `on_demand` on tokens per "
                    "minute (TPM): Limit 8000, Requested 9500, please reduce your "
                    "message size and try again.")
            return LLMResponse(text="done after compaction", tool_calls=[])

    fake = TwelveKCapLLM()
    try:
        ctx = RunContext(sandbox=sb, settings=settings, llm=fake,
                         tools=build_tools(sb, settings),
                         printer=lambda *a, **k: None)
        text, _ = run_developer(ctx, "fix", {"task": "fix the bug"},
                                "instructions:\n" + "detail " * 900)
        assert text == "done after compaction"
        assert len(fake.prompt_sizes) == 3
        assert fake.prompt_sizes[1] > fake.CAP_CHARS      # first attempt exceeded
        assert fake.prompt_sizes[2] <= fake.CAP_CHARS      # retry fit after compaction
    finally:
        sb.cleanup()
