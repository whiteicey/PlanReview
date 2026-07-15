from __future__ import annotations

import json

import httpx
import pytest

from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.provider import LLMProviderError, LLMRequest


def _request() -> LLMRequest:
    return LLMRequest(
        model="claude-x",
        system_prompt="只输出结构化复核意见",
        user_content="高峰产量超过处理能力",
        evidence_span_ids=["D:p:0", "D:t:1:1:1"],
    )


def _adapter(handler) -> AnthropicAdapter:
    transport = httpx.MockTransport(handler)
    return AnthropicAdapter(
        base_url="https://api.example.com/anthropic",
        model="claude-x",
        api_key="secret-key-123",
        transport=transport,
    )


def _anthropic_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})


def test_posts_to_v1_messages_with_auth_headers_and_maps_findings():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["x-api-key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["body"] = json.loads(request.content)
        findings = [{
            "category": "capacity",
            "severity": "high",
            "title": "高峰产量需复核",
            "description": "产量与处理能力关系需核实",
            "suggestion": "核对口径并补充依据",
            "evidence_span_ids": ["D:p:0"],
        }]
        return _anthropic_reply(json.dumps(findings))

    response = _adapter(handler).review(_request())

    assert seen["url"] == "https://api.example.com/anthropic/v1/messages"
    assert seen["x-api-key"] == "secret-key-123"
    assert seen["version"]
    assert seen["body"]["model"] == "claude-x"
    assert response.provider == "anthropic"
    assert len(response.findings) == 1
    assert response.findings[0]["category"] == "capacity"


def test_tolerates_markdown_fenced_json():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = [{
            "category": "capacity", "severity": "low", "title": "t",
            "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
        }]
        return _anthropic_reply("好的，结果如下：\n```json\n" + json.dumps(payload) + "\n```\n")

    response = _adapter(handler).review(_request())
    assert len(response.findings) == 1


def test_fabricated_span_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = [{
            "category": "capacity", "severity": "low", "title": "t",
            "description": "d", "suggestion": "s", "evidence_span_ids": ["NOT-A-REAL-SPAN"],
        }]
        return _anthropic_reply(json.dumps(payload))

    with pytest.raises(LLMProviderError):
        _adapter(handler).review(_request())


def test_unparseable_output_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_reply("抱歉，我无法完成。")

    with pytest.raises(LLMProviderError):
        _adapter(handler).review(_request())


def test_http_error_status_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LLMProviderError):
        _adapter(handler).review(_request())


def test_network_error_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(LLMProviderError):
        _adapter(handler).review(_request())


def test_api_key_never_appears_in_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(LLMProviderError) as exc:
        _adapter(handler).review(_request())
    assert "secret-key-123" not in str(exc.value)


def test_rejects_invalid_base_url_at_construction():
    with pytest.raises(Exception):
        AnthropicAdapter(base_url="ftp://bad", model="m", api_key="k")
