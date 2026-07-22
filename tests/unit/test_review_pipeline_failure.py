from __future__ import annotations

from app.domain.enums import BlockType, OnMissing, PipelineStage, Severity
from app.domain.schemas import RuleDefinition, SourceSpan
from app.llm.provider import LLMProviderError, LLMRequest, LLMResponse, LLMValidationError
from app.parsers.docx_parser import ParsedDocument
from app.review.pipeline import ReviewPipeline


class InvalidEvidenceProvider:
    def __init__(self, findings: list[dict[str, object]]) -> None:
        self.findings = findings

    def review(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(provider="bad", model=request.model, findings=self.findings)


class RaisingValidationProvider:
    provider_name = "anthropic"
    model_name = "deepseek-v4-pro"

    def __init__(self, category: str) -> None:
        self.category = category

    def review(self, request: LLMRequest) -> LLMResponse:
        raise LLMValidationError(
            "invalid_evidence" if self.category == "evidence_reference" else "invalid_json",
            validation_category=self.category,
            candidate_count=1,
            valid_count=0,
            rejected_count=1,
        )


class RetryOnceProvider:
    provider_name = "anthropic"
    model_name = "deepseek-v4-pro"

    def __init__(self, *, fail_twice: bool = False) -> None:
        self.calls = 0
        self.fail_twice = fail_twice

    def review(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1 or self.fail_twice:
            raise LLMProviderError(
                "safe timeout",
                reason_code="timeout",
                retryable=True,
            )
        return LLMResponse(provider="anthropic", model=request.model, findings=[])


def document() -> ParsedDocument:
    span = SourceSpan(
        span_id="s1", document_id="D", block_type=BlockType.PARAGRAPH,
        text="高峰产量：220万m³/d", text_hash="hash",
    )
    return ParsedDocument("D", "case.docx", [span], [span], [])


def rule() -> RuleDefinition:
    return RuleDefinition(
        rule_id="R1", version="1", name="required section", category="capacity",
        severity=Severity.HIGH, operator="required_sections_exist", on_missing=OnMissing.FAIL,
        params={"required_sections": ["不存在"]},
    )


def finding(evidence: list[str]) -> dict[str, object]:
    return {
        "category": "capacity", "severity": "high", "title": "bad evidence",
        "description": "bad", "suggestion": "bad", "evidence_span_ids": evidence,
    }


def test_invalid_llm_evidence_isolated_and_rule_findings_are_reconciled() -> None:
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([finding(["not-supplied"])])
    )

    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert [record.stage for record in run.stage_records] == [
        PipelineStage.UPLOADED, PipelineStage.PARSED, PipelineStage.EXTRACTED,
        PipelineStage.NORMALIZED, PipelineStage.RULE_CHECKED, PipelineStage.LLM_REVIEWED,
        PipelineStage.RECONCILED, PipelineStage.READY_FOR_HUMAN_REVIEW,
    ]
    assert run.facts and run.rule_results
    assert run.findings
    assert all(finding.origin.value == "rule" for finding in run.findings)
    assert run.llm_status.value == "VALIDATION_FAILED"
    assert run.llm_finding_count == 0
    assert run.stage_records[-3].stage is PipelineStage.LLM_REVIEWED
    assert run.stage_records[-3].status == "completed"
    assert "not-supplied" not in (run.llm_error_summary or "")


def test_empty_llm_evidence_is_validation_failure_without_invalid_finding() -> None:
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([finding([])])
    )

    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_status.value == "VALIDATION_FAILED"
    assert run.findings and all(item.origin.value == "rule" for item in run.findings)
    assert any(record.stage is PipelineStage.READY_FOR_HUMAN_REVIEW for record in run.stage_records)


def test_oversized_llm_finding_text_is_validation_failure_and_keeps_rules() -> None:
    oversized = finding(["s1"]) | {"description": "x" * 4001}
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([oversized])
    )

    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_status.value == "VALIDATION_FAILED"
    assert run.llm_finding_count == 0
    assert run.findings and all(item.origin.value == "rule" for item in run.findings)


def test_oversized_llm_finding_title_is_validation_failure_and_keeps_rules() -> None:
    oversized = finding(["s1"]) | {"title": "x" * 201}
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([oversized])
    )

    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_status.value == "VALIDATION_FAILED"
    assert run.llm_finding_count == 0
    assert run.findings and all(item.origin.value == "rule" for item in run.findings)


def test_adapter_validation_categories_are_safely_mapped_and_keep_rule_findings() -> None:
    for category, expected_message in [
        ("output_format", "AI 已返回内容，但未通过结构化格式校验"),
        ("evidence_reference", "AI 结果引用证据不符合要求，相关结果已丢弃"),
    ]:
        events = []
        run = ReviewPipeline().run(
            f"case-{category}", [document()], [rule()], RaisingValidationProvider(category),
            progress=lambda *args: events.append(args),
        )
        assert run.llm_status.value == "VALIDATION_FAILED"
        assert run.final_status == "READY_FOR_HUMAN_REVIEW"
        assert run.findings and all(item.origin.value == "rule" for item in run.findings)
        assert expected_message in [item[3] for item in events]
        serialized = repr(events)
        assert "unsafe provider detail" not in serialized


def test_retryable_provider_failure_is_recorded_once_then_success_keeps_rules() -> None:
    provider = RetryOnceProvider()
    events = []
    run = ReviewPipeline().run(
        "case-retry-success", [document()], [rule()], provider,
        progress=lambda *args: events.append(args),
    )

    assert provider.calls == 2
    assert run.llm_status.value == "COMPLETED"
    assert run.findings and all(item.origin.value == "rule" for item in run.findings)
    retry_events = [item for item in events if item[1] == "LLM_RETRY_SCHEDULED"]
    assert len(retry_events) == 1
    assert retry_events[0][4] == {"retry_attempt": 1, "provider_error_code": "timeout"}


def test_retryable_provider_failure_is_never_called_more_than_twice() -> None:
    provider = RetryOnceProvider(fail_twice=True)
    events = []
    run = ReviewPipeline().run(
        "case-retry-failed", [document()], [rule()], provider,
        progress=lambda *args: events.append(args),
    )

    assert provider.calls == 2
    assert run.llm_status.value == "PROVIDER_ERROR"
    assert run.llm_error_summary == "AI 服务调用失败，本次仅保留确定性规则结果"
    assert run.findings and all(item.origin.value == "rule" for item in run.findings)
    messages = [item[3] for item in events]
    assert "AI 服务调用出现可重试错误，已记录首次失败并执行一次受控重试" in messages
    assert "AI 服务调用失败，本次仅保留确定性规则结果" in messages


def test_validation_failure_is_not_retried() -> None:
    provider = RaisingValidationProvider("output_format")
    events = []
    run = ReviewPipeline().run(
        "case-no-validation-retry", [document()], [rule()], provider,
        progress=lambda *args: events.append(args),
    )
    assert run.llm_status.value == "VALIDATION_FAILED"
    assert not any(item[1] == "LLM_RETRY_SCHEDULED" for item in events)
