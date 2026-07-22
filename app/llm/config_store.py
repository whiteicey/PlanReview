"""Persist non-secret LLM configuration; keep the API key only in credentials.

The provider/base_url/model live in a small JSON file under the storage root.
The API key is never written there — it is handed to a credential store (the
Windows Credential Manager in production, an injected fake in tests).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.llm.factory import LLMConfig
from app.domain.exceptions import ReviewError
from app.security.url_policy import validate_llm_base_url

_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
_KEYRING_PROVIDER = "anthropic"
_KEY_REENTRY_ERROR = "re-enter API key required before changing online endpoint"


class Credentials(Protocol):
    def set_key(self, provider: str, key: str) -> None: ...
    def get_key(self, provider: str) -> str | None: ...
    def delete_key(self, provider: str) -> None: ...


@dataclass(frozen=True)
class _ConfigReadResult:
    state: str
    config: LLMConfig


class LLMConfigStore:
    def __init__(self, path: Path, credentials: Credentials) -> None:
        self._path = Path(path)
        self._credentials = credentials

    def _read_config(self) -> _ConfigReadResult:
        if not self._path.is_file():
            return _ConfigReadResult(
                "MISSING",
                LLMConfig(
                    provider="mock", base_url=_DEFAULT_BASE_URL, model=None,
                    allow_private_endpoint=False,
                ),
            )
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return _ConfigReadResult(
                "INVALID",
                LLMConfig(provider="invalid", configuration_error="LLM configuration is invalid"),
            )
        if not isinstance(data, dict):
            return _ConfigReadResult(
                "INVALID",
                LLMConfig(provider="invalid", configuration_error="LLM configuration is invalid"),
            )
        provider = data.get("provider")
        base_url = data.get("base_url")
        model = data.get("model")
        allow_private_endpoint = data.get("allow_private_endpoint", False)
        valid_model = model is None or (isinstance(model, str) and bool(model))
        valid_base = base_url is None or (isinstance(base_url, str) and bool(base_url))
        if (
            provider not in ("mock", "anthropic")
            or not isinstance(allow_private_endpoint, bool)
            or not valid_model
            or not valid_base
            or (provider == "anthropic" and not isinstance(base_url, str))
        ):
            return _ConfigReadResult(
                "INVALID",
                LLMConfig(provider="invalid", configuration_error="LLM configuration is invalid"),
            )
        resolved_base = base_url or _DEFAULT_BASE_URL
        try:
            validate_llm_base_url(
                resolved_base, allow_private_endpoint=allow_private_endpoint
            )
        except ReviewError:
            return _ConfigReadResult(
                "INVALID",
                LLMConfig(provider="invalid", configuration_error="LLM configuration is invalid"),
            )
        return _ConfigReadResult(
            "VALID",
            LLMConfig(
                provider=provider,
                base_url=resolved_base,
                model=model,
                allow_private_endpoint=allow_private_endpoint,
            ),
        )

    def load(self) -> LLMConfig:
        return self._read_config().config

    def save(
        self,
        *,
        provider: str,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
        allow_private_endpoint: bool = False,
    ) -> LLMConfig:
        if provider not in ("mock", "anthropic"):
            raise ValueError("provider must be mock or anthropic")
        if not isinstance(allow_private_endpoint, bool):
            raise ValueError("allow_private_endpoint must be boolean")
        if base_url:
            validate_llm_base_url(base_url, allow_private_endpoint=allow_private_endpoint)
        previous_read = self._read_config()
        previous = previous_read.config
        previous_key = self.get_key()
        trusted_unchanged_online_endpoint = (
            previous_read.state == "VALID"
            and previous.provider == "anthropic"
            and provider == "anthropic"
            and _normalize_endpoint(previous.base_url) == _normalize_endpoint(base_url)
            and previous.allow_private_endpoint == allow_private_endpoint
        )
        if (
            provider == "anthropic"
            and previous_key
            and not api_key
            and not trusted_unchanged_online_endpoint
        ):
            raise ValueError(_KEY_REENTRY_ERROR)

        payload = {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "allow_private_endpoint": allow_private_endpoint,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        old_bytes = self._path.read_bytes() if self._path.is_file() else None
        try:
            if provider == "mock":
                self.delete_key()
            elif api_key:
                self._credentials.set_key(_KEYRING_PROVIDER, api_key)
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            if old_bytes is None:
                self._path.unlink(missing_ok=True)
            else:
                self._path.write_bytes(old_bytes)
            if previous_key:
                self._credentials.set_key(_KEYRING_PROVIDER, previous_key)
            else:
                self._credentials.delete_key(_KEYRING_PROVIDER)
            raise
        return self.load()

    def get_key(self) -> str | None:
        return self._credentials.get_key(_KEYRING_PROVIDER)

    def key_present(self) -> bool:
        return bool(self.get_key())

    def delete_key(self) -> None:
        self._credentials.delete_key(_KEYRING_PROVIDER)


def _normalize_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        return value.casefold().rstrip("/")
    netloc = host
    if port is not None and not (
        (parsed.scheme.casefold() == "https" and port == 443)
        or (parsed.scheme.casefold() == "http" and port == 80)
    ):
        netloc = f"{host}:{port}"
    return urlunsplit(
        (parsed.scheme.casefold(), netloc, parsed.path.rstrip("/"), parsed.query, "")
    )
