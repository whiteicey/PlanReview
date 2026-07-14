from datetime import datetime, timezone

from app.domain.enums import (
    BlockType,
    ExtractionMethod,
    OnMissing,
    Origin,
    PipelineStage,
    RuleStatus,
    Severity,
)
from app.domain.schemas import (
    Finding,
    ParameterFact,
    RuleDefinition,
    RuleResult,
    StageRecord,
    SourceSpan,
)


def _fact(**overrides):
    values = dict(
        fact_id="f1",
        canonical_name="capacity",
        raw_name="Capacity",
        raw_value="36",
        normalized_value=36.0,
        raw_unit=None,
        canonical_unit=None,
        subject="all",
        time_scope="annual",
        statistical_scope="cumulative",
        condition=None,
        source_document="DEMO-001",
        source_version=None,
        source_span_id="s1",
        extraction_method=ExtractionMethod.TABLE,
    )
    values.update(overrides)
    return ParameterFact(**values)


def test_comparison_key_contains_all_five_dimensions_and_completeness():
    fact = _fact()
    assert fact.comparison_key() == (
        "capacity",
        "all",
        "annual",
        "cumulative",
        None,
    )
    assert fact.has_complete_key is True


def test_missing_required_key_dimension_marks_incomplete():
    assert _fact(subject=None).has_complete_key is False
    assert _fact(time_scope=None).has_complete_key is False
    assert _fact(statistical_scope=None).has_complete_key is False


def test_optional_condition_is_retained_in_comparison_key():
    fact = _fact(condition="peak")
    assert fact.comparison_key()[-1] == "peak"
    assert fact.has_complete_key is True


def test_source_span_has_no_page_number_field():
    span = SourceSpan(
        span_id="s1",
        document_id="DEMO-001",
        section_path=["Section A"],
        block_type=BlockType.TABLE_CELL,
        paragraph_index=None,
        table_index=0,
        row_index=1,
        column_index=1,
        char_start=None,
        char_end=None,
        text="36",
        text_hash="abc",
    )
    assert not hasattr(span, "page_number")
    assert span.block_type is BlockType.TABLE_CELL


def test_rule_definition_carries_operator_params():
    definition = RuleDefinition(
        rule_id="CONSISTENCY-001",
        version="0.1.0-demo",
        name="unit consistency",
        category="consistency",
        severity=Severity.HIGH,
        operator="all_equal",
        on_missing=OnMissing.UNKNOWN,
        params={"parameter": "capacity", "selectors": ["summary", "details"]},
    )
    assert definition.params["parameter"] == "capacity"
    assert definition.enabled is True
    assert definition.source_type == "DEMO_ONLY"


def test_stage_record_preserves_stage_timing_status_and_safe_error():
    started = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    record = StageRecord(
        stage=PipelineStage.RULE_CHECKED,
        started_at=started,
        ended_at=started,
        status="completed",
        exception_type=None,
        error="sanitized failure message",
    )

    assert record.stage is PipelineStage.RULE_CHECKED
    assert record.started_at == started
    assert record.ended_at == started
    assert record.status == "completed"
    assert record.error == "sanitized failure message"


def test_rule_result_and_finding_defaults():
    result = RuleResult(
        rule_id="CAPACITY-001",
        status=RuleStatus.FAIL,
        severity=Severity.HIGH,
        category="cross_domain",
        parameter="peak capacity",
        message="Values differ",
        evidence_span_ids=["s1", "s2"],
        involved_fact_ids=["f1", "f2"],
    )
    assert result.needs_human_review is False
    assert result.details == {}

    finding = Finding(
        finding_id="F1",
        origin=Origin.RULE,
        category="cross_domain",
        severity=Severity.HIGH,
        parameter="peak capacity",
        title="Peak capacity mismatch",
        description="...",
        suggestion="...",
        rule_id="CAPACITY-001",
        evidence_span_ids=["s1"],
        needs_human_review=True,
    )
    assert finding.review_status.value == "pending"
    assert finding.original_ai_snapshot == {}
