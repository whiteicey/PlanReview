from __future__ import annotations

from app.domain.enums import BlockType, OnMissing, PipelineStage, Severity
from app.domain.schemas import RuleDefinition, SourceSpan
from app.llm.mock import MockProvider
from app.parsers.docx_parser import ParsedDocument
from app.review.pipeline import ReviewPipeline


def document() -> ParsedDocument:
    span = SourceSpan(
        span_id="s1",
        document_id="D",
        block_type=BlockType.PARAGRAPH,
        text="高峰产量：220万m³/d，超过处理能力。",
        text_hash="hash",
    )
    return ParsedDocument(
        document_id="D",
        file_name="case.docx",
        spans=[span],
        paragraphs=[span],
        table_cells=[],
    )


def rule() -> RuleDefinition:
    return RuleDefinition(
        rule_id="R1",
        version="1",
        name="required section",
        category="capacity",
        severity=Severity.HIGH,
        operator="required_sections_exist",
        on_missing=OnMissing.FAIL,
        params={"required_sections": ["不存在"], "parameter": "高峰产量"},
    )


def test_pipeline_retains_facts_and_reaches_human_review_only_after_success() -> None:
    run = ReviewPipeline().run("case-1", [document()], [rule()], MockProvider())

    assert run.case_id == "case-1"
    assert len(run.facts) == 1
    assert run.facts[0].source_span_id == "s1"
    assert run.rule_results[0].rule_id == "R1"
    assert len(run.findings) == 1
    # This is a missing-section result: it deliberately has no fabricated
    # document-wide evidence fallback.
    assert all(finding.evidence_span_ids == [] for finding in run.findings)
    assert [record.stage for record in run.stage_records] == [
        PipelineStage.UPLOADED,
        PipelineStage.PARSED,
        PipelineStage.EXTRACTED,
        PipelineStage.NORMALIZED,
        PipelineStage.RULE_CHECKED,
        PipelineStage.LLM_REVIEWED,
        PipelineStage.RECONCILED,
        PipelineStage.READY_FOR_HUMAN_REVIEW,
    ]
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"


class _FailingProvider:
    provider_name = "anthropic"
    model_name = "safe-model"

    def review(self, request):  # noqa: ANN001, ANN201 - test double
        from app.llm.provider import LLMProviderError

        raise LLMProviderError("LLM 请求失败：ConnectError")


def test_pipeline_tolerates_online_llm_failure_and_keeps_rule_findings() -> None:
    run = ReviewPipeline().run("case-2", [document()], [rule()], _FailingProvider())

    # The review still completes and the rule finding survives; only the LLM
    # contribution is skipped (fail-closed, never a silent crash or lost rules).
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.rule_results[0].rule_id == "R1"
    assert any(finding.rule_id == "R1" for finding in run.findings)
    assert all(finding.origin.value != "llm" for finding in run.findings)
    assert run.llm_review_error == "AI 服务调用失败，本次仅保留确定性规则结果"
    assert run.llm_status.value == "PROVIDER_ERROR"
    assert run.llm_provider == "anthropic"
    assert run.llm_model == "safe-model"
    assert run.llm_finding_count == 0
    assert run.llm_error_summary == "AI 服务调用失败，本次仅保留确定性规则结果"
    stages = [record.stage for record in run.stage_records]
    assert PipelineStage.RECONCILED in stages
    assert PipelineStage.READY_FOR_HUMAN_REVIEW in stages
