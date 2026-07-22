from __future__ import annotations

from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.factory import LLMConfig, build_provider
from app.llm.mock import MockProvider


def test_defaults_to_mock_when_provider_is_mock():
    provider = build_provider(LLMConfig(provider="mock"), api_key=None)
    assert isinstance(provider, MockProvider)


def test_incomplete_anthropic_config_is_explicit_and_never_falls_back_to_mock():
    from app.llm.factory import ConfigurationErrorProvider

    assert isinstance(build_provider(LLMConfig(provider="anthropic", base_url="https://x/anthropic", model="m"), api_key=None), ConfigurationErrorProvider)
    assert isinstance(build_provider(LLMConfig(provider="anthropic", base_url=None, model="m"), api_key="k"), ConfigurationErrorProvider)
    assert isinstance(build_provider(LLMConfig(provider="anthropic", base_url="https://x/anthropic", model=None), api_key="k"), ConfigurationErrorProvider)
    assert isinstance(build_provider(LLMConfig(provider="invalid", configuration_error="bad"), api_key=None), ConfigurationErrorProvider)
    assert isinstance(build_provider(LLMConfig(provider="anthropic", base_url="ftp://invalid", model="m"), api_key="k"), ConfigurationErrorProvider)


def test_builds_anthropic_adapter_when_fully_configured():
    provider = build_provider(
        LLMConfig(provider="anthropic", base_url="https://api.example.com/anthropic", model="claude-x"),
        api_key="secret",
    )
    assert isinstance(provider, AnthropicAdapter)
