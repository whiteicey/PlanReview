"""Select the review LLM provider from local configuration.

The key is passed separately (read from the keyring by the caller) and is never
stored in ``LLMConfig``. Incomplete online configuration is represented by an
explicit non-network provider so the Run records CONFIGURATION_ERROR.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.exceptions import ReviewError
from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.mock import MockProvider
from app.llm.provider import LLMConfigurationError, LLMProvider, LLMRequest


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "mock"
    base_url: str | None = None
    model: str | None = None
    allow_private_endpoint: bool = False
    configuration_error: str | None = None


class ConfigurationErrorProvider:
    def __init__(self, provider_name: str, model_name: str | None = None) -> None:
        self.provider_name = provider_name
        self.model_name = model_name

    def review(self, _request: LLMRequest):
        raise LLMConfigurationError("LLM configuration is unavailable")

    def test_connection(self):
        raise LLMConfigurationError("LLM configuration is unavailable")


def build_provider(
    config: LLMConfig,
    api_key: str | None,
    *,
    credential_error: bool = False,
) -> LLMProvider:
    if config.provider == "mock" and config.configuration_error is None:
        return MockProvider()
    if (
        config.provider == "anthropic"
        and config.base_url
        and config.model
        and api_key
        and not credential_error
        and config.configuration_error is None
    ):
        try:
            return AnthropicAdapter(
                base_url=config.base_url,
                model=config.model,
                api_key=api_key,
                allow_private_endpoint=config.allow_private_endpoint,
            )
        except (ReviewError, ValueError):
            return ConfigurationErrorProvider(config.provider, config.model)
    return ConfigurationErrorProvider(config.provider, config.model)
