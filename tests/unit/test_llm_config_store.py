from __future__ import annotations

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


def test_saving_without_new_key_keeps_existing_key(tmp_path):
    path = tmp_path / "llm_config.json"
    creds = _FakeCredentials()
    store = LLMConfigStore(path, credentials=creds)
    store.save(provider="anthropic", base_url="https://api.deepseek.com/anthropic", model="m", api_key="k1")
    store.save(provider="anthropic", base_url="https://api.deepseek.com/anthropic", model="m2", api_key=None)
    assert store.get_key() == "k1"
    assert store.load().model == "m2"


def test_rejects_invalid_base_url(tmp_path):
    store = _store(tmp_path)
    import pytest

    with pytest.raises(Exception):
        store.save(provider="anthropic", base_url="ftp://bad", model="m", api_key="k")
