"""Select the review LLM provider from local configuration.

The key is passed separately (read from the keyring by the caller) and is never
stored in ``LLMConfig``. An incomplete online configuration falls back to the
deterministic MockProvider rather than making a half-configured network call.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.mock import MockProvider
from app.llm.provider import LLMProvider


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "mock"
    base_url: str | None = None
    model: str | None = None


def build_provider(config: LLMConfig, api_key: str | None) -> LLMProvider:
    if config.provider == "anthropic" and config.base_url and config.model and api_key:
        return AnthropicAdapter(
            base_url=config.base_url, model=config.model, api_key=api_key
        )
    return MockProvider()
