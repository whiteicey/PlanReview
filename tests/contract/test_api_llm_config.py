"""LLM config endpoints: store non-key config, keep the key out of responses."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()

    # Inject an in-memory credential store so the test does not touch keyring.
    from app.api import routes

    class _FakeCreds:
        def __init__(self):
            self.keys = {}

        def set_key(self, provider, key):
            self.keys[provider] = key

        def get_key(self, provider):
            return self.keys.get(provider)

        def delete_key(self, provider):
            self.keys.pop(provider, None)

    routes._reset_llm_config_store(credentials=_FakeCreds())
    from app.main import app

    return TestClient(app)


def test_get_config_defaults_to_mock_with_deepseek_base_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/llm/config").json()
    assert body["provider"] == "mock"
    assert body["base_url"] == "https://api.deepseek.com/anthropic"
    assert body["key_present"] is False
    assert "api_key" not in body


def test_post_config_stores_key_out_of_response_and_reports_present(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.deepseek.com/anthropic",
            "model": "deepseek-chat",
            "api_key": "sk-secret-value",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "anthropic"
    assert body["model"] == "deepseek-chat"
    assert body["key_present"] is True
    assert "sk-secret-value" not in response.text

    later = client.get("/api/llm/config").json()
    assert later["key_present"] is True
    assert "api_key" not in later


def test_post_config_rejects_bad_base_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/llm/config",
        json={"provider": "anthropic", "base_url": "ftp://bad", "model": "m", "api_key": "k"},
    )
    assert response.status_code == 422
