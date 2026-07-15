from __future__ import annotations

from pathlib import Path

from app.domain.enums import BlockType, OnMissing, RuleStatus, Severity
from app.domain.schemas import RuleDefinition, SourceSpan
from app.rules.engine import RuleEngine

ENGINE_SOURCE = Path(__file__).resolve().parents[2] / "app" / "rules" / "engine.py"


def rule(
    rule_id: str,
    *,
    enabled: bool = True,
    on_missing: OnMissing = OnMissing.FAIL,
    requires_human_review: bool = False,
    params: dict[str, object] | None = None,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        version="0.1",
        name="required",
        category="c",
        severity=Severity.LOW,
        operator="required_sections_exist",
        on_missing=on_missing,
        enabled=enabled,
        requires_human_review=requires_human_review,
        params=params if params is not None else {"required_sections": ["不存在"]},
    )


def span() -> SourceSpan:
    return SourceSpan(
        span_id="s",
        document_id="D",
        block_type=BlockType.PARAGRAPH,
        text="x",
        text_hash="h",
    )


def test_engine_converts_operator_outcome_to_full_result() -> None:
    result = RuleEngine().evaluate([rule("R1")], [], [span()])[0]

    assert result.status is RuleStatus.FAIL
    assert result.rule_id == "R1"
    assert result.severity is Severity.LOW
    assert result.category == "c"
    assert result.message == "缺少章节"
    assert result.evidence_span_ids == ["s"]
    assert result.involved_fact_ids == []
    assert result.details == {"missing": ["不存在"]}
    assert result.needs_human_review is False


def test_engine_deep_copies_nested_result_details() -> None:
    result = RuleEngine().evaluate([rule("R1")], [], [span()])[0]

    result.details["missing"].append("mutated")
    rerun = RuleEngine().evaluate([rule("R1")], [], [span()])[0]

    assert rerun.details == {"missing": ["不存在"]}


def test_engine_applies_unknown_missing_policies_without_fabricating_pass() -> None:
    unknown_rule = rule(
        "UNKNOWN",
        on_missing=OnMissing.UNKNOWN,
        params={},
    )
    fail_rule = rule("FAIL", on_missing=OnMissing.FAIL, params={})
    block_rule = rule("BLOCK", on_missing=OnMissing.BLOCK, params={})

    unknown, failed, blocked = RuleEngine().evaluate(
        [unknown_rule, fail_rule, block_rule], [], [span()]
    )

    assert unknown.status is RuleStatus.UNKNOWN
    assert unknown.needs_human_review is False
    assert failed.status is RuleStatus.FAIL
    assert failed.needs_human_review is True
    assert blocked.status is RuleStatus.UNKNOWN
    assert blocked.details["blocked"] is True
    assert blocked.needs_human_review is True
    assert all(result.status is not RuleStatus.PASS for result in (unknown, failed, blocked))


def test_requires_human_review_field_flags_fail_results() -> None:
    flagged = rule("R-FAIL", on_missing=OnMissing.FAIL, requires_human_review=True)

    result = RuleEngine().evaluate([flagged], [], [span()])[0]

    assert result.status is RuleStatus.FAIL
    assert result.needs_human_review is True


def test_requires_human_review_field_flags_unknown_results() -> None:
    flagged = rule(
        "R-UNKNOWN",
        on_missing=OnMissing.UNKNOWN,
        requires_human_review=True,
        params={},
    )

    result = RuleEngine().evaluate([flagged], [], [span()])[0]

    assert result.status is RuleStatus.UNKNOWN
    assert result.needs_human_review is True


def test_unknown_status_alone_does_not_require_human_review() -> None:
    plain = rule("R-PLAIN", on_missing=OnMissing.UNKNOWN, params={})

    result = RuleEngine().evaluate([plain], [], [span()])[0]

    assert result.status is RuleStatus.UNKNOWN
    assert result.needs_human_review is False


def test_engine_fans_out_generically_on_parameters_list() -> None:
    multi = RuleDefinition(
        rule_id="MULTI",
        version="0.1",
        name="version reason",
        category="version_change",
        severity=Severity.MEDIUM,
        operator="change_requires_reason",
        on_missing=OnMissing.UNKNOWN,
        params={"parameters": ["建设周期", "首次投产时间"]},
    )

    results = RuleEngine().evaluate([multi], [], [span()])

    assert len(results) == 2
    assert [result.parameter for result in results] == ["建设周期", "首次投产时间"]
    assert all(result.rule_id == "MULTI" for result in results)


def test_evidence_gate_escalates_unknown_to_fail_with_human_review() -> None:
    version_rule = RuleDefinition(
        rule_id="VERSION-001",
        version="0.1",
        name="version reason",
        category="version_change",
        severity=Severity.HIGH,
        operator="change_requires_reason",
        on_missing=OnMissing.FAIL,
        params={"parameter": "建设周期"},
    )

    result = RuleEngine().evaluate([version_rule], [], [])[0]

    assert result.status is RuleStatus.FAIL
    assert result.needs_human_review is True
    assert result.message
    assert result.details == {}


def test_engine_skips_disabled_rules() -> None:
    results = RuleEngine().evaluate(
        [rule("R1"), rule("DISABLED", enabled=False)], [], [span()]
    )

    assert len(results) == 1
    assert results[0].rule_id == "R1"


def test_engine_source_has_no_rule_id_or_legacy_special_casing() -> None:
    source = ENGINE_SOURCE.read_text(encoding="utf-8")

    assert "legacy_" not in source
    assert "rule_id ==" not in source
    assert "VERSION-001" not in source
