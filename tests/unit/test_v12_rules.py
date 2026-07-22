from __future__ import annotations

import hashlib

from app.domain.enums import BlockType, ExtractionMethod, RuleStatus
from app.domain.schemas import ParameterFact, SourceSpan
from app.rules.feature_flags import feature_flag_name, is_rule_enabled
from app.rules.operators import OperatorContext, get_operator
from app.rules.semantic import build_semantic_index


def _span(span_id: str, text: str, *, block=BlockType.PARAGRAPH, section=None, table=None, row=None, col=None):
    return SourceSpan(
        span_id=span_id,
        document_id="doc",
        section_path=section or ["1"],
        block_type=block,
        table_index=table,
        row_index=row,
        column_index=col,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _fact(fid: str, name: str, value: str, unit: str, sid: str, *, subject="station", time="design"):
    return ParameterFact(
        fact_id=fid,
        canonical_name=name,
        raw_name=name,
        raw_value=value,
        normalized_value=None,
        raw_unit=unit,
        canonical_unit=unit,
        unit_category=None,
        subject=subject,
        time_scope=time,
        statistical_scope="all",
        condition="normal",
        source_document="doc",
        source_span_id=sid,
        extraction_method=ExtractionMethod.REGEX,
    )


def _context(facts, spans):
    return OperatorContext(facts=facts, spans=spans, semantic_index=build_semantic_index(facts, spans))


def test_reference_rule_requires_an_existing_target():
    spans = [_span("h1", "1 General", block=BlockType.HEADING, section=["1 General"])]
    context = _context([], spans)
    operator = get_operator("reference_v12")
    assert operator(context, {"references": [{"target": "1 General"}]}).status is RuleStatus.PASS
    assert operator(context, {"references": [{"target": "9 Missing"}]}).status is RuleStatus.FAIL
    assert operator(context, {}).status is RuleStatus.UNKNOWN


def test_summary_detail_and_cross_source_are_three_valued():
    spans = [
        _span("p", "capacity 100", section=["9"]),
        _span("t", "capacity 120", block=BlockType.TABLE_CELL, section=["9"], table=1, row=1, col=1),
    ]
    facts = [
        _fact("f1", "capacity", "100", "m", "p"),
        _fact("f2", "capacity", "120", "m", "t"),
    ]
    context = _context(facts, spans)
    assert get_operator("cross_source_param_v12")(context, {"parameter": "capacity"}).status is RuleStatus.FAIL
    assert get_operator("summary_detail_v12")(context, {"target": "capacity", "components": ["missing"]}).status is RuleStatus.UNKNOWN


def test_equivalent_units_pass_and_magnitude_conflict_fails():
    spans = [_span("a", "180 m"), _span("b", "0.18 km", block=BlockType.TABLE_CELL, table=1, row=1, col=1)]
    facts = [_fact("a", "length", "180", "m", "a"), _fact("b", "length", "0.18", "km", "b")]
    context = _context(facts, spans)
    assert get_operator("cross_source_param_v12")(context, {"parameter": "length"}).status is RuleStatus.PASS
    spans.append(_span("c", "180 km"))
    facts.append(_fact("c", "length", "180", "km", "c"))
    assert get_operator("unit_magnitude_v12")(_context(facts, spans), {"parameter": "length", "ratio_threshold": 10}).status is RuleStatus.FAIL


def test_feature_flags_are_independent_and_default_off(monkeypatch):
    rule_id = "REFERENCE-001"
    monkeypatch.delenv(feature_flag_name(rule_id), raising=False)
    assert is_rule_enabled(rule_id, True) is False
    monkeypatch.setenv(feature_flag_name(rule_id), "true")
    assert is_rule_enabled(rule_id, False) is True
