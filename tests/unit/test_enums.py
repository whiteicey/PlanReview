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


def test_pipeline_stage_covers_all_required_states():
    assert {stage.name for stage in PipelineStage} == {
        "CREATED",
        "VALIDATING_FILES",
        "PAIRING_FILES",
        "PARSING",
        "BUILDING_SPANS",
        "EXTRACTING_PARAMETERS",
        "NORMALIZING_FACTS",
        "RUNNING_RULES",
        "RETRIEVING_KNOWLEDGE",
        "CALLING_MODEL",
        "VALIDATING_MODEL_OUTPUT",
        "MERGING_FINDINGS",
        "WAITING_HUMAN_REVIEW",
        "COMPLETED",
        "FAILED",
    }


def test_enum_str_roundtrip():
    assert Severity("high") is Severity.HIGH
    assert Origin("rule") is Origin.RULE
    assert ReviewStatus("pending") is ReviewStatus.PENDING
    assert DiffKind("unknown_scope") is DiffKind.UNKNOWN_SCOPE
    assert BlockType("heading") is BlockType.HEADING
    assert ExtractionMethod("table") is ExtractionMethod.TABLE
