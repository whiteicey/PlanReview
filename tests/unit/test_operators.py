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
    canonical_unit: str | None = None,
    unit_category: str | None = None,
) -> ParameterFact:
    return ParameterFact(
        fact_id=fid,
        canonical_name=canonical_name or name,
        raw_name=raw_name or name,
        raw_value="" if value is None else str(value),
        normalized_value=value,
        canonical_unit=canonical_unit,
        unit_category=unit_category,
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


def test_missing_section_uses_at_most_three_structure_anchors_not_document_wide_evidence() -> None:
    headings = [
        span(
            f"heading {index}",
            sid=f"h{index}",
            section=f"{index} heading",
            block_type=BlockType.HEADING,
        )
        for index in range(4)
    ]
    document_spans = headings + [
        span(f"body {index}", sid=f"p{index}", section="body")
        for index in range(1713)
    ]

    outcome = run(
        "required_sections_exist",
        spans=document_spans,
        params={"required_sections": ["missing section"]},
    )

    assert outcome.status is RuleStatus.FAIL
    assert outcome.evidence_span_ids == ["h0", "h1", "h2"]
    assert outcome.details["evidence_source"] == "structure_index"


def test_section_matching_does_not_treat_3_10_as_3_1() -> None:
    wrong = span("x", sid="wrong", section="3.10 其他章节")
    right = span("x", sid="right", section="3.1 目标章节")

    assert run(
        "required_sections_exist", spans=[wrong], params={"required_sections": ["3.1"]}
    ).status is RuleStatus.FAIL
    assert run(
        "required_sections_exist", spans=[right], params={"required_sections": ["3.1"]}
    ).status is RuleStatus.PASS


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
    facts = [
        fact("t", "总数", 36, span_id="t", canonical_unit="口", unit_category="count"),
        fact("a", "甲", 30, span_id="a", canonical_unit="口", unit_category="count"),
        fact("b", "乙", 6, span_id="b", canonical_unit="口", unit_category="count"),
    ]
    assert run("sum_equals", facts, params=params).status is RuleStatus.PASS
    assert run("sum_equals", [facts[0], facts[1], fact("b", "乙", 7, canonical_unit="口", unit_category="count")], params=params).status is RuleStatus.FAIL
    # Different operands may hold different scopes (each is a distinct quantity);
    # only each operand's own value must be complete and internally consistent.
    assert run("sum_equals", [facts[0], facts[1], fact("b", "乙", 6, time_scope="达产期", canonical_unit="口", unit_category="count")], params=params).status is RuleStatus.PASS
    # A single operand appearing with two conflicting complete values is UNKNOWN.
    assert run("sum_equals", [facts[0], facts[1], facts[2], fact("b2", "乙", 8, canonical_unit="口", unit_category="count")], params=params).status is RuleStatus.UNKNOWN
    # An operand present only without a complete key is UNKNOWN.
    assert run("sum_equals", [facts[0], facts[1], fact("b", "乙", 6, time_scope=None, canonical_unit="口", unit_category="count")], params=params).status is RuleStatus.UNKNOWN
    assert run("sum_equals", facts[:2], params=params).status is RuleStatus.UNKNOWN


def test_product_approximately_equals_has_pass_fail_unknown_and_scope_matching() -> None:
    params = {"left": ["井数", "单井产能"], "right": "总产能", "relative_tolerance": 0.05}
    facts = [
        fact("w", "井数", 36, canonical_unit="口", unit_category="count"),
        fact("r", "单井产能", 5, canonical_unit="m^3/day", unit_category="flow"),
        fact("t", "总产能", 180, canonical_unit="m^3/day", unit_category="flow"),
    ]
    assert run("product_approximately_equals", facts, params=params).status is RuleStatus.PASS
    assert run("product_approximately_equals", [facts[0], facts[1], fact("t", "总产能", 160, canonical_unit="m^3/day", unit_category="flow")], params=params).status is RuleStatus.FAIL
    # Cross-operand scope differences are expected (count vs capacity live in
    # different stages); the product still compares.
    assert run("product_approximately_equals", [facts[0], facts[1], fact("t", "总产能", 180, subject="单井", canonical_unit="m^3/day", unit_category="flow")], params=params).status is RuleStatus.PASS
    assert run("product_approximately_equals", facts[:2], params=params).status is RuleStatus.UNKNOWN


def test_cross_parameter_arithmetic_rejects_incomplete_siblings() -> None:
    params = {"left": "高峰产量", "right": "处理能力"}
    complete_left = fact("l", "高峰产量", 170, canonical_unit="m^3/day", unit_category="flow")
    complete_right = fact("r", "处理能力", 200, canonical_unit="m^3/day", unit_category="flow")
    incomplete_sibling = fact(
        "r-incomplete", "处理能力", 200, time_scope=None,
        canonical_unit="m^3/day", unit_category="flow",
    )

    outcome = run("less_or_equal", [complete_left, complete_right, incomplete_sibling], params=params)

    assert outcome.status is RuleStatus.UNKNOWN


def test_cross_parameter_arithmetic_rejects_all_null_scope_sibling() -> None:
    params = {"left": "高峰产量", "right": "处理能力"}
    complete_left = fact("l", "高峰产量", 170, canonical_unit="m^3/day", unit_category="flow")
    complete_right = fact("r", "处理能力", 200, canonical_unit="m^3/day", unit_category="flow")
    incomplete = fact(
        "r-null",
        "处理能力",
        200,
        subject=None,
        time_scope=None,
        statistical_scope=None,
        condition=None,
        canonical_unit="m^3/day",
        unit_category="flow",
    )

    outcome = run("less_or_equal", [complete_left, complete_right, incomplete], params=params)

    assert outcome.status is RuleStatus.UNKNOWN


def test_merged_fact_and_span_ids_remain_in_operator_evidence() -> None:
    params = {"left": "高峰产量", "right": "处理能力"}
    left = fact("l", "高峰产量", 170, span_id="table-left", canonical_unit="m^3/day", unit_category="flow").model_copy(
        update={"merged_fact_ids": ["prose-left"], "merged_span_ids": ["prose-span"]}
    )
    right = fact("r", "处理能力", 200, span_id="table-right", canonical_unit="m^3/day", unit_category="flow")

    outcome = run("less_or_equal", [left, right], params=params)

    assert outcome.status is RuleStatus.PASS
    assert outcome.involved_fact_ids == ["l", "prose-left", "r"]
    assert outcome.evidence_span_ids == ["table-left", "prose-span", "table-right"]


def test_less_or_equal_has_pass_fail_unknown_and_scope_matching() -> None:
    params = {"left": "高峰产量", "right": "处理能力"}
    left = fact("l", "高峰产量", 170, canonical_unit="m^3/day", unit_category="flow")
    right = fact("r", "处理能力", 200, canonical_unit="m^3/day", unit_category="flow")
    assert run("less_or_equal", [left, right], params=params).status is RuleStatus.PASS
    assert run("less_or_equal", [fact("l", "高峰产量", 220, canonical_unit="m^3/day", unit_category="flow"), right], params=params).status is RuleStatus.FAIL
    # Peak output (达产期) and processing capacity (设计期) naturally differ in
    # scope; the comparison still holds.
    assert run("less_or_equal", [left, fact("r", "处理能力", 200, statistical_scope="日峰值", canonical_unit="m^3/day", unit_category="flow")], params=params).status is RuleStatus.PASS
    assert run("less_or_equal", [left, fact("r", "处理能力", None, canonical_unit="m^3/day", unit_category="flow")], params=params).status is RuleStatus.UNKNOWN


def test_arithmetic_units_are_required_and_dimension_safe() -> None:
    params = {"left": "高峰产量", "right": "处理能力"}
    flow_left = fact("l", "高峰产量", 2739726.02739726, canonical_unit="m^3/day", unit_category="flow")
    flow_right = fact("r", "处理能力", 3000000, canonical_unit="m^3/day", unit_category="flow")
    assert run("less_or_equal", [flow_left, flow_right], params=params).status is RuleStatus.PASS
    assert run(
        "less_or_equal",
        [fact("l", "高峰产量", 4000000, canonical_unit="m^3/day", unit_category="flow"), flow_right],
        params=params,
    ).status is RuleStatus.FAIL
    assert run(
        "less_or_equal",
        [fact("l", "高峰产量", 10, canonical_unit="口", unit_category="count"), flow_right],
        params=params,
    ).status is RuleStatus.UNKNOWN
    assert run(
        "less_or_equal",
        [fact("l", "高峰产量", 10), flow_right],
        params=params,
    ).status is RuleStatus.UNKNOWN

    sum_params = {"target": "总量", "components": ["部分"]}
    assert run(
        "sum_equals",
        [
            fact("t", "总量", 10, canonical_unit="m^3/day", unit_category="flow"),
            fact("c", "部分", 10, canonical_unit="口", unit_category="count"),
        ],
        params=sum_params,
    ).status is RuleStatus.UNKNOWN


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
    # A single-version document has no cross-version change to explain: PASS,
    # not UNKNOWN. UNKNOWN is reserved for genuinely missing/ambiguous data.
    single = fact("only", "建设周期", 24, span_id="only", source_version="v1")
    assert run("change_requires_reason", [single], params=params).status is RuleStatus.PASS
    assert run("change_requires_reason", [single, fact("only2", "建设周期", 24, span_id="only2", source_version="v1")], params=params).status is RuleStatus.PASS
    # A second version present only as an INCOMPLETE fact must not be silently
    # dropped into a single-version PASS: that would mask a possible change.
    incomplete_v2 = fact("v2", "建设周期", 30, span_id="v2", source_version="v2", time_scope=None)
    assert run("change_requires_reason", [single, incomplete_v2], params=params).status is RuleStatus.UNKNOWN
    # A versionless (incomplete) second fact alongside one complete version is
    # likewise ambiguous, not a clean single-version PASS.
    versionless = fact("nov", "建设周期", 30, span_id="nov", source_version=None, time_scope=None)
    assert run("change_requires_reason", [single, versionless], params=params).status is RuleStatus.UNKNOWN
    # No usable facts at all remains UNKNOWN.
    assert run("change_requires_reason", [], params=params).status is RuleStatus.UNKNOWN


def _change_cell(text, *, table, row, col, sid):
    return SourceSpan(
        span_id=sid,
        document_id="D",
        section_path=["审查意见回复表"],
        block_type=BlockType.TABLE_CELL,
        table_index=table,
        row_index=row,
        column_index=col,
        text=text,
        text_hash=f"hash-{sid}",
    )


def _changed_facts():
    return [
        fact("old", "建设周期", 24, source_version="v1", span_id="old"),
        fact("new", "建设周期", 30, source_version="v2", span_id="new"),
    ]


def test_change_reason_uses_complete_rows_and_variable_column_order():
    cells = [
        _change_cell("说明", table=1, row=0, col=0, sid="h-reason"),
        _change_cell("调整后", table=1, row=0, col=1, sid="h-new"),
        _change_cell("参数名称", table=1, row=0, col=2, sid="h-param"),
        _change_cell("调整前", table=1, row=0, col=3, sid="h-old"),
        _change_cell("地面条件变化", table=1, row=1, col=0, sid="r1-reason"),
        _change_cell("30", table=1, row=1, col=1, sid="r1-new"),
        _change_cell("建设周期", table=1, row=1, col=2, sid="r1-param"),
        _change_cell("24", table=1, row=1, col=3, sid="r1-old"),
        # A merged XML cell may be exposed in more than one grid column.
        _change_cell("地面条件变化", table=1, row=1, col=4, sid="r1-reason"),
    ]
    outcome = run(
        "change_requires_reason",
        _changed_facts(),
        cells,
        {"parameter": "建设周期", "reason_terms": ["原因"]},
    )
    assert outcome.status is RuleStatus.PASS
    assert "r1-reason" in outcome.evidence_span_ids


@pytest.mark.parametrize("invalid", ["", "未说明", "无", "无原因", "不详", "原因不明", "待补充"])
def test_every_matching_change_row_requires_a_valid_reason(invalid):
    cells = [
        _change_cell("指标", table=2, row=0, col=0, sid="h0"),
        _change_cell("原值", table=2, row=0, col=1, sid="h1"),
        _change_cell("新值", table=2, row=0, col=2, sid="h2"),
        _change_cell("变更原因", table=2, row=0, col=3, sid="h3"),
        _change_cell("建设周期", table=2, row=1, col=0, sid="a0"),
        _change_cell("24", table=2, row=1, col=1, sid="a1"),
        _change_cell("30", table=2, row=1, col=2, sid="a2"),
        _change_cell("政策调整", table=2, row=1, col=3, sid="a3"),
        _change_cell("建设周期", table=2, row=2, col=0, sid="b0"),
        _change_cell("30", table=2, row=2, col=1, sid="b1"),
        _change_cell("32", table=2, row=2, col=2, sid="b2"),
    ]
    if invalid:
        cells.append(_change_cell(invalid, table=2, row=2, col=3, sid="b3"))
    outcome = run(
        "change_requires_reason",
        _changed_facts(),
        cells,
        {"parameter": "建设周期", "reason_terms": ["原因"]},
    )
    assert outcome.status is RuleStatus.FAIL


def test_relevant_change_table_without_reliable_headers_is_unknown():
    cells = [
        _change_cell("项目", table=3, row=0, col=0, sid="h0"),
        _change_cell("备注", table=3, row=0, col=1, sid="h1"),
        _change_cell("建设周期", table=3, row=1, col=0, sid="d0"),
        _change_cell("地面条件变化", table=3, row=1, col=1, sid="d1"),
    ]
    outcome = run(
        "change_requires_reason",
        _changed_facts(),
        cells,
        {"parameter": "建设周期", "reason_terms": ["原因"]},
    )
    assert outcome.status is RuleStatus.UNKNOWN


def test_change_reason_checks_all_relevant_tables():
    cells = []
    for table, reason in ((4, "方案优化"), (5, "待补充")):
        cells.extend([
            _change_cell("参数", table=table, row=0, col=0, sid=f"{table}-h0"),
            _change_cell("调整前", table=table, row=0, col=1, sid=f"{table}-h1"),
            _change_cell("调整后", table=table, row=0, col=2, sid=f"{table}-h2"),
            _change_cell("调整原因", table=table, row=0, col=3, sid=f"{table}-h3"),
            _change_cell("建设周期", table=table, row=1, col=0, sid=f"{table}-d0"),
            _change_cell("24", table=table, row=1, col=1, sid=f"{table}-d1"),
            _change_cell("30", table=table, row=1, col=2, sid=f"{table}-d2"),
            _change_cell(reason, table=table, row=1, col=3, sid=f"{table}-d3"),
        ])
    outcome = run(
        "change_requires_reason",
        _changed_facts(),
        cells,
        {"parameter": "建设周期", "reason_terms": ["原因"]},
    )
    assert outcome.status is RuleStatus.FAIL


def _reply_cell(text, *, row, col, sid):
    return SourceSpan(
        span_id=sid,
        document_id="D",
        section_path=["附件C 审查意见回复表"],
        block_type=BlockType.TABLE_CELL,
        table_index=3,
        row_index=row,
        column_index=col,
        text=text,
        text_hash="h",
    )


def _reply_table(rows):
    header = [
        _reply_cell("意见编号", row=0, col=0, sid="h0"),
        _reply_cell("意见内容", row=0, col=1, sid="h1"),
        _reply_cell("回复/状态", row=0, col=2, sid="h2"),
    ]
    cells = list(header)
    for index, (opinion, content, status) in enumerate(rows, start=1):
        cells.append(_reply_cell(opinion, row=index, col=0, sid=f"r{index}c0"))
        cells.append(_reply_cell(content, row=index, col=1, sid=f"r{index}c1"))
        cells.append(_reply_cell(status, row=index, col=2, sid=f"r{index}c2"))
    return cells


def test_issue_response_status_exists_has_pass_fail_unknown() -> None:
    params = {
        "status_terms": ["已完成", "待整改"],
        "section_contains": "审查意见回复表",
        "id_header_terms": ["意见编号", "意见"],
        "status_header_terms": ["回复", "状态"],
    }
    # 待回复 is a valid status (awaiting reply): a non-empty status cell means a
    # status is present, even if it is not one of the enumerated closed states.
    present = _reply_table([("OP-1", "请核对。", "待回复"), ("OP-2", "请补充。", "待整改")])
    assert run("issue_response_status_exists", spans=present, params=params).status is RuleStatus.PASS
    # Existence, not completeness: at least one status present passes even when
    # another row is blank (blank rows are COMPLETENESS-003's concern).
    partial = _reply_table([("OP-1", "请核对。", "待整改"), ("OP-2", "请补充。", "")])
    assert run("issue_response_status_exists", spans=partial, params=params).status is RuleStatus.PASS
    # No status anywhere fails.
    empty = _reply_table([("OP-1", "请核对。", ""), ("OP-2", "请补充。", "")])
    assert run("issue_response_status_exists", spans=empty, params=params).status is RuleStatus.FAIL
    # No reply table at all is UNKNOWN.
    assert run("issue_response_status_exists", spans=[span("待整改")], params=params).status is RuleStatus.UNKNOWN


def test_alias_normalization_has_pass_fail_unknown() -> None:
    params = {"canonical_name": "开发井总数", "aliases": ["钻井总数"]}
    assert run("alias_normalization", [fact("a", "开发井总数", 36)], params=params).status is RuleStatus.PASS
    assert run(
        "alias_normalization",
        [fact("a", "开发井总数", 36, raw_name="钻井总数")],
        params=params,
    ).status is RuleStatus.FAIL
    assert run("alias_normalization", [], params=params).status is RuleStatus.UNKNOWN


def test_evidence_required_has_pass_fail_unknown() -> None:
    supported = [fact("f1", "指标", 1, span_id="s1"), fact("f2", "指标", 2, span_id="s2")]
    available = [span("证据", sid="s1"), span("第二条", sid="s2")]
    assert run(
        "evidence_required", supported, available,
        {"parameter": "指标", "min_evidence": 2},
    ).status is RuleStatus.PASS
    assert run(
        "evidence_required", supported[:1], available[:1],
        {"parameter": "指标", "min_evidence": 2},
    ).status is RuleStatus.FAIL
    missing = run("evidence_required", spans=available, params={"parameter": "不存在", "min_evidence": 1})
    assert missing.status is RuleStatus.UNKNOWN
    assert missing.evidence_span_ids == []


def test_evidence_required_fails_closed_for_missing_or_ghost_fact_spans() -> None:
    valid = fact("f1", "指标", 1, span_id="s1")
    missing_span = fact("f2", "指标", 2, span_id="")
    ghost_span = fact("f3", "指标", 3, span_id="ghost")
    context_spans = [span("真实", sid="s1")]

    missing = run("evidence_required", [missing_span], context_spans, {"parameter": "指标", "min_evidence": 1})
    assert missing.status is RuleStatus.FAIL
    assert missing.evidence_span_ids == []
    assert missing.details["missing_span_fact_ids"] == ["f2"]

    ghost = run("evidence_required", [ghost_span], context_spans, {"parameter": "指标", "min_evidence": 1})
    assert ghost.status is RuleStatus.FAIL
    assert ghost.evidence_span_ids == []
    assert ghost.details["invalid_fact_ids"] == ["f3"]

    enough = run("evidence_required", [valid], context_spans, {"parameter": "指标", "min_evidence": 1})
    assert enough.status is RuleStatus.PASS
    assert enough.evidence_span_ids == ["s1"]
