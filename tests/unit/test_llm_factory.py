from __future__ import annotations

from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.factory import LLMConfig, build_provider
from app.llm.mock import MockProvider


def test_defaults_to_mock_when_provider_is_mock():
    provider = build_provider(LLMConfig(provider="mock"), api_key=None)
    assert isinstance(provider, MockProvider)


def test_falls_back_to_mock_when_anthropic_config_incomplete():
    # Missing key or base_url -> Mock, never a half-configured online call.
    assert isinstance(build_provider(LLMConfig(provider="anthropic", base_url="https://x/anthropic", model="m"), api_key=None), MockProvider)
    assert isinstance(build_provider(LLMConfig(provider="anthropic", base_url=None, model="m"), api_key="k"), MockProvider)


def test_builds_anthropic_adapter_when_fully_configured():
    provider = build_provider(
        LLMConfig(provider="anthropic", base_url="https://api.example.com/anthropic", model="claude-x"),
        api_key="secret",
    )
    assert isinstance(provider, AnthropicAdapter)
