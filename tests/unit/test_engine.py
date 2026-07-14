from __future__ import annotations

from app.domain.enums import BlockType, OnMissing, RuleStatus, Severity
from app.domain.schemas import RuleDefinition, SourceSpan
from app.rules.engine import RuleEngine


def rule(
    rule_id: str,
    *,
    enabled: bool = True,
    on_missing: OnMissing = OnMissing.FAIL,
    params: dict[str, object] | None = None,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        version="0.1",
        name="required",
        category="version-change" if rule_id == "VERSION-001" else "c",
        severity=Severity.LOW,
        operator="required_sections_exist",
        on_missing=on_missing,
        enabled=enabled,
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


def test_engine_marks_version_failures_for_human_review_and_skips_disabled_rules() -> None:
    version, = RuleEngine().evaluate(
        [rule("VERSION-001"), rule("DISABLED", enabled=False)], [], [span()]
    )

    assert version.status is RuleStatus.FAIL
    assert version.needs_human_review is True
    assert version.evidence_span_ids == ["s"]
    assert version.details == {"missing": ["不存在"]}
