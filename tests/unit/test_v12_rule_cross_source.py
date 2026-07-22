from __future__ import annotations

from app.domain.enums import BlockType, RuleStatus
from app.rules.operators import get_operator
from tests.unit.test_v12_rules import _context, _fact, _span


def test_cross_source_param_001_requires_both_source_sides():
    spans = [
        _span("p", "pressure 4 MPa"),
        _span("t", "pressure 4 MPa", block=BlockType.TABLE_CELL, table=1, row=1, col=1),
    ]
    facts = [_fact("p", "pressure", "4", "MPa", "p"), _fact("t", "pressure", "4", "MPa", "t")]
    operator = get_operator("cross_source_param_v12")
    assert operator(_context(facts, spans), {"parameter": "pressure"}).status is RuleStatus.PASS
    facts[1] = _fact("t", "pressure", "5", "MPa", "t")
    assert operator(_context(facts, spans), {"parameter": "pressure"}).status is RuleStatus.FAIL
    assert operator(_context(facts[:1], spans[:1]), {"parameter": "pressure"}).status is RuleStatus.UNKNOWN
