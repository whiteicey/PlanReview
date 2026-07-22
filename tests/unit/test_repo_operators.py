from __future__ import annotations

from app.domain.enums import BlockType, ExtractionMethod, RuleStatus
from app.domain.schemas import ParameterFact, SourceSpan
from app.rules.operators import OperatorContext, get_operator


def cell(
    text: str,
    *,
    sid: str,
    table_index: int = 3,
    row_index: int,
    column_index: int,
    section: str = "审查意见回复表",
) -> SourceSpan:
    return SourceSpan(
        span_id=sid,
        document_id="D",
        section_path=[section],
        block_type=BlockType.TABLE_CELL,
        table_index=table_index,
        row_index=row_index,
        column_index=column_index,
        text=text,
        text_hash="h",
    )


def paragraph(text: str, *, sid: str, document_id: str = "D") -> SourceSpan:
    return SourceSpan(
        span_id=sid,
        document_id=document_id,
        section_path=["开发部署方案"],
        block_type=BlockType.PARAGRAPH,
        paragraph_index=1,
        text=text,
        text_hash="h",
    )


def fact(
    name: str,
    *,
    raw_name: str | None = None,
    document_id: str = "D",
    sid: str = "f-span",
) -> ParameterFact:
    return ParameterFact(
        fact_id=f"fact-{name}",
        canonical_name=name,
        raw_name=raw_name or name,
        raw_value="1",
        normalized_value=1.0,
        source_document=document_id,
        source_span_id=sid,
        extraction_method=ExtractionMethod.REGEX,
    )


def run(name, facts=(), spans=(), params=None):
    return get_operator(name)(OperatorContext(list(facts), list(spans)), params or {})


REPLY_PARAMS = {
    "section_contains": "审查意见回复表",
    "id_header_terms": ["意见编号", "意见"],
    "status_header_terms": ["回复", "状态"],
}


def _reply_header() -> list[SourceSpan]:
    return [
        cell("意见编号", sid="h0", row_index=0, column_index=0),
        cell("意见内容", sid="h1", row_index=0, column_index=1),
        cell("回复/状态", sid="h2", row_index=0, column_index=2),
    ]


def test_reply_table_status_complete_passes_when_every_status_filled() -> None:
    spans = _reply_header() + [
        cell("OP-1", sid="r1c0", row_index=1, column_index=0),
        cell("请核对井数。", sid="r1c1", row_index=1, column_index=1),
        cell("待回复", sid="r1c2", row_index=1, column_index=2),
    ]
    outcome = run("reply_table_status_complete", spans=spans, params=REPLY_PARAMS)
    assert outcome.status is RuleStatus.PASS


def test_reply_table_status_complete_fails_on_blank_status_cell() -> None:
    spans = _reply_header() + [
        cell("OP-1", sid="r1c0", row_index=1, column_index=0),
        cell("请核对井数。", sid="r1c1", row_index=1, column_index=1),
        cell("", sid="r1c2", row_index=1, column_index=2),
        cell("OP-2", sid="r2c0", row_index=2, column_index=0),
        cell("请补充位置。", sid="r2c1", row_index=2, column_index=1),
        cell("待整改", sid="r2c2", row_index=2, column_index=2),
    ]
    outcome = run("reply_table_status_complete", spans=spans, params=REPLY_PARAMS)
    assert outcome.status is RuleStatus.FAIL
    assert "r1c2" in outcome.evidence_span_ids
    assert "r2c2" not in outcome.evidence_span_ids


def test_reply_table_status_complete_fails_on_missing_status_cell() -> None:
    # Data row omits the status column entirely (only id + content cells present).
    spans = _reply_header() + [
        cell("OP-1", sid="r1c0", row_index=1, column_index=0),
        cell("请核对井数。", sid="r1c1", row_index=1, column_index=1),
    ]
    outcome = run("reply_table_status_complete", spans=spans, params=REPLY_PARAMS)
    assert outcome.status is RuleStatus.FAIL
    assert outcome.evidence_span_ids


def test_reply_table_status_complete_unknown_without_table() -> None:
    outcome = run(
        "reply_table_status_complete",
        spans=[paragraph("没有回复表", sid="p1")],
        params=REPLY_PARAMS,
    )
    assert outcome.status is RuleStatus.UNKNOWN


def test_missing_reply_table_never_uses_all_document_spans_as_evidence() -> None:
    headings = [
        SourceSpan(
            span_id=f"h{index}",
            document_id="D",
            section_path=[f"{index} heading"],
            block_type=BlockType.HEADING,
            text=f"{index} heading",
            text_hash="h",
        )
        for index in range(4)
    ]
    spans = headings + [paragraph(f"body {index}", sid=f"p{index}") for index in range(1713)]

    outcome = run("reply_table_status_complete", spans=spans, params=REPLY_PARAMS)

    assert outcome.status is RuleStatus.UNKNOWN
    assert outcome.evidence_span_ids == ["h0", "h1", "h2"]


def test_reply_table_status_complete_does_not_key_on_verdict_prose() -> None:
    # A paragraph literally naming a defect must not drive the outcome; only the
    # table structure does. Here the table is complete, so PASS despite the prose.
    spans = _reply_header() + [
        cell("OP-1", sid="r1c0", row_index=1, column_index=0),
        cell("请核对井数。", sid="r1c1", row_index=1, column_index=1),
        cell("已闭环", sid="r1c2", row_index=1, column_index=2),
        paragraph("审查意见回复缺少状态", sid="p-prose"),
    ]
    outcome = run("reply_table_status_complete", spans=spans, params=REPLY_PARAMS)
    assert outcome.status is RuleStatus.PASS


ALIAS_PARAMS = {"terms": [{"canonical": "开发井总数", "aliases": ["部署井数", "井位数量"]}]}


def test_prose_alias_unnormalized_fails_when_alias_in_prose_and_canonical_not_a_fact() -> None:
    spans = [paragraph("正文交替使用开发井总数、部署井数、井位数量，未统一。", sid="p32")]
    outcome = run("prose_alias_unnormalized", spans=spans, params=ALIAS_PARAMS)
    assert outcome.status is RuleStatus.FAIL
    assert "p32" in outcome.evidence_span_ids


def test_prose_alias_unnormalized_fails_even_when_canonical_is_extracted_fact() -> None:
    # Mixing a distinct alias into prose is a divergence regardless of whether the
    # canonical also appears as an extracted fact.
    spans = [paragraph("本节另称开发井总数为部署井数。", sid="p1")]
    facts = [fact("开发井总数", raw_name="开发井总数")]
    outcome = run("prose_alias_unnormalized", facts=facts, spans=spans, params=ALIAS_PARAMS)
    assert outcome.status is RuleStatus.FAIL
    assert "p1" in outcome.evidence_span_ids


def test_prose_alias_unnormalized_ignores_alias_that_is_substring_of_canonical() -> None:
    # 生产井 is a substring of canonical 生产井数 — a generic word, not a divergence.
    params = {"terms": [{"canonical": "生产井数", "aliases": ["生产井", "开发生产井"]}]}
    spans = [paragraph("其中生产井32口。", sid="p1")]
    outcome = run("prose_alias_unnormalized", spans=spans, params=params)
    assert outcome.status is RuleStatus.PASS


def test_prose_alias_unnormalized_passes_when_no_alias_in_prose() -> None:
    spans = [paragraph("本方案规划开发井总数为40口。", sid="p1")]
    outcome = run("prose_alias_unnormalized", spans=spans, params=ALIAS_PARAMS)
    assert outcome.status is RuleStatus.PASS


def test_prose_alias_unnormalized_unknown_without_terms() -> None:
    outcome = run(
        "prose_alias_unnormalized",
        spans=[paragraph("部署井数为40口。", sid="p1")],
        params={"terms": []},
    )
    assert outcome.status is RuleStatus.UNKNOWN


def test_prose_alias_unnormalized_evidence_is_only_the_alias_paragraph() -> None:
    spans = [
        paragraph("开发井总数为40口。", sid="pa"),
        paragraph("本节另称开发井总数为部署井数。", sid="pb"),
    ]
    outcome = run("prose_alias_unnormalized", spans=spans, params=ALIAS_PARAMS)
    assert outcome.status is RuleStatus.FAIL
    assert "pb" in outcome.evidence_span_ids
    assert "pa" not in outcome.evidence_span_ids
