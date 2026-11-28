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


# ---------------------------------------------------------- pseudo-tools

def test_pseudo_tool_calls_are_never_executed(ctx):
    """A model emitting tool calls like 'json'/'commentary' must be tolerated:
    the calls are answered with an error tool message and never executed."""
    from fixora.llm import ToolCall, LLMResponse

    class PseudoToolLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools, reasoning_effort=None):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(text="", tool_calls=[
                    ToolCall(id="c1", name="json", arguments={"verdict": "approve"}),
                    ToolCall(id="c2", name="commentary", arguments={"note": "hi"}),
                ])
            return LLMResponse(text="summary", tool_calls=[])

    run_ctx, fake = ctx
    run_ctx.llm = PseudoToolLLM()
    before = run_ctx.stats.get("tool_calls", 0)
    text, messages = run_developer(run_ctx, "analyse", {"task": "find bug"}, "explore")
    assert text == "summary"
    assert run_ctx.stats.get("tool_calls", 0) == before  # no pseudo-tool ever executed
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2                        # one error result per bogus call id
    assert all("not an available tool" in m["content"] for m in tool_msgs)


def test_pseudo_tool_repeated_calls_raise_after_cap(tmp_path):
    """If the model keeps emitting pseudo-tools, stop cleanly with LLMError
    (the runner maps this to a friendly message + resumable checkpoint)."""
    from fixora.llm import ToolCall, LLMResponse

    repo = tmp_path / "demo3"
    repo.mkdir()
    (repo / "README.md").write_text("# demo\n")
    sb = Sandbox(repo)

    class AlwaysPseudo:
        def chat(self, messages, tools, reasoning_effort=None):
            return LLMResponse(text="", tool_calls=[ToolCall(id="c", name="json", arguments={})])

    try:
        run_ctx = RunContext(sandbox=sb, settings=_settings(), llm=AlwaysPseudo(),
                             tools=build_tools(sb, _settings()),
                             printer=lambda *a, **k: None)
        with pytest.raises(LLMError, match="unavailable tools"):
            run_developer(run_ctx, "analyse", {"task": "find bug"}, "explore")
    finally:
        sb.cleanup()


def test_mixed_turn_executes_valid_calls_and_rejects_bogus(ctx):
    from fixora.llm import ToolCall, LLMResponse

    class MixedLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools, reasoning_effort=None):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(text="", tool_calls=[
                    ToolCall(id="c1", name="list_tree", arguments={}),
                    ToolCall(id="c2", name="json", arguments={"x": 1}),
                ])
            return LLMResponse(text="done", tool_calls=[])

    run_ctx, fake = ctx
    run_ctx.llm = MixedLLM()
    text, messages = run_developer(run_ctx, "analyse", {"task": "find bug"}, "explore")
    assert text == "done"
    by_id = {m.get("tool_call_id"): m for m in messages if m["role"] == "tool"}
    assert '"error"' not in by_id["c1"]["content"]       # real tool executed
    assert "not an available tool" in by_id["c2"]["content"]  # pseudo-tool answered with error


def test_schema_mismatch_correction_names_required_params(tmp_path):
    """The exact production failure: provider 400 'parameters for tool
    apply_patch did not match schema: errors: [missing properties ...]'.

    The retry message must name the tool's REQUIRED properties so the model
    can resend a valid call — the generic 'only these tools exist' message
    does not help here."""
    repo = tmp_path / "demo_schema"
    repo.mkdir()
    (repo / "README.md").write_text("# demo\n")
    sb = Sandbox(repo)

    class FlakyLLM:
        def __init__(self):
            self.calls = 0
            self.messages = []

        def chat(self, messages, tools, reasoning_effort=None):
            self.calls += 1
            self.messages = messages
            if self.calls == 1:
                raise LLMError("Error code: 400 - Tool call validation failed: "
                               "tool call validation failed: parameters for tool "
                               "apply_patch did not match schema: errors: "
                               "[missing properties: 'path', 'old_str', 'new_str']")
            return LLMResponse(text="ok", tool_calls=[])

    fake = FlakyLLM()
    try:
        run_ctx = RunContext(sandbox=sb, settings=_settings(), llm=fake,
                             tools=build_tools(sb, _settings()),
                             printer=lambda *a, **k: None)
        text, _ = run_developer(run_ctx, "fix", {"task": "fix it"}, "fix")
        assert text == "ok"
        assert fake.calls == 2
        corrective = [m for m in fake.messages
                      if m["role"] == "user" and "REQUIRES exactly these JSON properties" in m["content"]]
        assert corrective, "expected a schema-aware corrective message"
        body = corrective[0]["content"]
        assert "path" in body and "old_str" in body and "new_str" in body
        assert "apply_patch" in body
    finally:
        sb.cleanup()


def test_wrong_phase_tool_gets_phase_correction(tmp_path):
    """Tool exists but is not allowed in this phase — the correction must say
    so, not dump the tool's argument schema (which the model didn't misuse)."""
    repo = tmp_path / "demo_phase"
    repo.mkdir()
    (repo / "README.md").write_text("# demo\n")
    sb = Sandbox(repo)

    class WrongPhaseLLM:
        def __init__(self):
            self.calls = 0
            self.messages = []

        def chat(self, messages, tools, reasoning_effort=None):
            self.calls += 1
            self.messages = messages
            if self.calls == 1:
                raise LLMError("Error code: 400 - Tool call validation failed: "
                               "attempted to call tool 'apply_patch' which was not in request.tools")
            return LLMResponse(text="ok", tool_calls=[])

    fake = WrongPhaseLLM()
    try:
        run_ctx = RunContext(sandbox=sb, settings=_settings(), llm=fake,
                             tools=build_tools(sb, _settings()),
                             printer=lambda *a, **k: None)
        text, _ = run_developer(run_ctx, "analyse", {"task": "find bug"}, "explore")
        assert text == "ok"
        assert fake.calls == 2
        corrective = [m for m in fake.messages
                      if m["role"] == "user" and "does not exist in this phase" in m["content"]]
        assert corrective, "expected a phase-aware corrective message"
        assert "apply_patch" in corrective[0]["content"]
    finally:
        sb.cleanup()
