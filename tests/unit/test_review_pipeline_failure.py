from __future__ import annotations

from app.domain.enums import BlockType, OnMissing, PipelineStage, Severity
from app.domain.schemas import RuleDefinition, SourceSpan
from app.llm.provider import LLMRequest, LLMResponse
from app.parsers.docx_parser import ParsedDocument
from app.review.pipeline import ReviewPipeline


class InvalidEvidenceProvider:
    def __init__(self, findings: list[dict[str, object]]) -> None:
        self.findings = findings

    def review(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(provider="bad", model=request.model, findings=self.findings)


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


def test_invalid_llm_evidence_fails_and_stops_before_reconciliation() -> None:
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([finding(["not-supplied"])])
    )

    assert run.final_status == "FAILED"
    assert [record.stage for record in run.stage_records] == [
        PipelineStage.UPLOADED, PipelineStage.PARSED, PipelineStage.EXTRACTED,
        PipelineStage.NORMALIZED, PipelineStage.RULE_CHECKED, PipelineStage.LLM_REVIEWED,
        PipelineStage.FAILED,
    ]
    assert run.facts and run.rule_results
    assert run.findings == []
    assert run.stage_records[-2].status == "failed"
    assert "not-supplied" not in (run.stage_records[-2].error or "")


def test_empty_llm_evidence_fails_and_never_becomes_ready() -> None:
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([finding([])])
    )

    assert run.final_status == "FAILED"
    assert run.stage_records[-1].stage is PipelineStage.FAILED
    assert not any(record.stage is PipelineStage.READY_FOR_HUMAN_REVIEW for record in run.stage_records)
