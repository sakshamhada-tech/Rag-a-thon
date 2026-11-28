"""Fallback chain order: monotonic, forward-only key switching.

When one API key reaches its limit the chain must move to the NEXT key in
number (key1 -> key2 -> key3) and never go back to an earlier key — otherwise
runs keep flapping between keys every call. Dashboard-facing messages must be
friendly ("Switching to next API key"), never provider internals.
"""
import time

import pytest

from fixora.llm import FallbackLLMClient, LLMError, LLMResponse


class _RateLimited:
    def __init__(self, label="dead"):
        self.calls = 0
        self.provider_label = label
        self.supports_reasoning = False

    def chat(self, messages, tools, reasoning_effort=None):
        self.calls += 1
        raise LLMError("429 rate limit exceeded: tokens per day exhausted")


class _Healthy:
    def __init__(self, label="ok", text="ok"):
        self.calls = 0
        self.provider_label = label
        self.supports_reasoning = False
        self.text = text

    def chat(self, messages, tools, reasoning_effort=None):
        self.calls += 1
        return LLMResponse(text=self.text, tool_calls=[])


def _msgs():
    return [{"role": "user", "content": "hi"}]


def test_switches_to_next_key_in_number():
    k1, k2, k3 = _RateLimited("key1"), _RateLimited("key2"), _Healthy("key3", "ok-from-key3")
    client = FallbackLLMClient([k1, k2, k3])
    resp = client.chat(_msgs(), [])
    assert resp.text == "ok-from-key3"
    assert k1.calls == 2          # one retry on the dead key
    assert k2.calls == 2          # retried, then abandoned for good
    assert k3.calls == 1


def test_never_returns_to_an_earlier_key(monkeypatch):
    k1, k2 = _RateLimited("key1"), _Healthy("key2")
    client = FallbackLLMClient([k1, k2])
    for _ in range(5):
        client.chat(_msgs(), [])
    assert k1.calls == 2          # exhausted once, never revisited — no flapping
    assert k2.calls == 5


def test_sticks_at_the_last_healthy_key():
    k1, k2, k3 = _RateLimited("key1"), _Healthy("key2", "from-key2"), _Healthy("key3", "from-key3")
    client = FallbackLLMClient([k1, k2, k3])
    client.chat(_msgs(), [])
    client.chat(_msgs(), [])
    assert k3.calls == 0          # key2 healthy -> key3 never touched


def test_ui_message_is_friendly_console_gets_detail(capsys):
    seen = []
    FallbackLLMClient.on_fallback = seen.append
    try:
        k1, k2 = _RateLimited("gsk-secret-key-1234567890"), _Healthy("key2")
        client = FallbackLLMClient([k1, k2])
        client.chat(_msgs(), [])
    finally:
        FallbackLLMClient.on_fallback = None
    assert seen == ["Switching to next API key"]
    out = capsys.readouterr().out
    assert "[LLM FALLBACK]" in out          # raw detail stays on the operator console
    assert "Switching" not in str(seen) or "429" not in str(seen)


def test_all_keys_exhausted_raises_clear_error():
    k1, k2 = _RateLimited("key1"), _RateLimited("key2")
    client = FallbackLLMClient([k1, k2])
    with pytest.raises(LLMError):
        client.chat(_msgs(), [])
    # Friendly mapping: friendly_llm_error() must produce the rate-limit one-liner
    from fixora.llm import friendly_llm_error
    client2 = FallbackLLMClient([_RateLimited("k1"), _RateLimited("k2")])
    try:
        client2.chat(_msgs(), [])
    except LLMError as e:
        friendly = friendly_llm_error(e)
        assert "rate limit" in friendly.lower() or "unavailable" in friendly.lower()


def test_non_retryable_error_still_raises_immediately():
    class _BadRequest:
        provider_label = "bad"
        supports_reasoning = False

        def chat(self, messages, tools, reasoning_effort=None):
            raise LLMError("400 invalid request schema")

    k2 = _Healthy("key2")
    client = FallbackLLMClient([_BadRequest(), k2])
    with pytest.raises(LLMError, match="400"):
        client.chat(_msgs(), [])
    assert k2.calls == 0
