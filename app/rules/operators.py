"""Pure, whitelisted three-valued operators for declarative review rules."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import reduce
from operator import mul
from typing import Any, Callable

from app.domain.enums import BlockType, RuleStatus
from app.domain.exceptions import UnknownOperatorError
from app.domain.schemas import ParameterFact, SourceSpan


@dataclass(frozen=True)
class OperatorContext:
    facts: list[ParameterFact]
    spans: list[SourceSpan]


@dataclass(frozen=True)
class OperatorOutcome:
    status: RuleStatus
    message: str
    evidence_span_ids: list[str] = field(default_factory=list)
    involved_fact_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _fact_ids(facts: list[ParameterFact]) -> list[str]:
    return _unique([fact.fact_id for fact in facts])


def _span_ids(spans: list[SourceSpan]) -> list[str]:
    return _unique([span.span_id for span in spans])


def _fact_span_ids(facts: list[ParameterFact]) -> list[str]:
    return _unique([fact.source_span_id for fact in facts])


def _outcome(
    status: RuleStatus,
    message: str,
    facts: list[ParameterFact] | None = None,
    spans: list[SourceSpan] | None = None,
    details: dict[str, Any] | None = None,
) -> OperatorOutcome:
    facts = facts or []
    spans = spans or []
    return OperatorOutcome(
        status=status,
        message=message,
        evidence_span_ids=_unique(_fact_span_ids(facts) + _span_ids(spans)),
        involved_fact_ids=_fact_ids(facts),
        details=details or {},
    )


def _unknown(
    message: str,
    facts: list[ParameterFact] | None = None,
    spans: list[SourceSpan] | None = None,
) -> OperatorOutcome:
    return _outcome(RuleStatus.UNKNOWN, message, facts, spans)


def _named_facts(context: OperatorContext, name: object) -> list[ParameterFact]:
    return (
        [fact for fact in context.facts if fact.canonical_name == name]
        if isinstance(name, str) and name
        else []
    )


def _usable(facts: list[ParameterFact]) -> bool:
    return bool(facts) and all(
        fact.normalized_value is not None
        and math.isfinite(fact.normalized_value)
        and fact.has_complete_key
        for fact in facts
    )


def _dimensions(fact: ParameterFact) -> tuple[str | None, str | None, str | None, str | None]:
    """Return every non-name comparison dimension without dropping scope."""
    _, subject, time_scope, statistical_scope, condition = fact.comparison_key()
    return subject, time_scope, statistical_scope, condition


def _same_dimensions(facts: list[ParameterFact]) -> bool:
    return bool(facts) and len({_dimensions(fact) for fact in facts}) == 1


def _single_matching_facts(
    context: OperatorContext, names: list[str]
) -> tuple[list[ParameterFact] | None, list[ParameterFact]]:
    """Select exactly one fact per name only when every scope dimension matches.

    A selector that could choose across different subjects, time scopes,
    statistical scopes, or conditions is deliberately ambiguous.  It returns
    UNKNOWN rather than silently grouping or choosing a first occurrence.
    """
    groups = [_named_facts(context, name) for name in names]
    gathered = [fact for group in groups for fact in group]
    if len(names) != len(set(names)) or any(not group for group in groups):
        return None, gathered
    if not all(_usable(group) for group in groups):
        return None, gathered
    if any(len(group) != 1 for group in groups):
        return None, gathered
    selected = [group[0] for group in groups]
    return (selected if _same_dimensions(selected) else None), gathered


def required_sections_exist(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    required = params.get("required_sections")
    if not isinstance(required, list) or not required or not all(
        isinstance(section, str) and section for section in required
    ):
        return _unknown("缺少有效章节配置", spans=context.spans)

    found = [part for span in context.spans for part in span.section_path]
    missing = [
        section
        for section in required
        if not any(section == part or section in part for part in found)
    ]
    return _outcome(
        RuleStatus.FAIL if missing else RuleStatus.PASS,
        "缺少章节" if missing else "章节齐全",
        spans=context.spans,
        details={"missing": missing},
    )


def required_parameter_table_exists(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    needle = params.get("section_contains")
    if not isinstance(needle, str) or not needle:
        return _unknown("缺少参数表章节配置", spans=context.spans)
    cells = [
        span
        for span in context.spans
        if span.block_type is BlockType.TABLE_CELL
        and any(needle in section for section in span.section_path)
    ]
    return _outcome(
        RuleStatus.PASS if cells else RuleStatus.FAIL,
        "参数表存在" if cells else "缺少参数表",
        spans=cells,
    )


def all_equal(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    facts = _named_facts(context, params.get("parameter"))
    if not _usable(facts) or not _same_dimensions(facts):
        return _unknown("缺少完整且可比较的事实", facts)
    values = {fact.normalized_value for fact in facts}
    return _outcome(
        RuleStatus.PASS if len(values) == 1 else RuleStatus.FAIL,
        "值一致" if len(values) == 1 else "值不一致",
        facts,
    )


def sum_equals(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    target = params.get("target")
    components = params.get("components")
    if (
        not isinstance(target, str)
        or not target
        or not isinstance(components, list)
        or not components
        or not all(isinstance(component, str) and component for component in components)
    ):
        return _unknown("缺少有效求和配置")
    selected, gathered = _single_matching_facts(context, [target, *components])
    if selected is None:
        return _unknown("缺少完整、唯一且同范围的求和事实", gathered)
    target_fact, *component_facts = selected
    total = sum(fact.normalized_value for fact in component_facts)
    matches = total == target_fact.normalized_value
    return _outcome(
        RuleStatus.PASS if matches else RuleStatus.FAIL,
        "求和一致" if matches else "求和不一致",
        selected,
        details={"target": target_fact.normalized_value, "sum": total},
    )


def product_approximately_equals(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    left = params.get("left")
    right = params.get("right")
    tolerance = params.get("relative_tolerance", 0.05)
    if (
        not isinstance(left, list)
        or len(left) < 2
        or not all(isinstance(name, str) and name for name in left)
        or not isinstance(right, str)
        or not right
        or isinstance(tolerance, bool)
    ):
        return _unknown("缺少有效乘积配置")
    try:
        relative_tolerance = float(tolerance)
    except (TypeError, ValueError):
        return _unknown("相对容差无效")
    if not math.isfinite(relative_tolerance) or relative_tolerance < 0:
        return _unknown("相对容差无效")

    selected, gathered = _single_matching_facts(context, [*left, right])
    if selected is None:
        return _unknown("缺少完整、唯一且同范围的乘积事实", gathered)
    *left_facts, right_fact = selected
    product = reduce(mul, (fact.normalized_value for fact in left_facts), 1.0)
    difference = abs(product - right_fact.normalized_value)
    allowed = abs(right_fact.normalized_value) * relative_tolerance
    matches = difference <= allowed
    return _outcome(
        RuleStatus.PASS if matches else RuleStatus.FAIL,
        "乘积近似一致" if matches else "乘积不一致",
        selected,
        details={
            "product": product,
            "right": right_fact.normalized_value,
            "relative_tolerance": relative_tolerance,
        },
    )


def less_or_equal(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    left = params.get("left")
    right = params.get("right")
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return _unknown("缺少有效容量比较配置")
    selected, gathered = _single_matching_facts(context, [left, right])
    if selected is None:
        return _unknown("缺少完整、唯一且同范围的容量比较事实", gathered)
    left_fact, right_fact = selected
    matches = left_fact.normalized_value <= right_fact.normalized_value
    return _outcome(
        RuleStatus.PASS if matches else RuleStatus.FAIL,
        "不超能力" if matches else "超过处理能力",
        selected,
    )


def change_requires_reason(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    parameter = params.get("parameter")
    terms = params.get("reason_terms", ["原因", "调整"])
    if (
        not isinstance(parameter, str)
        or not parameter
        or not isinstance(terms, list)
        or not terms
        or not all(isinstance(term, str) and term for term in terms)
    ):
        return _unknown("缺少有效变更原因配置")
    facts = _named_facts(context, parameter)
    if len(facts) < 2 or not _usable(facts) or not _same_dimensions(facts):
        return _unknown("缺少完整且同范围的版本事实", facts)
    changed = len({fact.normalized_value for fact in facts}) > 1
    reasons = [
        span for span in context.spans if any(term in span.text for term in terms)
    ]
    if not changed:
        return _outcome(RuleStatus.PASS, "参数未变更", facts)
    return _outcome(
        RuleStatus.PASS if reasons else RuleStatus.FAIL,
        "变更有原因" if reasons else "变更缺少原因",
        facts,
        reasons,
    )


def issue_response_status_exists(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    terms = params.get("status_terms")
    if not isinstance(terms, list) or not terms or not all(
        isinstance(term, str) and term for term in terms
    ):
        return _unknown("缺少有效意见状态配置", spans=context.spans)
    matches = [span for span in context.spans if any(term in span.text for term in terms)]
    return _outcome(
        RuleStatus.PASS if matches else RuleStatus.FAIL,
        "意见状态存在" if matches else "意见状态缺失",
        spans=matches,
    )


def alias_normalization(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    canonical_name = params.get("canonical_name")
    aliases = params.get("aliases", [])
    if (
        not isinstance(canonical_name, str)
        or not canonical_name
        or not isinstance(aliases, list)
        or not all(isinstance(alias, str) and alias for alias in aliases)
    ):
        return _unknown("缺少有效术语归一配置")
    canonical_facts = _named_facts(context, canonical_name)
    if canonical_facts:
        return _outcome(RuleStatus.PASS, "术语已归一", canonical_facts)
    alias_facts = [fact for fact in context.facts if fact.raw_name in aliases]
    if alias_facts:
        return _outcome(RuleStatus.FAIL, "术语未归一", alias_facts)
    return _unknown("未找到术语事实")


def evidence_required(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    minimum = params.get("min_evidence")
    if isinstance(minimum, bool):
        return _unknown("最小证据数无效", spans=context.spans)
    try:
        required = int(minimum)
    except (TypeError, ValueError):
        return _unknown("最小证据数无效", spans=context.spans)
    if required < 1:
        return _unknown("最小证据数无效", spans=context.spans)
    if not context.spans:
        return _unknown("证据不足")
    return _outcome(
        RuleStatus.PASS if len(context.spans) >= required else RuleStatus.FAIL,
        "证据充分" if len(context.spans) >= required else "证据不足",
        spans=context.spans,
        details={"required": required, "available": len(context.spans)},
    )


_OPERATORS: dict[str, Callable[[OperatorContext, dict[str, Any]], OperatorOutcome]] = {
    "required_sections_exist": required_sections_exist,
    "required_parameter_table_exists": required_parameter_table_exists,
    "all_equal": all_equal,
    "sum_equals": sum_equals,
    "product_approximately_equals": product_approximately_equals,
    "less_or_equal": less_or_equal,
    "change_requires_reason": change_requires_reason,
    "issue_response_status_exists": issue_response_status_exists,
    "alias_normalization": alias_normalization,
    "evidence_required": evidence_required,
}
OPERATOR_NAMES = frozenset(_OPERATORS)


def get_operator(name: str) -> Callable[[OperatorContext, dict[str, Any]], OperatorOutcome]:
    try:
        return _OPERATORS[name]
    except KeyError as exc:
        raise UnknownOperatorError(f"未知 operator: {name}") from exc
