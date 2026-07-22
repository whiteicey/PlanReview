from __future__ import annotations

from tests.unit.test_v12_rules import _context, _fact, _span
from app.rules.operators import get_operator
from app.domain.enums import RuleStatus


def test_summary_detail_001_pass_fail_and_unknown():
    spans = [_span("total", "total 30"), _span("a", "10"), _span("b", "20")]
    facts = [
        _fact("total", "total", "30", "m", "total"),
        _fact("a", "detail_a", "10", "m", "a"),
        _fact("b", "detail_b", "20", "m", "b"),
    ]
    operator = get_operator("summary_detail_v12")
    assert operator(_context(facts, spans), {"target": "total", "components": ["detail_a", "detail_b"]}).status is RuleStatus.PASS
    facts[2] = _fact("b", "detail_b", "25", "m", "b")
    assert operator(_context(facts, spans), {"target": "total", "components": ["detail_a", "detail_b"]}).status is RuleStatus.FAIL
    assert operator(_context(facts, spans), {"target": "total", "components": ["missing"]}).status is RuleStatus.UNKNOWN
