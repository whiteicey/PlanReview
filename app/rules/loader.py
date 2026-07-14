"""Safely load declarative review rules and terminology from YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.domain.exceptions import RuleLoadError
from app.domain.schemas import RuleDefinition
from app.extraction.terminology import TerminologyMap

# This whitelist is deliberately declarative: rule YAML selects a known name but
# never supplies executable code.  Task 12 implements these named operators.
_OPERATOR_NAMES = frozenset(
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
    }
)
_RULE_FIELDS = frozenset(RuleDefinition.model_fields)


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping without constructing arbitrary Python objects."""
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuleLoadError(f"无法读取 YAML: {exc}") from exc

    if not isinstance(value, dict):
        raise RuleLoadError("YAML 根节点必须是对象")
    return value


def _require_only_root_key(data: dict[str, Any], expected_key: str) -> Any:
    if expected_key not in data:
        raise RuleLoadError(f"缺少 {expected_key}")
    unexpected = set(data) - {expected_key}
    if unexpected:
        raise RuleLoadError(f"YAML 根节点包含未知字段: {', '.join(sorted(unexpected))}")
    return data[expected_key]


def _validate_rule_row(row: Any, index: int) -> RuleDefinition:
    if not isinstance(row, dict):
        raise RuleLoadError(f"rules[{index}] 必须是对象")

    unexpected = set(row) - _RULE_FIELDS
    if unexpected:
        raise RuleLoadError(
            f"rules[{index}] 包含未知字段: {', '.join(sorted(unexpected))}"
        )

    try:
        rule = RuleDefinition.model_validate(row)
    except ValidationError as exc:
        raise RuleLoadError(f"rules[{index}] 不符合规则模式: {exc}") from exc

    if rule.operator not in _OPERATOR_NAMES:
        raise RuleLoadError(f"未知 operator: {rule.operator}")
    return rule


def load_rules(path: Path) -> list[RuleDefinition]:
    """Load and validate a complete rule set from a ``rules`` YAML document."""
    rows = _require_only_root_key(_read_yaml(path), "rules")
    if not isinstance(rows, list):
        raise RuleLoadError("rules 必须是列表")

    rules: list[RuleDefinition] = []
    seen_rule_ids: set[str] = set()
    for index, row in enumerate(rows):
        rule = _validate_rule_row(row, index)
        if rule.rule_id in seen_rule_ids:
            raise RuleLoadError(f"重复 rule_id: {rule.rule_id}")
        seen_rule_ids.add(rule.rule_id)
        rules.append(rule)
    return rules


def _validate_aliases(aliases: Any) -> dict[str, list[str]]:
    if not isinstance(aliases, dict):
        raise RuleLoadError("aliases 必须是对象")

    validated: dict[str, list[str]] = {}
    for canonical, values in aliases.items():
        if not isinstance(canonical, str) or not canonical.strip():
            raise RuleLoadError("aliases 的术语名称必须是非空字符串")
        if not isinstance(values, list):
            raise RuleLoadError(f"aliases[{canonical!r}] 必须是列表")
        if any(not isinstance(alias, str) or not alias.strip() for alias in values):
            raise RuleLoadError(f"aliases[{canonical!r}] 的别名必须是非空字符串")
        validated[canonical] = values
    return validated


def load_terminology(path: Path) -> TerminologyMap:
    """Load an explicit terminology map from an ``aliases`` YAML document."""
    aliases = _validate_aliases(_require_only_root_key(_read_yaml(path), "aliases"))
    try:
        return TerminologyMap.from_mapping(aliases)
    except (TypeError, ValueError) as exc:
        raise RuleLoadError(f"术语映射无效: {exc}") from exc
