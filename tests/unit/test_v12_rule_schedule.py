from __future__ import annotations

from app.domain.enums import RuleStatus
from app.rules.operators import get_operator
from tests.unit.test_v12_rules import _context, _fact, _span


def test_schedule_001_checks_inclusive_months_and_returns_unknown_without_dates():
    spans = [_span("s", "2024-01"), _span("e", "2024-03"), _span("d", "3 months")]
    facts = [
        _fact("s", "start", "2024-01", "month", "s"),
        _fact("e", "end", "2024-03", "month", "e"),
        _fact("d", "duration", "3", "month", "d"),
    ]
    operator = get_operator("schedule_v12")
    context = _context(facts, spans)
    assert operator(context, {"start": "start", "end": "end", "duration": "duration"}).status is RuleStatus.PASS
    facts[2] = _fact("d", "duration", "2", "month", "d")
    assert operator(_context(facts, spans), {"start": "start", "end": "end", "duration": "duration"}).status is RuleStatus.FAIL
    assert operator(_context(facts[:2], spans[:2]), {"start": "start", "end": "end", "duration": "duration"}).status is RuleStatus.UNKNOWN
