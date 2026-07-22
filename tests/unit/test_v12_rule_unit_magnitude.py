from __future__ import annotations

from app.domain.enums import RuleStatus
from app.rules.operators import get_operator
from tests.unit.test_v12_rules import _context, _fact, _span


def test_unit_magnitude_001_accepts_equivalent_units_and_flags_real_scale_error():
    spans = [_span("a", "180 m"), _span("b", "0.18 km")]
    facts = [_fact("a", "length", "180", "m", "a"), _fact("b", "length", "0.18", "km", "b")]
    operator = get_operator("unit_magnitude_v12")
    assert operator(_context(facts, spans), {"parameter": "length", "ratio_threshold": 10}).status is RuleStatus.PASS
    facts[1] = _fact("b", "length", "180", "km", "b")
    assert operator(_context(facts, spans), {"parameter": "length", "ratio_threshold": 10}).status is RuleStatus.FAIL
    assert operator(_context(facts[:1], spans[:1]), {"parameter": "length"}).status is RuleStatus.UNKNOWN
