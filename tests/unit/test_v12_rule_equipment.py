from __future__ import annotations

from app.domain.enums import RuleStatus
from app.rules.operators import get_operator
from tests.unit.test_v12_rules import _context, _fact, _span


def test_equipment_redundancy_001_checks_total_running_standby_and_capacity():
    names = ["total", "running", "standby", "single_capacity", "demand"]
    spans = [_span(name, name) for name in names]
    facts = [
        _fact("total", "total", "3", "个", "total"),
        _fact("running", "running", "2", "个", "running"),
        _fact("standby", "standby", "1", "个", "standby"),
        _fact("single_capacity", "single_capacity", "100", "m", "single_capacity"),
        _fact("demand", "demand", "150", "m", "demand"),
    ]
    params = dict(zip(names, names))
    operator = get_operator("equipment_redundancy_v12")
    assert operator(_context(facts, spans), params).status is RuleStatus.PASS
    facts[-1] = _fact("demand", "demand", "250", "m", "demand")
    assert operator(_context(facts, spans), params).status is RuleStatus.FAIL
    assert operator(_context(facts[:3], spans[:3]), params).status is RuleStatus.UNKNOWN
