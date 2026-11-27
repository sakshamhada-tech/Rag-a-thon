"""Provider cooldown/circuit breaker: dead keys are skipped, not retried forever."""
import time

import pytest

from fixora.llm import FallbackLLMClient, LLMError, LLMResponse


class _RateLimited:
    provider_label = "rate-limited"
    supports_reasoning = False

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools, reasoning_effort=None):
        self.calls += 1
        raise LLMError("429 rate limit exceeded: tokens per day exhausted")


class _Healthy:
    provider_label = "healthy"
    supports_reasoning = False

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools, reasoning_effort=None):
        self.calls += 1
        return LLMResponse(text="ok", tool_calls=[])


def test_first_call_retries_then_falls_back_and_marks_cooldown(monkeypatch):
    monkeypatch.setenv("FIXORA_PROVIDER_COOLDOWN", "300")
    dead, live = _RateLimited(), _Healthy()
    client = FallbackLLMClient([dead, live])
    assert client.chat([{"role": "user", "content": "hi"}], []).text == "ok"
    assert dead.calls == 2          # one retry, then cooldown — not 2 per call forever


def test_subsequent_calls_skip_dead_key_entirely(monkeypatch, capsys):
    monkeypatch.setenv("FIXORA_PROVIDER_COOLDOWN", "300")
    dead, live = _RateLimited(), _Healthy()
    client = FallbackLLMClient([dead, live])
    for _ in range(3):
        client.chat([{"role": "user", "content": "hi"}], [])
    assert dead.calls == 2          # served from cache of cooldown after first call
    assert live.calls == 3
    assert capsys.readouterr().out.count("[LLM FALLBACK]") == 1   # logged once, not per call


def test_all_providers_on_cooldown_fails_clearly(monkeypatch):
    monkeypatch.setenv("FIXORA_PROVIDER_COOLDOWN", "300")
    a, b = _RateLimited(), _RateLimited()
    client = FallbackLLMClient([a, b])
    for _ in range(2):  # first call fills cooldowns; second should short-circuit
        try:
            client.chat([{"role": "user", "content": "hi"}], [])
        except LLMError:
            pass
    with pytest.raises(LLMError, match="[Aa]ll .* providers are on cooldown"):
        client.chat([{"role": "user", "content": "hi"}], [])


def test_zero_cooldown_preserves_old_behavior(monkeypatch):
    monkeypatch.setenv("FIXORA_PROVIDER_COOLDOWN", "0")
    dead, live = _RateLimited(), _Healthy()
    client = FallbackLLMClient([dead, live])
    for _ in range(2):
        client.chat([{"role": "user", "content": "hi"}], [])
    assert dead.calls == 4          # no skipping: old behavior, 2 attempts per call


def test_cooldown_expires(monkeypatch):
    monkeypatch.setenv("FIXORA_PROVIDER_COOLDOWN", "0.2")
    dead, live = _RateLimited(), _Healthy()
    client = FallbackLLMClient([dead, live])
    client.chat([{"role": "user", "content": "hi"}], [])
    assert dead.calls == 2
    time.sleep(0.25)
    client.chat([{"role": "user", "content": "hi"}], [])
    assert dead.calls == 4          # retried after expiry
