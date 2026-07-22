"""LLM config endpoints: store non-key config, keep the key out of responses."""

from __future__ import annotations

from pathlib import Path
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient
import pytest

from app.llm.provider import (
    LLMConnectionResult,
    LLMProviderError,
    LLMResponse,
    LLMValidationError,
)


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


def _docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("普通方案正文")
    payload = BytesIO()
    document.save(payload)
    return payload.getvalue()


def test_get_config_defaults_to_mock_with_deepseek_base_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/llm/config").json()
    assert body["provider"] == "mock"
    assert body["base_url"] == "https://api.deepseek.com/anthropic"
    assert body["key_present"] is False
    assert body["credential_storage_available"] is True
    assert body["configuration_error"] is None
    assert body["allow_private_endpoint"] is False
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
            "allow_private_endpoint": False,
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

    cleared = client.delete("/api/llm/config/credentials")
    assert cleared.status_code == 200
    assert cleared.json()["key_present"] is False


def test_post_config_rejects_bad_base_url(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/llm/config",
        json={"provider": "anthropic", "base_url": "ftp://bad", "model": "m", "api_key": "k"},
    )
    assert response.status_code == 422


def test_endpoint_change_without_reauthentication_is_fixed_422(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.deepseek.com/anthropic",
            "model": "m",
            "api_key": "sk-old",
        },
    )
    assert first.status_code == 200

    changed = client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.minimaxi.com/anthropic",
            "model": "m",
        },
    )
    assert changed.status_code == 422
    assert changed.json()["detail"] == "修改在线端点或私网模式前必须重新输入 API Key。"
    assert client.get("/api/llm/config").json()["base_url"] == "https://api.deepseek.com/anthropic"


def test_corrupt_config_with_orphan_key_is_fixed_422_and_unchanged(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.deepseek.com/anthropic",
            "model": "m",
            "api_key": "old-placeholder-key",
        },
    )
    assert first.status_code == 200
    config_path = tmp_path / "storage" / "llm_config.json"
    config_path.write_text("{broken", encoding="utf-8")

    changed = client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.example.com/anthropic",
            "model": "new-model",
        },
    )

    assert changed.status_code == 422
    assert changed.json()["detail"] == "修改在线端点或私网模式前必须重新输入 API Key。"
    assert "old-placeholder-key" not in changed.text
    assert config_path.read_text(encoding="utf-8") == "{broken"
    assert client.get("/api/llm/config").json()["key_present"] is True


def test_switching_to_mock_clears_online_credentials(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.deepseek.com/anthropic",
            "model": "deepseek-chat",
            "api_key": "sk-secret-value",
        },
    )
    response = client.post(
        "/api/llm/config",
        json={"provider": "mock", "base_url": None, "model": None},
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "mock"
    assert response.json()["key_present"] is False


def test_keyring_failure_is_sanitized_and_reported_as_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.api import routes
    from app.settings import get_settings

    class BrokenCredentials:
        def set_key(self, *_args):
            raise RuntimeError("sk-must-not-leak")

        def get_key(self, *_args):
            raise RuntimeError("sk-must-not-leak")

        def delete_key(self, *_args):
            raise RuntimeError("sk-must-not-leak")

    get_settings.cache_clear()
    previous = routes._LLM_CONFIG_STORE
    try:
        routes._reset_llm_config_store(credentials=BrokenCredentials())
        from app.main import app

        response = TestClient(app).get("/api/llm/config")
        assert response.status_code == 200
        assert response.json()["credential_storage_available"] is False
        assert response.json()["configuration_error"] == "系统凭据存储不可用"
        assert "sk-must-not-leak" not in response.text
    finally:
        routes._LLM_CONFIG_STORE = previous


def test_incomplete_online_configuration_is_persisted_as_configuration_error_run(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    configured = client.post(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "base_url": "https://api.deepseek.com/anthropic",
            "model": None,
            "api_key": "sk-secret-value",
        },
    )
    assert configured.status_code == 200
    created = client.post(
        "/api/cases",
        files={"file": ("config.docx", _docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    reviewed = client.post(f"/api/cases/{created.json()['case_id']}/review")
    assert reviewed.status_code == 201
    run = client.get(
        f"/api/cases/{created.json()['case_id']}/runs/{reviewed.json()['run_id']}"
    ).json()
    assert run["final_status"] == "READY_FOR_HUMAN_REVIEW"
    assert run["llm_provider"] == "anthropic"
    assert run["llm_status"] == "CONFIGURATION_ERROR"
    assert run["llm_error_summary"] == "LLM configuration is incomplete or unavailable"


class _StructuredProvider:
    provider_name = "anthropic"
    model_name = "deepseek-v4-pro"

    def __init__(self, result):
        self.result = result

    def test_connection(self):
        if isinstance(self.result, Exception):
            raise self.result
        return LLMConnectionResult("anthropic", self.model_name, 200)

    def review(self, request):
        if isinstance(self.result, Exception):
            raise self.result
        return LLMResponse("anthropic", self.model_name, self.result)


def _valid_structured_finding(evidence_ids):
    return {
        "category": "consistency", "severity": "high", "title": "虚拟数据不一致",
        "description": "两个虚拟证据中的产能不一致", "suggestion": "核对虚拟数据",
        "evidence_span_ids": evidence_ids,
    }


def test_basic_connection_does_not_claim_structured_output_success(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from app.api import routes
    provider = _StructuredProvider([])
    monkeypatch.setattr(routes, "_build_active_provider", lambda: provider)

    health = client.post("/api/llm/health")
    assert health.json() == {"ok": True, "detail": "基础连接正常；尚未验证结构化审查输出。"}
    structured = client.post("/api/llm/structured-output-test").json()
    assert structured["connection_ok"] is True
    assert structured["structured_output_ok"] is False
    assert structured["validation_reason_code"] is None
    assert (structured["candidate_count"], structured["valid_count"], structured["rejected_count"]) == (0, 0, 0)


def test_structured_output_test_requires_one_finding_with_both_evidence_ids(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from app.api import routes
    ids = ["structured-test-span-1", "structured-test-span-2"]
    monkeypatch.setattr(routes, "_build_active_provider", lambda: _StructuredProvider([_valid_structured_finding(ids)]))

    body = client.post("/api/llm/structured-output-test").json()
    assert body["connection_ok"] is True
    assert body["structured_output_ok"] is True
    assert body["validation_reason_code"] is None
    assert (body["candidate_count"], body["valid_count"], body["rejected_count"]) == (1, 1, 0)


@pytest.mark.parametrize(
    ("finding", "reason"),
    [
        (_valid_structured_finding(["structured-test-span-1"]), "invalid_evidence"),
        (_valid_structured_finding(["not-a-test-span"]), "invalid_evidence"),
        (_valid_structured_finding(["structured-test-span-1", "structured-test-span-2"]) | {"category": "invented"}, "invalid_category"),
    ],
)
def test_structured_output_test_rejects_incomplete_evidence_and_invalid_fields(
    monkeypatch, tmp_path, finding, reason
):
    client = _client(monkeypatch, tmp_path)
    from app.api import routes
    monkeypatch.setattr(routes, "_build_active_provider", lambda: _StructuredProvider([finding]))
    body = client.post("/api/llm/structured-output-test").json()
    assert body["connection_ok"] is True
    assert body["structured_output_ok"] is False
    assert body["validation_reason_code"] == reason
    assert (body["candidate_count"], body["valid_count"], body["rejected_count"]) == (1, 0, 1)


def test_structured_output_test_safely_classifies_validation_and_provider_errors(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from app.api import routes
    validation = LLMValidationError(
        "truncated_json", http_status=200, response_character_count=120,
        stop_reason="max_tokens", content_block_count=1,
    )
    monkeypatch.setattr(routes, "_build_active_provider", lambda: _StructuredProvider(validation))
    invalid = client.post("/api/llm/structured-output-test").json()
    assert invalid["connection_ok"] is True
    assert invalid["structured_output_ok"] is False
    assert invalid["validation_reason_code"] == "truncated_json"
    assert invalid["candidate_count"] is None

    monkeypatch.setattr(routes, "_build_active_provider", lambda: _StructuredProvider(LLMProviderError("secret transport detail")))
    failed = client.post("/api/llm/structured-output-test")
    assert failed.json()["connection_ok"] is False
    assert failed.json()["validation_reason_code"] is None
    assert "secret transport detail" not in failed.text


def test_mock_structured_output_test_is_explicitly_not_executed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.post("/api/llm/structured-output-test").json()
    assert body["connection_ok"] is True
    assert body["structured_output_ok"] is False
    assert "未执行真实结构化输出测试" in body["detail"]
