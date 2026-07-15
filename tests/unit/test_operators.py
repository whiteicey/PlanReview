from __future__ import annotations

import inspect

import pytest

from app.domain.enums import BlockType, ExtractionMethod, RuleStatus
from app.domain.exceptions import UnknownOperatorError
from app.domain.schemas import ParameterFact, SourceSpan
import app.rules.operators as operators
from app.rules.operators import OPERATOR_NAMES, OperatorContext, get_operator


def span(
    text: str,
    sid: str = "s1",
    section: str = "附件A关键参数表",
    block_type: BlockType = BlockType.PARAGRAPH,
) -> SourceSpan:
    return SourceSpan(
        span_id=sid,
        document_id="D",
        section_path=[section],
        block_type=block_type,
        text=text,
        text_hash="h",
    )


def fact(
    fid: str,
    name: str,
    value: float | None,
    *,
    subject: str | None = "全区",
    time_scope: str | None = "全生命周期",
    statistical_scope: str | None = "累计",
    condition: str | None = None,
    raw_name: str | None = None,
    span_id: str = "s1",
    canonical_name: str | None = None,
    source_version: str | None = None,
) -> ParameterFact:
    return ParameterFact(
        fact_id=fid,
        canonical_name=canonical_name or name,
        raw_name=raw_name or name,
        raw_value="" if value is None else str(value),
        normalized_value=value,
        subject=subject,
        time_scope=time_scope,
        statistical_scope=statistical_scope,
        condition=condition,
        source_document="D",
        source_version=source_version,
        source_span_id=span_id,
        extraction_method=ExtractionMethod.TABLE,
    )


def run(name: str, facts=(), spans=(), params=None):
    return get_operator(name)(OperatorContext(list(facts), list(spans)), params or {})


def test_operator_registry_is_exact_immutable_and_contains_no_eval() -> None:
    assert OPERATOR_NAMES == frozenset(
        {
            "required_sections_exist",
            "required_parameter_table_exists",
            "all_equal",
            "sum_equals",
            "product_approximately_equals",
            "less_or_equal",
            "change_requires_reason",
            "issue_response_status_exists",
            "alias_normalization",
            "evidence_required",
            "reply_table_status_complete",
            "prose_alias_unnormalized",
        }
    )
    assert isinstance(OPERATOR_NAMES, frozenset)
    assert "eval(" not in inspect.getsource(operators)
    with pytest.raises(UnknownOperatorError):
        get_operator("__import__")


def test_required_sections_exist_has_pass_fail_unknown_and_evidence() -> None:
    present = span("x", sid="section")
    assert run(
        "required_sections_exist", spans=[present], params={"required_sections": ["附件A"]}
    ).status is RuleStatus.PASS
    missing = run("required_sections_exist", spans=[], params={"required_sections": ["附件A"]})
    assert missing.status is RuleStatus.FAIL
    assert missing.details["missing"] == ["附件A"]
    assert run("required_sections_exist", spans=[present]).status is RuleStatus.UNKNOWN


def test_required_parameter_table_exists_has_pass_fail_unknown() -> None:
    table_cell = span(
        "36", sid="cell", section="附件A关键参数表", block_type=BlockType.TABLE_CELL
    )
    assert run(
        "required_parameter_table_exists",
        spans=[table_cell],
        params={"section_contains": "关键参数表"},
    ).status is RuleStatus.PASS
    assert run(
        "required_parameter_table_exists",
        spans=[span("x")],
        params={"section_contains": "关键参数表"},
    ).status is RuleStatus.FAIL
    assert run("required_parameter_table_exists", spans=[table_cell]).status is RuleStatus.UNKNOWN


def test_all_equal_has_pass_fail_unknown_and_never_compares_different_scopes() -> None:
    params = {"parameter": "开发井总数"}
    matching = [fact("a", "开发井总数", 36, span_id="a"), fact("b", "开发井总数", 36, span_id="b")]
    passed = run("all_equal", matching, params=params)
    assert passed.status is RuleStatus.PASS
    assert passed.evidence_span_ids == ["a", "b"]
    assert run("all_equal", [matching[0], fact("b", "开发井总数", 38, span_id="b")], params=params).status is RuleStatus.FAIL
    assert run("all_equal", [matching[0], fact("b", "开发井总数", 38, time_scope="达产期")], params=params).status is RuleStatus.UNKNOWN
    assert run("all_equal", [matching[0], fact("b", "开发井总数", 38, statistical_scope="日峰值")], params=params).status is RuleStatus.UNKNOWN
    assert run("all_equal", [matching[0], fact("b", "开发井总数", 38, subject="单区")], params=params).status is RuleStatus.UNKNOWN
    assert run("all_equal", [matching[0], fact("b", "开发井总数", 38, condition="峰值")], params=params).status is RuleStatus.UNKNOWN
    assert run("all_equal", [fact("a", "开发井总数", 36, time_scope=None), matching[1]], params=params).status is RuleStatus.UNKNOWN
    assert run("all_equal", matching, params={"parameter": "开发井总数", "match_dimensions": ["canonical_name", "subject", "time_scope", "statistical_scope", "condition"]}).status is RuleStatus.PASS
    assert run("all_equal", [matching[0], fact("b", "开发井总数", 36, condition="峰值")], params={"parameter": "开发井总数", "match_dimensions": ["canonical_name", "subject", "time_scope", "statistical_scope", "condition"]}).status is RuleStatus.UNKNOWN
    assert run("all_equal", matching, params={"parameter": "开发井总数", "match_dimensions": ["subject", "time_scope"]}).status is RuleStatus.UNKNOWN
    assert run("all_equal", matching, params={"parameter": "开发井总数", "match_dimensions": ["canonical_name", {"bad": "nested"}, "time_scope", "statistical_scope", "condition"]}).status is RuleStatus.UNKNOWN


def test_sum_equals_has_pass_fail_unknown_and_requires_one_shared_full_key() -> None:
    params = {"target": "总数", "components": ["甲", "乙"]}
    facts = [fact("t", "总数", 36, span_id="t"), fact("a", "甲", 30, span_id="a"), fact("b", "乙", 6, span_id="b")]
    assert run("sum_equals", facts, params=params).status is RuleStatus.PASS
    assert run("sum_equals", [facts[0], facts[1], fact("b", "乙", 7)], params=params).status is RuleStatus.FAIL
    assert run("sum_equals", [facts[0], facts[1], fact("b", "乙", 6, time_scope="达产期")], params=params).status is RuleStatus.UNKNOWN
    assert run("sum_equals", facts[:2], params=params).status is RuleStatus.UNKNOWN


def test_product_approximately_equals_has_pass_fail_unknown_and_scope_matching() -> None:
    params = {"left": ["井数", "单井产能"], "right": "总产能", "relative_tolerance": 0.05}
    facts = [fact("w", "井数", 36), fact("r", "单井产能", 5), fact("t", "总产能", 180)]
    assert run("product_approximately_equals", facts, params=params).status is RuleStatus.PASS
    assert run("product_approximately_equals", [facts[0], facts[1], fact("t", "总产能", 160)], params=params).status is RuleStatus.FAIL
    assert run("product_approximately_equals", [facts[0], facts[1], fact("t", "总产能", 180, subject="单井")], params=params).status is RuleStatus.UNKNOWN
    assert run("product_approximately_equals", facts[:2], params=params).status is RuleStatus.UNKNOWN


def test_less_or_equal_has_pass_fail_unknown_and_scope_matching() -> None:
    params = {"left": "高峰产量", "right": "处理能力"}
    left, right = fact("l", "高峰产量", 170), fact("r", "处理能力", 200)
    assert run("less_or_equal", [left, right], params=params).status is RuleStatus.PASS
    assert run("less_or_equal", [fact("l", "高峰产量", 220), right], params=params).status is RuleStatus.FAIL
    assert run("less_or_equal", [left, fact("r", "处理能力", 200, statistical_scope="日峰值")], params=params).status is RuleStatus.UNKNOWN
    assert run("less_or_equal", [left, fact("r", "处理能力", None)], params=params).status is RuleStatus.UNKNOWN


def test_change_requires_reason_has_pass_fail_unknown_and_scope_matching() -> None:
    params = {"parameter": "建设周期", "reason_terms": ["原因"]}
    old, new = fact("old", "建设周期", 24, span_id="old"), fact("new", "建设周期", 30, span_id="new")
    assert run("change_requires_reason", [old, new], params=params).status is RuleStatus.UNKNOWN
    old = fact("old", "建设周期", 24, span_id="old", source_version="v1")
    new = fact("new", "建设周期", 30, span_id="new", source_version="v2")
    assert run("change_requires_reason", [old, new], params=params).status is RuleStatus.FAIL
    outcome = run(
        "change_requires_reason",
        [old, new],
        [span("建设周期调整原因：地面条件变化", sid="reason", section="审查意见回复表")],
        params,
    )
    assert outcome.status is RuleStatus.PASS
    assert outcome.evidence_span_ids == ["old", "new", "reason"]
    assert run("change_requires_reason", [old, fact("new", "建设周期", 30, time_scope="达产期", source_version="v3")], params=params).status is RuleStatus.UNKNOWN
    assert run("change_requires_reason", [old, fact("new", "建设周期", 30, source_version="v1")], params=params).status is RuleStatus.UNKNOWN
    for phrase in ("无原因", "未说明原因", "未提供原因", "原因不明", "尚无原因"):
        failed = run("change_requires_reason", [old, fact("new", "建设周期", 30, span_id="new", source_version="v3")], [span(f"建设周期{phrase}，调整", sid="bad", section="审查意见回复表")], params)
        assert failed.status is RuleStatus.FAIL
        assert failed.evidence_span_ids == ["old", "new", "bad"]
    failed = run("change_requires_reason", [old, fact("new", "建设周期", 30, span_id="new", source_version="v3")], [span("建设周期尚未说明，调整", sid="scan", section="审查意见回复表")], params)
    assert failed.status is RuleStatus.FAIL
    assert failed.evidence_span_ids == ["old", "new", "scan"]
    assert run("change_requires_reason", [old, fact("new", "建设周期", 30, source_version="v3")], [span("建设周期调整原因：地面条件变化", sid="wrong", section="普通说明")], params).status is RuleStatus.FAIL
    assert run("change_requires_reason", [old], params=params).status is RuleStatus.UNKNOWN


def test_issue_response_status_exists_has_pass_fail_unknown() -> None:
    params = {"status_terms": ["已完成", "待整改"]}
    assert run("issue_response_status_exists", spans=[span("待整改", sid="status", section="审查意见回复表")], params=params).status is RuleStatus.PASS
    failed = run("issue_response_status_exists", spans=[span("没有状态", section="审查意见回复表")], params=params)
    assert failed.status is RuleStatus.FAIL
    assert failed.evidence_span_ids == ["s1"]
    assert run("issue_response_status_exists", spans=[span("待整改")]).status is RuleStatus.UNKNOWN


def test_alias_normalization_has_pass_fail_unknown() -> None:
    params = {"canonical_name": "开发井总数", "aliases": ["钻井总数"]}
    assert run("alias_normalization", [fact("a", "开发井总数", 36, raw_name="钻井总数")], params=params).status is RuleStatus.PASS
    assert run(
        "alias_normalization",
        [fact("a", "钻井总数", 36, raw_name="钻井总数")],
        params=params,
    ).status is RuleStatus.FAIL
    assert run("alias_normalization", [], params=params).status is RuleStatus.UNKNOWN


def test_evidence_required_has_pass_fail_unknown() -> None:
    assert run("evidence_required", spans=[span("证据")], params={"min_evidence": 1}).status is RuleStatus.PASS
    assert run("evidence_required", spans=[span("仅一条")], params={"min_evidence": 2}).status is RuleStatus.FAIL
    missing = run("evidence_required", spans=[], params={"min_evidence": 1})
    assert missing.status is RuleStatus.UNKNOWN
    assert missing.evidence_span_ids == []
