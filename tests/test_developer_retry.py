"""Unit tests for the developer tool-calling loop's provider-error handling."""
import pytest

from fixora.agents.common import RunContext
from fixora.agents.developer import run_developer
from fixora.config import Settings
from fixora.llm import LLMError, LLMResponse
from fixora.sandbox import Sandbox
from fixora.tools import build_tools


def _settings():
    return Settings(
        llm_provider="groq",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-5",
        openai_api_key=None,
        openai_model="gpt-4o-mini",
        xai_api_key=None,
        xai_model="grok-4-latest",
        xai_base_url="https://api.x.ai/v1",
        groq_api_key="gsk-test",
        groq_model="openai/gpt-oss-120b",
        groq_base_url="https://api.groq.com/openai/v1",
        max_attempts=3,
        command_timeout=60,
        max_tool_steps=5,
        llm_providers="",
        run_timeout=900,
        clone_timeout=120,
        max_repo_mb=50,
        read_cache_chars=24000,
    )


@pytest.fixture
def ctx(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "README.md").write_text("# demo\n")
    sb = Sandbox(repo)

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools, reasoning_effort=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("Error code: 400 - Tool call validation failed: "
                               "attempted to call tool 'apply_patch' which was not in request.tools")
            return LLMResponse(text="summary", tool_calls=[])

    fake = FakeLLM()
    try:
        yield RunContext(sandbox=sb, settings=_settings(), llm=fake,
                         tools=build_tools(sb, _settings()),
                         printer=lambda *a, **k: None), fake
    finally:
        sb.cleanup()


def test_tool_unavailable_error_gets_one_correction_round(ctx):
    run_ctx, fake = ctx
    text, messages = run_developer(run_ctx, "analyse", {"task": "find bug"}, "explore")
    assert text == "summary"
    assert fake.calls == 2
    assert any(m["role"] == "user" and "Allowed tools" not in m["content"]
               and "Only these tools exist" in m["content"]
               for m in messages)


def test_non_tool_llm_error_raises(tmp_path):
    repo = tmp_path / "demo2"
    repo.mkdir()
    (repo / "README.md").write_text("# demo\n")
    sb = Sandbox(repo)

    class BadLLM:
        def chat(self, messages, tools, reasoning_effort=None):
            raise LLMError("Error code: 401 - invalid api key")

    try:
        run_ctx = RunContext(sandbox=sb, settings=_settings(), llm=BadLLM(),
                             tools=build_tools(sb, _settings()),
                             printer=lambda *a, **k: None)
        with pytest.raises(LLMError):
            run_developer(run_ctx, "analyse", {"task": "find bug"}, "explore")
    finally:
        sb.cleanup()
