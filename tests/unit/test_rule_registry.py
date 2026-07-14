from __future__ import annotations

import pytest

from app.domain.enums import OnMissing, Severity
from app.domain.exceptions import RuleLoadError
from app.domain.schemas import RuleDefinition
from app.rules.registry import RuleRegistry


def rule(rule_id: str = "R1") -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        version="0.1",
        name="r",
        category="c",
        severity=Severity.LOW,
        operator="all_equal",
        on_missing=OnMissing.UNKNOWN,
    )


def test_registry_rejects_duplicate_and_returns_copy() -> None:
    definition = rule()
    registry = RuleRegistry()
    registry.register(definition)

    with pytest.raises(RuleLoadError, match="重复 rule_id: R1"):
        registry.register(definition)

    loaded = registry.get("R1")
    assert loaded == definition
    assert loaded is not definition


def test_registry_returns_nested_params_defensive_copy() -> None:
    definition = rule()
    definition.params = {"selectors": {"sections": ["附件A"]}}
    registry = RuleRegistry()
    registry.register(definition)

    definition.params["selectors"]["sections"].append("篡改输入")
    loaded = registry.get("R1")
    loaded.params["selectors"]["sections"].append("篡改输出")

    assert registry.get("R1").params == {"selectors": {"sections": ["附件A"]}}


def test_registry_rejects_unknown_rule_id() -> None:
    with pytest.raises(RuleLoadError, match="不存在 rule_id: missing"):
        RuleRegistry().get("missing")
