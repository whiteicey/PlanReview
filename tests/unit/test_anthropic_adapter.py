from __future__ import annotations

import json

import httpx
import pytest

from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.provider import LLMProviderError, LLMRequest, LLMValidationError


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
    assert seen["body"]["max_tokens"] == 16384
    assert seen["body"]["temperature"] == 0.0
    system = seen["body"]["system"]
    assert "最多返回 8 条" in system
    assert "unknown_scope" in system and "severity 只能是 high/medium/low" in system
    assert "<think>" in system
    assert response.provider == "anthropic"
    assert len(response.findings) == 1
    assert response.findings[0]["category"] == "capacity"


def test_default_timeout_uses_explicit_connect_read_write_and_pool_limits():
    adapter = _adapter(lambda request: _anthropic_reply("[]"))

    assert adapter._timeout.connect == 15.0
    assert adapter._timeout.read == 120.0
    assert adapter._timeout.write == 30.0
    assert adapter._timeout.pool == 30.0


@pytest.mark.parametrize("fence", ["```json", "```"])
def test_tolerates_markdown_fenced_json(fence):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = [{
            "category": "capacity", "severity": "low", "title": "t",
            "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
        }]
        return _anthropic_reply("好的，结果如下：\n" + fence + "\n" + json.dumps(payload) + "\n```\n")

    response = _adapter(handler).review(_request())
    assert len(response.findings) == 1


@pytest.mark.parametrize(
    "wrapper",
    [
        lambda payload: "以下是结果：\n" + payload,
        lambda payload: payload + "\n以上为复核结果。",
    ],
)
def test_extracts_json_array_with_small_explanation(wrapper):
    payload = json.dumps([{
        "category": "capacity", "severity": "low", "title": "t",
        "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
    }])

    response = _adapter(lambda request: _anthropic_reply(wrapper(payload))).review(_request())
    assert len(response.findings) == 1


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('[{"category": "capacity"}', "truncated_json"),
        ("[{'category': 'capacity'}]", "invalid_json"),
        ('{"category": "capacity"}', "root_not_array"),
        ('{"results": []}', "root_not_array"),
        ("[] and []", "multiple_arrays"),
    ],
)
def test_rejects_truncated_pseudo_object_and_multiple_array_outputs(text, reason):
    with pytest.raises(LLMValidationError) as exc:
        _adapter(lambda request: _anthropic_reply(text)).review(_request())
    assert exc.value.category == "output_format"
    assert exc.value.reason_code == reason
    assert exc.value.candidate_count is None
    assert exc.value.valid_count is None
    assert exc.value.rejected_count is None


def test_fabricated_span_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = [{
            "category": "capacity", "severity": "low", "title": "t",
            "description": "d", "suggestion": "s", "evidence_span_ids": ["NOT-A-REAL-SPAN"],
        }]
        return _anthropic_reply(json.dumps(payload))

    with pytest.raises(LLMValidationError) as exc:
        _adapter(handler).review(_request())
    assert exc.value.category == "evidence_reference"
    assert exc.value.reason_code == "invalid_evidence"
    assert (exc.value.candidate_count, exc.value.valid_count, exc.value.rejected_count) == (1, 0, 1)


def test_invalid_field_is_output_format_validation_failure():
    payload = [{
        "category": "capacity", "severity": "critical", "title": "t",
        "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
    }]
    with pytest.raises(LLMValidationError) as exc:
        _adapter(lambda request: _anthropic_reply(json.dumps(payload))).review(_request())
    assert exc.value.category == "output_format"
    assert exc.value.reason_code == "invalid_severity"


def test_unparseable_output_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_reply("抱歉，我无法完成。")

    with pytest.raises(LLMValidationError) as exc:
        _adapter(handler).review(_request())
    assert exc.value.category == "output_format"


def test_http_error_status_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LLMProviderError) as exc:
        _adapter(handler).review(_request())
    assert exc.value.reason_code == "http_error"
    assert exc.value.retryable is False


def test_network_error_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(LLMProviderError) as exc:
        _adapter(handler).review(_request())
    assert exc.value.reason_code == "transport_error"
    assert exc.value.retryable is True


def test_network_timeout_remains_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMProviderError) as exc:
        _adapter(handler).review(_request())
    assert exc.value.reason_code == "timeout"
    assert exc.value.retryable is True


def test_strips_one_complete_long_leading_think_block_without_exposing_it():
    payload = [{
        "category": "capacity", "severity": "low", "title": "t",
        "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
    }]
    text = "<think>" + ("private-reasoning " * 80) + "</think>\n" + json.dumps(payload)
    response = _adapter(lambda request: _anthropic_reply(text)).review(_request())
    assert len(response.findings) == 1


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("<think>unfinished", "unclosed_think"),
        ("<think>complete but no result</think>", "no_text"),
        ("<think>one</think><think>two</think>[]", "no_complete_array"),
        ('<think>done</think>[{"category":"capacity"}', "truncated_json"),
        ("没有结构化数组", "no_complete_array"),
        (("说明" * 251) + "[]", "explanation_too_long"),
    ],
)
def test_think_and_array_extraction_failures_have_safe_reason_codes(text, reason):
    with pytest.raises(LLMValidationError) as exc:
        _adapter(lambda request: _anthropic_reply(text)).review(_request())
    assert exc.value.reason_code == reason
    assert exc.value.candidate_count is None
    assert "private" not in str(exc.value)


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"category": "not-allowed"}, "invalid_category"),
        ({"severity": "critical"}, "invalid_severity"),
        ({"title": None}, "missing_field"),
    ],
)
def test_parsed_array_field_failures_reject_the_whole_batch(update, reason):
    finding = {
        "category": "capacity", "severity": "low", "title": "t",
        "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
    } | update
    with pytest.raises(LLMValidationError) as exc:
        _adapter(lambda request: _anthropic_reply(json.dumps([finding]))).review(_request())
    assert exc.value.reason_code == reason
    assert (exc.value.candidate_count, exc.value.valid_count, exc.value.rejected_count) == (1, 0, 1)


def test_formal_empty_array_is_valid_with_zero_candidates():
    response = _adapter(lambda request: _anthropic_reply("[]")).review(_request())
    assert response.findings == []


def test_success_response_exposes_only_safe_metadata():
    response = _adapter(lambda request: httpx.Response(200, json={
        "content": [{"type": "text", "text": "[]"}], "stop_reason": "end_turn",
    })).review(_request())
    assert response.http_status == 200
    assert response.response_character_count == 2
    assert response.stop_reason == "end_turn"
    assert response.content_block_count == 1


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (lambda request: httpx.Response(200, content=b"{"), "invalid_json"),
        (lambda request: httpx.Response(200, json={}), "envelope_missing_content"),
        (lambda request: httpx.Response(200, json={"content": []}), "no_text"),
    ],
)
def test_response_envelope_failures_are_validation_errors_with_null_counts(handler, reason):
    with pytest.raises(LLMValidationError) as exc:
        _adapter(handler).review(_request())
    assert exc.value.reason_code == reason
    assert exc.value.candidate_count is None
    assert exc.value.http_status == 200


def test_more_than_eight_findings_is_rejected_after_array_parsing():
    finding = {
        "category": "capacity", "severity": "low", "title": "t",
        "description": "d", "suggestion": "s", "evidence_span_ids": ["D:p:0"],
    }
    with pytest.raises(LLMValidationError) as exc:
        _adapter(lambda request: _anthropic_reply(json.dumps([finding] * 9))).review(_request())
    assert exc.value.reason_code == "too_many_findings"
    assert (exc.value.candidate_count, exc.value.valid_count, exc.value.rejected_count) == (9, 0, 9)


def test_api_key_never_appears_in_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(LLMProviderError) as exc:
        _adapter(handler).review(_request())
    assert "secret-key-123" not in str(exc.value)


def test_rejects_invalid_base_url_at_construction():
    with pytest.raises(Exception):
        AnthropicAdapter(base_url="ftp://bad", model="m", api_key="k")


def test_adapter_rechecks_private_endpoint_mode():
    with pytest.raises(Exception):
        AnthropicAdapter(base_url="http://127.0.0.1:11434", model="m", api_key="k")
    adapter = AnthropicAdapter(
        base_url="http://127.0.0.1:11434", model="m", api_key="k",
        allow_private_endpoint=True,
    )
    assert adapter.model_name == "m"
