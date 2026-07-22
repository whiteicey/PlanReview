from __future__ import annotations

from app.domain.enums import BlockType, ExtractionMethod, LLMStatus, RuleStatus, Severity
from app.domain.schemas import ParameterFact, RuleResult, SourceSpan
from app.llm.factory import LLMConfig, build_provider
from app.llm.limits import (
    MAX_LLM_EVIDENCE_IDS,
    MAX_LLM_SINGLE_SPAN_CHARACTERS,
    MAX_LLM_SPANS,
    MAX_LLM_TOTAL_CHARACTERS,
)
from app.llm.provider import LLMProviderError, LLMRequest, LLMResponse, LLMValidationError
from app.parsers.docx_parser import ParsedDocument
from app.review.pipeline import ReviewPipeline, select_llm_evidence


class CaptureProvider:
    provider_name = "capture"
    model_name = "capture-model"

    def __init__(self, finding_evidence: str | None = None):
        self.requests: list[LLMRequest] = []
        self.finding_evidence = finding_evidence

    def review(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        findings = []
        if self.finding_evidence is not None:
            findings.append({
                "category": "evidence",
                "severity": "low",
                "title": "bounded",
                "description": "bounded",
                "suggestion": "review",
                "evidence_span_ids": [self.finding_evidence],
            })
        return LLMResponse(provider=self.provider_name, model=self.model_name, findings=findings)


def _document(texts: list[str]) -> ParsedDocument:
    spans = [
        SourceSpan(
            span_id=f"s-{index:03d}",
            document_id="D",
            block_type=BlockType.PARAGRAPH,
            paragraph_index=index,
            text=value,
            text_hash=f"hash-{index}",
        )
        for index, value in enumerate(texts)
    ]
    return ParsedDocument("D", "case.docx", spans, spans, [])


def test_llm_limits_are_fixed_and_partial_request_is_deterministic():
    assert (MAX_LLM_SPANS, MAX_LLM_TOTAL_CHARACTERS) == (40, 24_000)
    assert (MAX_LLM_SINGLE_SPAN_CHARACTERS, MAX_LLM_EVIDENCE_IDS) == (4_000, 40)
    provider = CaptureProvider()
    run = ReviewPipeline().run("CASE-limit", [_document(["甲" * 5_000] * 21)], [], provider)

    assert run.llm_status is LLMStatus.COMPLETED_PARTIAL
    assert run.validation_reason_code is None
    assert (run.candidate_count, run.valid_count, run.rejected_count) == (0, 0, 0)
    assert run.coverage_ratio is not None and 0 <= run.coverage_ratio < 1
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert len(request.evidence_span_ids) <= 40
    assert len(request.user_content) <= 24_000
    assert request.evidence_span_ids == [f"s-{index:03d}" for index in range(len(request.evidence_span_ids))]
    first_block = request.user_content.split("\n\n", 1)[0]
    assert len(first_block.split("\n", 1)[1]) == 4_000

    count_provider = CaptureProvider()
    count_run = ReviewPipeline().run(
        "CASE-count-limit", [_document(["x"] * 41)], [], count_provider
    )
    assert count_run.llm_status is LLMStatus.COMPLETED_PARTIAL
    assert len(count_provider.requests[0].evidence_span_ids) == 40


def test_full_request_is_completed_and_empty_input_does_not_call_provider():
    provider = CaptureProvider()
    complete = ReviewPipeline().run("CASE-full", [_document(["one", "two"])], [], provider)
    assert complete.llm_status is LLMStatus.COMPLETED
    assert complete.validation_reason_code is None
    assert (complete.candidate_count, complete.valid_count, complete.rejected_count) == (0, 0, 0)
    assert complete.coverage_ratio == 1
    assert provider.requests[0].user_content == "[s-000]\none\n\n[s-001]\ntwo"

    empty_provider = CaptureProvider()
    empty = ReviewPipeline().run("CASE-empty", [_document(["", ""])], [], empty_provider)
    assert empty.llm_status is LLMStatus.INPUT_LIMIT_EXCEEDED
    assert empty.final_status == "READY_FOR_HUMAN_REVIEW"
    assert empty_provider.requests == []


def test_llm_finding_cannot_reference_a_span_omitted_by_limits():
    omitted = "s-040"
    provider = CaptureProvider(omitted)
    run = ReviewPipeline().run("CASE-evidence", [_document(["x"] * 41)], [], provider)
    assert omitted not in provider.requests[0].evidence_span_ids
    assert run.llm_status is LLMStatus.VALIDATION_FAILED
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.findings == []


def test_configuration_error_provider_keeps_rules_path_ready_without_mock_fallback():
    provider = build_provider(
        LLMConfig(provider="anthropic", base_url="https://api.example.com", model=None),
        api_key=None,
    )
    run = ReviewPipeline().run("CASE-config", [_document(["safe"])], [], provider)
    assert provider.provider_name == "anthropic"
    assert run.llm_status is LLMStatus.CONFIGURATION_ERROR
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_error_summary == "LLM configuration is incomplete or unavailable"


def test_evidence_selection_prioritizes_fail_unknown_facts_then_context():
    parsed = _document([f"evidence-{index}" for index in range(50)])
    results = [
        RuleResult(
            rule_id="FAIL-RULE", status=RuleStatus.FAIL, severity=Severity.HIGH,
            category="capacity", evidence_span_ids=["s-030"],
        ),
        RuleResult(
            rule_id="UNKNOWN-RULE", status=RuleStatus.UNKNOWN, severity=Severity.MEDIUM,
            category="evidence", evidence_span_ids=["s-020"],
        ),
        RuleResult(
            rule_id="PASS-RULE", status=RuleStatus.PASS, severity=Severity.LOW,
            category="evidence", evidence_span_ids=["s-040"],
        ),
    ]
    facts = [ParameterFact(
        fact_id="F1", canonical_name="peak", raw_name="peak", raw_value="1",
        source_document="D", source_span_id="s-010",
        extraction_method=ExtractionMethod.REGEX,
    )]

    selection = select_llm_evidence(parsed.spans, results, facts)

    assert selection.original_count == 50
    assert selection.selected_ids[:3] == ["s-030", "s-020", "s-010"]
    assert "s-040" not in selection.selected_ids
    assert {"s-029", "s-031", "s-019", "s-021", "s-009", "s-011"}.issubset(selection.selected_ids)
    assert len(selection.selected_ids) <= 40
    assert selection.selected_character_count <= 24_000
    assert selection.partial is True


class _RaisingProvider:
    provider_name = "anthropic"
    model_name = "deepseek-v4-pro"

    def __init__(self, error):
        self.error = error

    def review(self, request):
        raise self.error


def test_partial_coverage_never_overrides_provider_or_validation_failure_status():
    partial_document = _document(["x"] * 41)
    provider_failed = ReviewPipeline().run(
        "CASE-provider-priority", [partial_document], [],
        _RaisingProvider(LLMProviderError("timeout")),
    )
    assert provider_failed.llm_status is LLMStatus.PROVIDER_ERROR
    assert provider_failed.validation_reason_code is None
    assert provider_failed.candidate_count is None

    validation_failed = ReviewPipeline().run(
        "CASE-validation-priority", [partial_document], [],
        _RaisingProvider(LLMValidationError("truncated_json", stop_reason="max_tokens")),
    )
    assert validation_failed.llm_status is LLMStatus.VALIDATION_FAILED
    assert validation_failed.validation_reason_code == "truncated_json"
    assert validation_failed.candidate_count is None
