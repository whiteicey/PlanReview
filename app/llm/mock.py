"""Deterministic, data-only local provider used for development and tests."""

from __future__ import annotations

from app.llm.provider import LLMRequest, LLMResponse, validate_findings


class MockProvider:
    """Return a fixed capacity finding for an unambiguous keyword combination.

    The provider has no filesystem, subprocess, or network dependencies.  It
    scans only request text as data and never follows document-contained
    instructions, paths, URLs, or tool-like syntax.
    """

    def review(self, request: LLMRequest) -> LLMResponse:
        findings: list[dict[str, object]] = []
        if "高峰产量" in request.user_content and "超过处理能力" in request.user_content:
            findings.append(
                {
                    "category": "capacity",
                    "severity": "high",
                    "title": "高峰产量需复核",
                    "description": "Mock 检测到产量与处理能力关系需核实",
                    "suggestion": "核对口径并补充依据",
                    "evidence_span_ids": list(request.evidence_span_ids),
                }
            )
        return LLMResponse(
            provider="mock",
            model=request.model,
            findings=validate_findings(findings, request.evidence_span_ids),
        )
