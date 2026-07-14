from app.domain.enums import (
    RuleStatus, ReviewStatus, Severity, Origin, OnMissing, PipelineStage,
    DiffKind, BlockType, ExtractionMethod,
)


def test_rule_status_is_three_valued_uppercase():
    assert {s.value for s in RuleStatus} == {"PASS", "FAIL", "UNKNOWN"}
    assert not hasattr(RuleStatus, "SUSPECTED")
    assert not hasattr(RuleStatus, "BLOCK")


def test_on_missing_values():
    assert OnMissing("unknown") is OnMissing.UNKNOWN
    assert OnMissing("fail") is OnMissing.FAIL
    assert OnMissing("block") is OnMissing.BLOCK


def test_pipeline_stage_covers_the_review_lifecycle():
    assert {stage.name for stage in PipelineStage} == {
        "UPLOADED",
        "PARSED",
        "EXTRACTED",
        "NORMALIZED",
        "RULE_CHECKED",
        "LLM_REVIEWED",
        "RECONCILED",
        "READY_FOR_HUMAN_REVIEW",
        "FAILED",
    }


def test_enum_str_roundtrip():
    assert Severity("high") is Severity.HIGH
    assert Origin("rule") is Origin.RULE
    assert ReviewStatus("pending") is ReviewStatus.PENDING
    assert DiffKind("unknown_scope") is DiffKind.UNKNOWN_SCOPE
    assert BlockType("heading") is BlockType.HEADING
    assert ExtractionMethod("table") is ExtractionMethod.TABLE
