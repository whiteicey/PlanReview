"""Persist non-secret LLM configuration; keep the API key only in credentials.

The provider/base_url/model live in a small JSON file under the storage root.
The API key is never written there — it is handed to a credential store (the
Windows Credential Manager in production, an injected fake in tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.llm.factory import LLMConfig
from app.security.url_policy import validate_llm_base_url

_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
_KEYRING_PROVIDER = "anthropic"


class Credentials(Protocol):
    def set_key(self, provider: str, key: str) -> None: ...
    def get_key(self, provider: str) -> str | None: ...
    def delete_key(self, provider: str) -> None: ...


class LLMConfigStore:
    def __init__(self, path: Path, credentials: Credentials) -> None:
        self._path = Path(path)
        self._credentials = credentials

    def load(self) -> LLMConfig:
        if not self._path.is_file():
            return LLMConfig(provider="mock", base_url=_DEFAULT_BASE_URL, model=None)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return LLMConfig(provider="mock", base_url=_DEFAULT_BASE_URL, model=None)
        if not isinstance(data, dict):
            return LLMConfig(provider="mock", base_url=_DEFAULT_BASE_URL, model=None)
        provider = data.get("provider")
        base_url = data.get("base_url")
        model = data.get("model")
        return LLMConfig(
            provider=provider if provider in ("mock", "anthropic") else "mock",
            base_url=base_url if isinstance(base_url, str) and base_url else _DEFAULT_BASE_URL,
            model=model if isinstance(model, str) and model else None,
        )

    def save(
        self,
        *,
        provider: str,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
    ) -> LLMConfig:
        if provider not in ("mock", "anthropic"):
            raise ValueError("provider must be mock or anthropic")
        if base_url:
            validate_llm_base_url(base_url)
        payload = {"provider": provider, "base_url": base_url, "model": model}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if api_key:
            self._credentials.set_key(_KEYRING_PROVIDER, api_key)
        return self.load()

    def get_key(self) -> str | None:
        return self._credentials.get_key(_KEYRING_PROVIDER)

    def key_present(self) -> bool:
        return bool(self.get_key())
