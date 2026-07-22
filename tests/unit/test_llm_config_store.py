from __future__ import annotations

import pytest

from app.llm.config_store import LLMConfigStore


class _FakeCredentials:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def set_key(self, provider: str, key: str) -> None:
        self._keys[provider] = key

    def get_key(self, provider: str) -> str | None:
        return self._keys.get(provider)

    def delete_key(self, provider: str) -> None:
        self._keys.pop(provider, None)


def _store(tmp_path):
    return LLMConfigStore(tmp_path / "llm_config.json", credentials=_FakeCredentials())


def test_default_config_uses_mock_with_deepseek_base_url(tmp_path):
    store = _store(tmp_path)
    config = store.load()
    assert config.provider == "mock"
    assert config.base_url == "https://api.deepseek.com/anthropic"
    assert config.allow_private_endpoint is False
    assert store.key_present() is False


def test_saving_config_persists_without_key_and_stores_key_in_credentials(tmp_path):
    path = tmp_path / "llm_config.json"
    store = LLMConfigStore(path, credentials=_FakeCredentials())
    store.save(provider="anthropic", base_url="https://api.deepseek.com/anthropic", model="deepseek-chat", api_key="sk-secret")

    on_disk = path.read_text(encoding="utf-8")
    assert "sk-secret" not in on_disk
    assert "anthropic" in on_disk
    assert store.key_present() is True
    assert store.get_key() == "sk-secret"


def test_reload_reads_persisted_non_key_config(tmp_path):
    path = tmp_path / "llm_config.json"
    creds = _FakeCredentials()
    LLMConfigStore(path, credentials=creds).save(
        provider="anthropic", base_url="https://api.minimaxi.com/anthropic", model="m", api_key="k",
    )
    reloaded = LLMConfigStore(path, credentials=creds).load()
    assert reloaded.provider == "anthropic"
    assert reloaded.base_url == "https://api.minimaxi.com/anthropic"
    assert reloaded.model == "m"


def test_private_endpoint_mode_is_explicit_and_persisted(tmp_path):
    store = _store(tmp_path)
    saved = store.save(
        provider="anthropic",
        base_url="http://127.0.0.1:11434",
        model="m",
        api_key="k",
        allow_private_endpoint=True,
    )

    assert saved.allow_private_endpoint is True
    assert store.load().allow_private_endpoint is True
    assert '"allow_private_endpoint": true' in (tmp_path / "llm_config.json").read_text()


def test_saving_without_new_key_keeps_existing_key(tmp_path):
    path = tmp_path / "llm_config.json"
    creds = _FakeCredentials()
    store = LLMConfigStore(path, credentials=creds)
    store.save(provider="anthropic", base_url="https://api.deepseek.com/anthropic", model="m", api_key="k1")
    store.save(provider="anthropic", base_url="https://api.deepseek.com/anthropic", model="m2", api_key=None)
    assert store.get_key() == "k1"
    assert store.load().model == "m2"


def test_endpoint_change_requires_key_reentry_and_keeps_old_configuration(tmp_path):
    path = tmp_path / "llm_config.json"
    creds = _FakeCredentials()
    store = LLMConfigStore(path, credentials=creds)
    store.save(
        provider="anthropic", base_url="https://api.deepseek.com/anthropic",
        model="m", api_key="k1",
    )

    import pytest
    with pytest.raises(ValueError, match="re-enter API key"):
        store.save(
            provider="anthropic", base_url="https://api.minimaxi.com/anthropic",
            model="m", api_key=None,
        )

    assert store.load().base_url == "https://api.deepseek.com/anthropic"
    assert store.get_key() == "k1"


def test_rejects_invalid_base_url(tmp_path):
    store = _store(tmp_path)
    import pytest

    with pytest.raises(Exception):
        store.save(provider="anthropic", base_url="ftp://bad", model="m", api_key="k")


def test_malformed_config_is_explicit_and_mock_switch_clears_key(tmp_path):
    path = tmp_path / "llm_config.json"
    path.write_text("{broken", encoding="utf-8")
    store = _store(tmp_path)
    broken = store.load()
    assert broken.provider == "invalid"
    assert broken.configuration_error == "LLM configuration is invalid"

    store.save(
        provider="anthropic",
        base_url="https://api.deepseek.com/anthropic",
        model="m",
        api_key="sk-secret",
    )
    assert store.key_present()
    store.save(provider="mock", base_url=None, model=None, api_key=None)
    assert not store.key_present()


@pytest.mark.parametrize("config_state", ["missing", "corrupt"])
def test_orphan_key_requires_reentry_when_previous_config_is_untrusted(
    tmp_path, config_state
):
    path = tmp_path / "llm_config.json"
    credentials = _FakeCredentials()
    credentials.set_key("anthropic", "old-placeholder-key")
    if config_state == "corrupt":
        path.write_text("{broken", encoding="utf-8")
    store = LLMConfigStore(path, credentials=credentials)
    previous_bytes = path.read_bytes() if path.exists() else None

    with pytest.raises(ValueError, match="re-enter API key"):
        store.save(
            provider="anthropic",
            base_url="https://api.example.com/anthropic",
            model="new-model",
            api_key=None,
        )

    assert store.get_key() == "old-placeholder-key"
    assert (path.read_bytes() if path.exists() else None) == previous_bytes


def test_corrupt_config_can_be_replaced_after_explicit_key_reentry(tmp_path):
    path = tmp_path / "llm_config.json"
    path.write_text("{broken", encoding="utf-8")
    credentials = _FakeCredentials()
    credentials.set_key("anthropic", "old-placeholder-key")
    store = LLMConfigStore(path, credentials=credentials)

    saved = store.save(
        provider="anthropic",
        base_url="https://api.example.com/anthropic",
        model="new-model",
        api_key="new-placeholder-key",
    )

    assert saved.provider == "anthropic"
    assert store.get_key() == "new-placeholder-key"
