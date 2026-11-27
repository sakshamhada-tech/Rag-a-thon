"""Unit tests for provider response parsing (dict- or object-style payloads)."""
from types import SimpleNamespace

import pytest

from devmate.llm import (
    LLMError,
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
