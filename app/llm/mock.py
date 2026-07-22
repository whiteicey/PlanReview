"""Deterministic, data-only local provider used for development and tests."""

from __future__ import annotations

from app.llm.provider import LLMConnectionResult, LLMRequest, LLMResponse, validate_findings


class MockProvider:
    """A deterministic no-op provider used to verify the call chain."""

    provider_name = "mock"
    model_name = "mock"

    def test_connection(self) -> LLMConnectionResult:
        return LLMConnectionResult(provider="mock", model="mock", http_status=200)

    def review(self, request: LLMRequest) -> LLMResponse:
        findings: list[dict[str, object]] = []
        return LLMResponse(
            provider="mock",
            model=request.model,
            findings=validate_findings(findings, request.evidence_span_ids),
        )
