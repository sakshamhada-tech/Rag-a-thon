"""Unit tests for provider response/request parsing (dict- or object-style)."""
import json
from types import SimpleNamespace

import pytest

import anthropic
import openai

from devmate.llm import (
    AnthropicClient,
    LLMError,
    OpenAIClient,
    _parse_anthropic_response,
    _parse_openai_response,
    _parse_tool_arguments,
)


def test_openai_parse_dict_response_with_tool_call():
    resp = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "list_tree", "arguments": '{"path": ".", "max_depth": 2}'}}]}}]}
    parsed = _parse_openai_response(resp)
    assert parsed.tool_calls[0].id == "call_1"
    assert parsed.tool_calls[0].name == "list_tree"
    assert parsed.tool_calls[0].arguments == {"path": ".", "max_depth": 2}


def test_openai_parse_object_response_with_tool_call():
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(id="call_2",
                                    function=SimpleNamespace(name="read_file",
                                                             arguments='{"path": "money.py"}'))]))])
    parsed = _parse_openai_response(resp)
    assert parsed.tool_calls[0].id == "call_2"
    assert parsed.tool_calls[0].arguments == {"path": "money.py"}


def test_openai_parse_tool_arguments_already_dict():
    resp = {"choices": [{"message": {"content": "done", "tool_calls": [
        {"id": "c", "function": {"name": "write_file",
                                "arguments": {"path": "x.py", "content": "print(1)\n"}}}]}}]}
    parsed = _parse_openai_response(resp)
    assert parsed.tool_calls[0].arguments == {"path": "x.py", "content": "print(1)\n"}


def test_openai_parse_invalid_json_arguments_returns_structured_error():
    resp = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c", "function": {"name": "apply_patch", "arguments": "{not json"}}]}}]}
    parsed = _parse_openai_response(resp)
    assert "_json_error" in parsed.tool_calls[0].arguments


def test_openai_empty_choices_raises_llm_error():
    with pytest.raises(LLMError):
        _parse_openai_response({"choices": []})


def test_anthropic_parse_dict_blocks():
    resp = {"content": [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "t1", "name": "search_code", "input": {"pattern": "ROUND"}},
    ]}
    parsed = _parse_anthropic_response(resp)
    assert parsed.text == "hi"
    assert parsed.tool_calls[0].name == "search_code"
    assert parsed.tool_calls[0].arguments == {"pattern": "ROUND"}


def test_parse_tool_arguments_none_and_object():
    assert _parse_tool_arguments(None) == {}
    assert _parse_tool_arguments({"a": 1}) == {"a": 1}
    assert _parse_tool_arguments("[1, 2]") == {"_raw_arguments": [1, 2]}


class _FakeOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.captured = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        _FakeOpenAI.instances.append(self)

    def _create(self, **kwargs):
        self.captured = kwargs
        return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}


class _FakeAnthropicMessages:
    def __init__(self):
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        return {"content": [{"type": "text", "text": "ok"}]}


class _FakeAnthropic:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = _FakeAnthropicMessages()
        _FakeAnthropic.instances.append(self)


def _history_with_dict_tool_call():
    return [
        {"role": "system", "content": "PHASE: analyse\nYou are DevMate."},
        {"role": "user", "content": "find the bug"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "name": "list_tree",
                         "arguments": {"path": ".", "max_depth": 2}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "list_tree", "content": "demo_repo/"},
    ]


def test_openai_client_serializes_dict_tool_calls(monkeypatch):
    _FakeOpenAI.instances.clear()
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    client = OpenAIClient("key", "model", base_url="https://example.test/v1", provider_label="groq")
    resp = client.chat(_history_with_dict_tool_call(), [])
    captured = _FakeOpenAI.instances[0].captured
    tool_call = captured["messages"][2]["tool_calls"][0]
    assert tool_call["id"] == "call_1"
    assert tool_call["function"]["name"] == "list_tree"
    assert json.loads(tool_call["function"]["arguments"]) == {"path": ".", "max_depth": 2}
    assert resp.text == "ok"


def test_anthropic_client_serializes_dict_tool_calls(monkeypatch):
    _FakeAnthropic.instances.clear()
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    client = AnthropicClient("key", "model")
    resp = client.chat(_history_with_dict_tool_call(), [])
    captured = _FakeAnthropic.instances[0].messages.captured
    assistant = captured["messages"][1]  # Anthropic path omits the system message
    tool_use = assistant["content"][0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["id"] == "call_1"
    assert tool_use["input"] == {"path": ".", "max_depth": 2}
    assert resp.text == "ok"
