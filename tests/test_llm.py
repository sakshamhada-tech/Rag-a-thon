"""Unit tests for LLM provider selection (no network calls)."""
import pytest

from devmate.config import Settings
from devmate.llm import LLMError, MockClient, OpenAIClient, get_llm, list_available_models


def _settings(provider: str, **overrides) -> Settings:
    base = dict(
        llm_provider=provider,
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
    )
    base.update(overrides)
    return Settings(**base)


def test_xai_requires_api_key():
    with pytest.raises(LLMError) as exc:
        get_llm(_settings("xai"))
    assert "XAI_API_KEY" in str(exc.value)


def test_grok_alias_requires_api_key():
    with pytest.raises(LLMError) as exc:
        get_llm(_settings("grok"))
    assert "XAI_API_KEY" in str(exc.value)


def test_xai_selects_openai_compatible_client():
    client = get_llm(_settings("xai", xai_api_key="test-xai-key", xai_model="test-grok-model"))
    assert isinstance(client, OpenAIClient)
    assert client.model == "test-grok-model"
    assert client.provider_label == "xai"


def test_mock_still_available():
    assert isinstance(get_llm(_settings("mock")), MockClient)


def test_groq_requires_api_key():
    with pytest.raises(LLMError) as exc:
        get_llm(_settings("groq"))
    assert "GROQ_API_KEY" in str(exc.value)


def test_groq_selects_openai_compatible_client():
    client = get_llm(_settings("groq", groq_api_key="gsk-test", groq_model="test-tool-model"))
    assert isinstance(client, OpenAIClient)
    assert client.model == "test-tool-model"
    assert client.provider_label == "groq"


def test_list_available_models_mock():
    assert list_available_models(_settings("mock")) == ["mock (offline)"]
