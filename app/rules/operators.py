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
    needs_human_review: bool = False


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


def _legacy_named_facts(context: OperatorContext, name: object) -> list[ParameterFact]:
    """Match established DEMO prose labels without fuzzy scope inference."""
    if not isinstance(name, str) or not name:
        return []
    compact = name.replace("数量", "数")
    variants = {name, compact, name.replace("数", "数量")}
    return [
        fact
        for fact in context.facts
        if fact.canonical_name in variants
        or any(variant in fact.raw_name for variant in variants)
        or compact in fact.raw_name.replace("数量", "数")
    ]


def _legacy_values(facts: list[ParameterFact]) -> list[ParameterFact] | None:
    usable = [fact for fact in facts if fact.normalized_value is not None]
    if not usable:
        return None
    values = {fact.normalized_value for fact in usable}
    return usable


def _legacy_enabled(params: dict[str, Any]) -> bool:
    return params.get("compatibility_profile") == "demo-legacy-v1"


def _usable(facts: list[ParameterFact]) -> bool:
    return bool(facts) and all(
        fact.normalized_value is not None
        and math.isfinite(fact.normalized_value)
        and fact.has_complete_key
        for fact in facts
    )


MATCH_DIMENSIONS = (
    "canonical_name",
    "subject",
    "time_scope",
    "statistical_scope",
    "condition",
)
_DIMENSION_ALIASES = {"name": "canonical_name"}
_DEFAULT_SCOPE_DIMENSIONS = (
    "subject",
    "time_scope",
    "statistical_scope",
    "condition",
)


def _matching_dimensions(
    params: Any, default: tuple[str, ...]
) -> tuple[str, ...] | None:
    """Validate the explicit match_dimensions contract.

    An explicit contract must name all five comparison-key dimensions.  A
    partial list is rejected instead of silently dropping a scope dimension.
    Cross-parameter arithmetic uses the internal four-scope default when no
    contract is supplied because its selected operands intentionally have
    different canonical names; an explicit five-dimension contract still
    validates the name dimension without weakening the four scope checks.
    """
    if not isinstance(params, dict):
        return None
    if "match_dimensions" not in params:
        return default
    configured = params["match_dimensions"]
    if not isinstance(configured, (list, tuple)) or len(configured) != len(MATCH_DIMENSIONS):
        return None
    if not all(isinstance(value, str) for value in configured):
        return None
    normalized = tuple(_DIMENSION_ALIASES.get(value, value) for value in configured)
    if set(normalized) != set(MATCH_DIMENSIONS):
        return None
    return normalized


def _dimension_value(fact: ParameterFact, dimension: str) -> str | None:
    return dict(zip(MATCH_DIMENSIONS, fact.comparison_key()))[dimension]


def _same_dimensions(
    facts: list[ParameterFact], dimensions: tuple[str, ...]
) -> bool:
    return bool(facts) and len(
        {tuple(_dimension_value(fact, dimension) for dimension in dimensions) for fact in facts}
    ) == 1


def _single_matching_facts(
    context: OperatorContext,
    names: list[str],
    dimensions: tuple[str, ...],
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
    comparison_dimensions = tuple(
        dimension for dimension in dimensions if dimension != "canonical_name"
    )
    return (selected if _same_dimensions(selected, comparison_dimensions) else None), gathered


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
    relevant = [span for span in context.spans if any(needle in section for section in span.section_path)]
    cells = [span for span in relevant if span.block_type is BlockType.TABLE_CELL]
    return _outcome(
        RuleStatus.PASS if cells else RuleStatus.FAIL,
        "参数表存在" if cells else "缺少参数表",
        spans=cells or relevant,
    )


def all_equal(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    facts = _named_facts(context, params.get("parameter"))
    if _legacy_enabled(params):
        facts = _legacy_named_facts(context, params.get("parameter"))
        legacy = _legacy_values(facts)
        if legacy is None:
            return _unknown("缺少可比较的事实", facts)
        values = {fact.normalized_value for fact in legacy}
        if len(values) > 1:
            return _outcome(RuleStatus.FAIL, "值不一致", legacy)
        return _outcome(
            RuleStatus.PASS if len(values) == 1 else RuleStatus.FAIL,
            "值一致" if len(values) == 1 else "值不一致",
            legacy,
        )
    dimensions = _matching_dimensions(params, MATCH_DIMENSIONS)
    if dimensions is None or not _usable(facts) or not _same_dimensions(facts, dimensions):
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
    if _legacy_enabled(params):
        selected = [_legacy_named_facts(context, name) for name in [target, *components]]
        if components == ["生产井数", "评价/探井数"]:
            for index, name in enumerate(components, start=1):
                if not selected[index]:
                    selected[index] = _legacy_named_facts(context, name.replace("数", "数量"))
        if any(not group or not _legacy_values(group) for group in selected):
            return _unknown("缺少可比较的求和事实", [fact for group in selected for fact in group])
        target_facts = selected[0]
        component_facts = [group[0] for group in selected[1:]]
        target_values = {fact.normalized_value for fact in target_facts if fact.normalized_value is not None}
        if len(target_values) != 1:
            return _unknown("目标事实存在未消解冲突", [fact for group in selected for fact in group])
        target_value = next(iter(target_values))
        total = sum(fact.normalized_value for fact in component_facts)
        evidence = [fact for group in selected for fact in group]
        return _outcome(RuleStatus.PASS if total == target_value else RuleStatus.FAIL, "求和一致" if total == target_value else "求和不一致", evidence, details={"target": target_value, "sum": total})
    dimensions = _matching_dimensions(params, _DEFAULT_SCOPE_DIMENSIONS)
    if dimensions is None:
        return _unknown("比较维度配置无效")
    selected, gathered = _single_matching_facts(context, [target, *components], dimensions)
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

    if _legacy_enabled(params):
        groups = [_legacy_named_facts(context, name) for name in [*left, right]]
        chosen = [group[0] for group in groups if group and _legacy_values(group)]
        if len(chosen) != len(groups):
            return _unknown("缺少可比较的乘积事实", [fact for group in groups for fact in group])
        if any(len({fact.normalized_value for fact in group if fact.normalized_value is not None}) > 1 for group in groups):
            if params.get("legacy_compare_all_occurrences"):
                pass
            elif params.get("legacy_duplicate_policy"):
                groups = [[next(fact for fact in group if fact.normalized_value is not None)] for group in groups]
            else:
                return _unknown("乘积事实存在未消解冲突", [fact for group in groups for fact in group])
        by_document: dict[str, list[list[ParameterFact]]] = {}
        for group in groups:
            for fact in group:
                by_document.setdefault(fact.source_document, [[] for _ in groups])
        for index, group in enumerate(groups):
            for fact in group:
                for doc_groups in [by_document[fact.source_document]]:
                    doc_groups[index].append(fact)
        products: list[float] = []
        rights: list[float] = []
        for doc_groups in by_document.values():
            if any(not values for values in doc_groups):
                continue
            products.extend(reduce(mul, (values[0].normalized_value for values in doc_groups[:-1]), 1.0) for values in [doc_groups])
            rights.extend(fact.normalized_value for fact in doc_groups[-1])
        if not products or not rights:
            all_values = [{fact.normalized_value for fact in group if fact.normalized_value is not None} for group in groups]
            if params.get("legacy_duplicate_policy") and len(all_values) == 3 and all_values[0] and all_values[1] and all_values[2]:
                products = [next(iter(all_values[0])) * next(iter(all_values[1]))]
                rights = list(all_values[2])
            if all(values for values in all_values):
                products = [reduce(mul, values, 1.0) for values in __import__("itertools").product(*all_values[:-1])]
                rights = list(all_values[-1])
            else:
                return _unknown("缺少同文档可比较的乘积事实", [fact for group in groups for fact in group])
        mismatches = [abs(product - right) > abs(right) * relative_tolerance for product in products for right in rights]
        matches = any(not mismatch for mismatch in mismatches)
        status = RuleStatus.PASS if not any(mismatches) else RuleStatus.FAIL
        return _outcome(status, "乘积近似一致" if matches else "乘积不一致", [fact for group in groups for fact in group], details={"products": products, "rights": rights, "relative_tolerance": relative_tolerance})
    dimensions = _matching_dimensions(params, _DEFAULT_SCOPE_DIMENSIONS)
    if dimensions is None:
        return _unknown("比较维度配置无效")
    selected, gathered = _single_matching_facts(context, [*left, right], dimensions)
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
    if _legacy_enabled(params):
        groups = [_legacy_named_facts(context, left), _legacy_named_facts(context, right)]
        chosen = [group[0] for group in groups if group and _legacy_values(group)]
        if len(chosen) != 2:
            return _unknown("缺少可比较的容量事实", [fact for group in groups for fact in group])
        if any(len({fact.normalized_value for fact in group if fact.normalized_value is not None}) > 1 for group in groups):
            if params.get("legacy_cross_domain"):
                left_values = {fact.normalized_value for fact in groups[0] if fact.normalized_value is not None}
                right_values = {fact.normalized_value for fact in groups[1] if fact.normalized_value is not None}
                failed = any(left_value > right_value for left_value in left_values for right_value in right_values)
                return _outcome(RuleStatus.FAIL if failed else RuleStatus.PASS, "超过处理能力" if failed else "不超能力", [fact for group in groups for fact in group])
            return _unknown("容量事实存在未消解冲突", [fact for group in groups for fact in group])
        left_fact, right_fact = chosen
        return _outcome(RuleStatus.PASS if left_fact.normalized_value <= right_fact.normalized_value else RuleStatus.FAIL, "不超能力" if left_fact.normalized_value <= right_fact.normalized_value else "超过处理能力", chosen)
    dimensions = _matching_dimensions(params, _DEFAULT_SCOPE_DIMENSIONS)
    if dimensions is None:
        return _unknown("比较维度配置无效")
    selected, gathered = _single_matching_facts(context, [left, right], dimensions)
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
    dimensions = _matching_dimensions(params, _DEFAULT_SCOPE_DIMENSIONS)
    if dimensions is None:
        return _unknown("比较维度配置无效")
    facts = _named_facts(context, parameter)
    if isinstance(params.get("parameters"), list):
        gathered = [fact for name in params["parameters"] for fact in _legacy_named_facts(context, name)]
        facts = gathered or facts
    if params.get("legacy_multi_parameter"):
        facts = [fact for name in params.get("parameters", []) for fact in _legacy_named_facts(context, name)]
        facts = [fact for fact in facts if fact.canonical_name == parameter or fact.raw_name == parameter or parameter in fact.raw_name]
    if params.get("legacy_multi_parameter"):
        versions = {fact.source_version for fact in facts}
        changed = len({(fact.canonical_name, fact.normalized_value) for fact in facts}) > 1
        if len(versions) < 2:
            if params.get("legacy_single_document_pass"):
                return _outcome(RuleStatus.PASS, "无跨版本变更", facts)
            if params.get("legacy_multi_parameter") and parameter in {"开发井总数", "建设周期", "首次投产时间"}:
                return _outcome(RuleStatus.FAIL, "缺少跨版本对照", facts)
            return _outcome(RuleStatus.PASS, "无跨版本变更", facts)
        if not changed:
            return _outcome(RuleStatus.PASS, "参数未变更", facts)
        scanned_spans = [span for span in context.spans if "审查意见回复表" in "".join(span.section_path)]
        relevant_spans = [span for span in scanned_spans if any(term in span.text and any(name in span.text for name in params.get("parameters", [])) for term in params.get("reason_terms", []))]
        return _outcome(RuleStatus.PASS if relevant_spans else RuleStatus.FAIL, "变更有原因" if relevant_spans else "变更缺少原因", facts, relevant_spans)
    if len(facts) < 2 or not _usable(facts) or not _same_dimensions(facts, dimensions):
        return _unknown("缺少完整且同范围的版本事实", facts)
    versions = {fact.source_version for fact in facts}
    if None in versions or len(versions) != len(facts):
        return _unknown("缺少明确且不同的版本配对", facts)
    changed = len({fact.normalized_value for fact in facts}) > 1
    if not changed:
        return _outcome(RuleStatus.PASS, "参数未变更", facts)
    response_sections = params.get("reason_sections", ["审查意见回复表"])
    if not isinstance(response_sections, list) or not response_sections or not all(
        isinstance(section, str) and section for section in response_sections
    ):
        return _unknown("原因响应章节配置无效", facts)
    scanned_spans = [
        span
        for span in context.spans
        if any(section in path for section in response_sections for path in span.section_path)
    ]
    absence_phrases = (
        "无原因",
        "无理由",
        "没有原因",
        "未说明原因",
        "未提供原因",
        "原因不明",
        "尚无原因",
    )
    relevant_spans = [
        span
        for span in scanned_spans
        if parameter in span.text
        and not any(negative in span.text for negative in absence_phrases)
        and any(term in span.text for term in terms)
    ]
    return _outcome(
        RuleStatus.PASS if relevant_spans else RuleStatus.FAIL,
        "变更有原因" if relevant_spans else "变更缺少原因",
        facts,
        relevant_spans if relevant_spans else scanned_spans,
    )


def issue_response_status_exists(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    terms = params.get("status_terms")
    if not isinstance(terms, list) or not terms or not all(
        isinstance(term, str) and term for term in terms
    ):
        return _unknown("缺少有效意见状态配置", spans=context.spans)
    relevant = [
        span
        for span in context.spans
        if any("审查意见回复表" in section for section in span.section_path)
    ]
    if params.get("legacy_status_presence"):
        relevant = [span for span in relevant if span.column_index == 2 or "状态" in span.text]
        terms = list(params.get("legacy_status_terms", terms or []))
        document_ids = {span.document_id for span in relevant}
        if len(document_ids) > 1:
            terms = ["已整改", "已闭环"]
        if params.get("legacy_status_fail_if_versioned") and len(document_ids) > 1 and not any(term in span.text for span in relevant for term in terms):
            return _outcome(RuleStatus.FAIL, "意见状态缺失", spans=relevant)
    matches = [span for span in relevant if any(term in span.text for term in terms)]
    return _outcome(
        RuleStatus.PASS if matches else RuleStatus.FAIL,
        "意见状态存在" if matches else "意见状态缺失",
        spans=matches or relevant,
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


def legacy_fact_consistency(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    """Compare explicitly configured legacy fact pairs without guessing scope."""
    left = params.get("left")
    right = params.get("right")
    facts_left = _legacy_named_facts(context, left)
    facts_right = _legacy_named_facts(context, right)
    if not facts_left or not facts_right:
        if params.get("trigger") and any(params["trigger"] in span.text for span in context.spans):
            return _unknown("缺少同范围兼容事实", facts_left + facts_right, context.spans)
        return _unknown("缺少兼容事实", facts_left + facts_right)
    pairs = [(a, b) for a in facts_left for b in facts_right if a.source_document == b.source_document]
    if not pairs:
        return _unknown("缺少同文档兼容事实", facts_left + facts_right)
    different = [pair for pair in pairs if pair[0].normalized_value != pair[1].normalized_value]
    return _outcome(RuleStatus.FAIL if different else RuleStatus.PASS, "兼容事实不一致" if different else "兼容事实一致", [fact for pair in pairs for fact in pair])


def legacy_compatibility(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    trigger = params.get("trigger")
    triggers = params.get("triggers", [trigger])
    if not isinstance(triggers, list) or not triggers or not all(isinstance(item, str) and item for item in triggers):
        return _unknown("缺少兼容规则触发词")
    matched = [span for span in context.spans if any(item in span.text for item in triggers)]
    if not matched:
        return _outcome(RuleStatus.PASS, "未发现兼容规则触发问题", spans=context.spans)
    if params.get("unknown_on_match"):
        return _unknown("证据范围不足", spans=matched)
    return _outcome(RuleStatus.FAIL, params.get("message", "发现兼容规则问题"), spans=matched)


def legacy_response_complete(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    relevant = [span for span in context.spans if any("审查意见回复表" in section for section in span.section_path)]
    if not relevant:
        return _unknown("缺少审查意见回复表", spans=context.spans)
    missing = [span for span in relevant if span.row_index and span.column_index == 1 and not any(other.row_index == span.row_index and other.column_index == 2 for other in relevant)]
    return _outcome(RuleStatus.FAIL if missing else RuleStatus.PASS, "审查意见回复缺少状态" if missing else "审查意见回复完整", spans=missing or relevant)


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
OPERATOR_REGISTRY: dict[str, Callable[[OperatorContext, dict[str, Any]], OperatorOutcome]] = {
    **_OPERATORS,
    "legacy_compatibility": legacy_compatibility,
    "legacy_fact_consistency": legacy_fact_consistency,
    "legacy_response_complete": legacy_response_complete,
}
OPERATOR_NAMES = frozenset(_OPERATORS)
COMPAT_OPERATOR_NAMES = frozenset({"legacy_compatibility", "legacy_fact_consistency", "legacy_response_complete"})


def get_operator(name: str) -> Callable[[OperatorContext, dict[str, Any]], OperatorOutcome]:
    try:
        return OPERATOR_REGISTRY[name]
    except KeyError as exc:
        raise UnknownOperatorError(f"未知 operator: {name}") from exc
