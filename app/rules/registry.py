"""In-memory registry for validated rule definitions."""

from __future__ import annotations

from app.domain.exceptions import RuleLoadError
from app.domain.schemas import RuleDefinition


class RuleRegistry:
    """Register rules by unique identifier and return immutable-style copies."""

    def __init__(self) -> None:
        self._rules: dict[str, RuleDefinition] = {}

    def register(self, rule: RuleDefinition) -> None:
        if rule.rule_id in self._rules:
            raise RuleLoadError(f"重复 rule_id: {rule.rule_id}")
        self._rules[rule.rule_id] = rule.model_copy(deep=True)

    def get(self, rule_id: str) -> RuleDefinition:
        try:
            return self._rules[rule_id].model_copy(deep=True)
        except KeyError as exc:
            raise RuleLoadError(f"不存在 rule_id: {rule_id}") from exc
