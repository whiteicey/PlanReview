"""Pure, whitelisted three-valued operators for declarative review rules."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import reduce
from operator import mul
from typing import Any, Callable

from app.domain.enums import BlockType, RuleStatus
from app.domain.exceptions import UnknownOperatorError
from app.domain.schemas import ParameterFact, SourceSpan
from app.domain.units import UnitMetadata, get_unit_metadata
from app.rules.semantic import SemanticIndex

_MAX_FINDING_EVIDENCE_SPANS = 5
_MAX_STRUCTURE_EVIDENCE_SPANS = 3


@dataclass(frozen=True)
class OperatorContext:
    facts: list[ParameterFact]
    spans: list[SourceSpan]
    semantic_index: SemanticIndex | None = None


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
    return _unique(
        [identifier for fact in facts for identifier in [fact.fact_id, *fact.merged_fact_ids]]
    )


def _span_ids(spans: list[SourceSpan]) -> list[str]:
    return _unique([span.span_id for span in spans])


def _structure_anchors(spans: list[SourceSpan]) -> list[SourceSpan]:
    """Return a small, stable document-structure witness for absence checks.

    A missing section or attachment has no direct source text.  The title tree
    is therefore useful context, but the complete document is not evidence for
    a single absence claim.  Keep only heading anchors and allow an empty list
    when no structural anchor is available.
    """
    return [
        span for span in spans if span.block_type is BlockType.HEADING
    ][:_MAX_STRUCTURE_EVIDENCE_SPANS]


def _section_matches(query: str, path: str) -> bool:
    """Match a configured section without ordinary substring false positives."""
    query = query.strip()
    path = path.strip()
    if not query or not path:
        return False
    if query == path:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)*", query):
        return re.search(rf"(?<![\d.]){re.escape(query)}(?![\d.])", path) is not None
    tokens = [token for token in re.split(r"[\s/:：()（）,，;；]+", path) if token]
    return any(token.startswith(query) or token.endswith(query) for token in tokens)


def _fact_span_ids(facts: list[ParameterFact]) -> list[str]:
    return _unique(
        [span_id for fact in facts for span_id in [fact.source_span_id, *fact.merged_span_ids]]
    )


def _outcome(
    status: RuleStatus,
    message: str,
    facts: list[ParameterFact] | None = None,
    spans: list[SourceSpan] | None = None,
    details: dict[str, Any] | None = None,
) -> OperatorOutcome:
    facts = facts or []
    spans = spans or []
    evidence_span_ids = _unique(_fact_span_ids(facts) + _span_ids(spans))
    bounded_evidence = evidence_span_ids[:_MAX_FINDING_EVIDENCE_SPANS]
    normalized_details = dict(details or {})
    if len(evidence_span_ids) > len(bounded_evidence):
        normalized_details["evidence_trimmed_count"] = (
            len(evidence_span_ids) - len(bounded_evidence)
        )
        normalized_details["evidence_limit"] = _MAX_FINDING_EVIDENCE_SPANS
    return OperatorOutcome(
        status=status,
        message=message,
        evidence_span_ids=bounded_evidence,
        involved_fact_ids=_fact_ids(facts),
        details=normalized_details,
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


def _one_complete_fact_per_operand(
    context: OperatorContext, names: list[str]
) -> tuple[list[ParameterFact] | None, list[ParameterFact]]:
    """Select one fact per operand for cross-parameter arithmetic.

    Each operand is a distinct physical quantity that naturally lives in its own
    scope (a well count is a build-phase figure; a processing capacity is a
    design figure), so operands are NOT required to share a comparison scope.
    Each operand must instead resolve to exactly one complete-key value: a fact
    with a complete comparison key, and — where the operand appears more than
    once — a single agreed value.  A missing operand, an operand with no
    complete-key fact, or an operand carrying conflicting complete values yields
    UNKNOWN (returned as ``None``).
    """
    groups = [_named_facts(context, name) for name in names]
    gathered = [fact for group in groups for fact in group]
    if len(names) != len(set(names)) or any(not group for group in groups):
        return None, gathered
    selected: list[ParameterFact] = []
    for group in groups:
        complete = [
            fact
            for fact in group
            if _usable([fact])
            and get_unit_metadata(fact.canonical_unit, fact.unit_category) is not None
        ]
        if len(complete) != len(group):
            return None, gathered
        values = {fact.normalized_value for fact in complete}
        if not complete or len(values) != 1:
            return None, gathered
        selected.append(complete[0])
    return selected, gathered


def _compatible_unit_metadata(facts: list[ParameterFact]) -> list[UnitMetadata] | None:
    """Resolve complete physical metadata and require one shared dimension."""

    metadata = [get_unit_metadata(fact.canonical_unit, fact.unit_category) for fact in facts]
    if not facts or any(item is None for item in metadata):
        return None
    resolved = [item for item in metadata if item is not None]
    if len({item.dimension for item in resolved}) != 1:
        return None
    return resolved


def required_sections_exist(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    required = params.get("required_sections")
    if not isinstance(required, list) or not required or not all(
        isinstance(section, str) and section for section in required
    ):
        return _unknown("缺少有效章节配置", spans=_structure_anchors(context.spans))

    found = [part for span in context.spans for part in span.section_path]
    missing = [
        section
        for section in required
        if not any(_section_matches(section, part) for part in found)
    ]
    return _outcome(
        RuleStatus.FAIL if missing else RuleStatus.PASS,
        "缺少章节" if missing else "章节齐全",
        spans=_structure_anchors(context.spans) if missing else [],
        details={"missing": missing, "evidence_source": "structure_index"},
    )


def required_parameter_table_exists(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    needle = params.get("section_contains")
    if not isinstance(needle, str) or not needle:
        return _unknown("缺少参数表章节配置", spans=_structure_anchors(context.spans))
    relevant = [
        span for span in context.spans
        if any(_section_matches(needle, section) for section in span.section_path)
    ]
    cells = [span for span in relevant if span.block_type is BlockType.TABLE_CELL]
    return _outcome(
        RuleStatus.PASS if cells else RuleStatus.FAIL,
        "参数表存在" if cells else "缺少参数表",
        spans=cells or relevant,
    )


def all_equal(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    facts = _named_facts(context, params.get("parameter"))
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
    selected, gathered = _one_complete_fact_per_operand(context, [target, *components])
    if selected is None:
        return _unknown("缺少完整且唯一的求和事实", gathered)
    target_fact, *component_facts = selected
    if _compatible_unit_metadata(selected) is None:
        return _unknown("求和事实的单位不兼容", selected)
    total = sum(fact.normalized_value for fact in component_facts)
    matches = math.isclose(
        total,
        target_fact.normalized_value,
        rel_tol=1e-6,
        abs_tol=1e-9,
    )
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

    selected, gathered = _one_complete_fact_per_operand(context, [*left, right])
    if selected is None:
        return _unknown("缺少完整且唯一的乘积事实", gathered)
    *left_facts, right_fact = selected
    right_metadata = get_unit_metadata(right_fact.canonical_unit, right_fact.unit_category)
    left_metadata = [
        get_unit_metadata(fact.canonical_unit, fact.unit_category) for fact in left_facts
    ]
    if right_metadata is None or any(item is None for item in left_metadata):
        return _unknown("乘积事实的单位不完整", selected)
    resolved_left = [item for item in left_metadata if item is not None]
    count_operands = [item for item in resolved_left if item.category == "count"]
    measured_operands = [item for item in resolved_left if item.category != "count"]
    if (
        len(count_operands) != 1
        or not measured_operands
        or right_metadata.category == "count"
        or any(item.dimension != right_metadata.dimension for item in measured_operands)
    ):
        return _unknown("乘积事实的物理维度不兼容", selected)
    product = reduce(mul, (fact.normalized_value for fact in left_facts), 1.0)
    matches = math.isclose(
        product,
        right_fact.normalized_value,
        rel_tol=relative_tolerance,
        abs_tol=1e-9,
    )
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
    selected, gathered = _one_complete_fact_per_operand(context, [left, right])
    if selected is None:
        return _unknown("缺少完整且唯一的容量比较事实", gathered)
    left_fact, right_fact = selected
    if _compatible_unit_metadata(selected) is None:
        return _unknown("比较事实的单位不兼容", selected)
    matches = left_fact.normalized_value <= right_fact.normalized_value
    return _outcome(
        RuleStatus.PASS if matches else RuleStatus.FAIL,
        "不超能力" if matches else "超过处理能力",
        selected,
    )


_CHANGE_PARAMETER_HEADERS = frozenset({"参数", "参数名称", "指标"})
_CHANGE_OLD_VALUE_HEADERS = frozenset({"原值", "调整前"})
_CHANGE_NEW_VALUE_HEADERS = frozenset({"新值", "调整后"})
_CHANGE_REASON_HEADERS = frozenset({"变更原因", "调整原因", "说明"})
_INVALID_CHANGE_REASONS = frozenset(
    {"未说明", "无", "无原因", "不详", "原因不明", "待补充"}
)


def _change_reason_table_result(
    spans: list[SourceSpan], parameter: str
) -> tuple[RuleStatus, list[SourceSpan]] | None:
    table_cells = [
        span
        for span in spans
        if span.block_type is BlockType.TABLE_CELL
        and span.table_index is not None
        and span.row_index is not None
        and span.column_index is not None
    ]
    tables: dict[tuple[str, int], list[SourceSpan]] = {}
    for cell in table_cells:
        tables.setdefault((cell.document_id, cell.table_index), []).append(cell)

    relevant_tables = [cells for cells in tables.values() if any(parameter in cell.text for cell in cells)]
    if not relevant_tables:
        return None

    matched_rows: list[list[SourceSpan]] = []
    for cells in relevant_tables:
        rows: dict[int, dict[int, SourceSpan]] = {}
        for cell in cells:
            rows.setdefault(cell.row_index, {}).setdefault(cell.column_index, cell)

        header_row_index: int | None = None
        parameter_column = old_column = new_column = reason_column = None
        for row_index in sorted(rows):
            row = rows[row_index]
            headings = {column: cell.text.strip() for column, cell in row.items()}
            parameter_columns = [col for col, text in headings.items() if text in _CHANGE_PARAMETER_HEADERS]
            old_columns = [col for col, text in headings.items() if text in _CHANGE_OLD_VALUE_HEADERS]
            new_columns = [col for col, text in headings.items() if text in _CHANGE_NEW_VALUE_HEADERS]
            reason_columns = [col for col, text in headings.items() if text in _CHANGE_REASON_HEADERS]
            if parameter_columns and old_columns and new_columns and reason_columns:
                header_row_index = row_index
                parameter_column = parameter_columns[0]
                old_column = old_columns[0]
                new_column = new_columns[0]
                reason_column = reason_columns[0]
                break
        if header_row_index is None:
            return (RuleStatus.UNKNOWN, cells)

        assert parameter_column is not None and reason_column is not None
        assert old_column is not None and new_column is not None
        for row_index in sorted(rows):
            if row_index <= header_row_index:
                continue
            row = rows[row_index]
            parameter_cell = row.get(parameter_column)
            if parameter_cell is None or parameter not in parameter_cell.text:
                continue
            # Presence of both before/after columns is part of a reliable change row.
            if row.get(old_column) is None or row.get(new_column) is None:
                return (RuleStatus.UNKNOWN, list(row.values()))
            matched_rows.append(list(row.values()))

    if not matched_rows:
        return (RuleStatus.UNKNOWN, [cell for cells in relevant_tables for cell in cells])

    evidence: list[SourceSpan] = []
    all_valid = True
    for row in matched_rows:
        cells_by_id: dict[str, SourceSpan] = {}
        for cell in row:
            cells_by_id.setdefault(cell.span_id, cell)
        deduplicated = list(cells_by_id.values())
        evidence.extend(deduplicated)
        table_key = (row[0].document_id, row[0].table_index)
        table = tables[table_key]
        header = next(
            cell for cell in table if cell.text.strip() in _CHANGE_REASON_HEADERS
        )
        reason = next((cell for cell in deduplicated if cell.column_index == header.column_index), None)
        reason_text = reason.text.strip() if reason is not None else ""
        if not reason_text or reason_text in _INVALID_CHANGE_REASONS:
            all_valid = False
    return (RuleStatus.PASS if all_valid else RuleStatus.FAIL, evidence)


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
    if not facts:
        return _unknown("缺少参数事实", facts)
    # Consider the version of every occurrence, complete or not. A versionless
    # fact is ambiguous — it may be an uncaptured second version — so fail closed.
    if any(fact.source_version is None for fact in facts):
        return _unknown("存在缺版本的参数事实", facts)
    versions = {fact.source_version for fact in facts}
    if len(versions) < 2:
        # Genuinely a single-version document: any extra occurrences are
        # same-version duplicates, not a hidden change. PASS only when the
        # version resolves to one consistent value across usable facts.
        usable = [fact for fact in facts if _usable([fact])]
        if not usable:
            return _unknown("缺少完整的单版本参数事实", facts)
        if len({fact.normalized_value for fact in usable}) == 1:
            return _outcome(RuleStatus.PASS, "无跨版本变更", usable)
        return _unknown("单版本内存在未消解冲突", usable)
    # Multiple versions present: the comparison requires every fact complete,
    # same-scope, and exactly one fact per distinct version.
    if len(facts) < 2 or not _usable(facts) or not _same_dimensions(facts, dimensions):
        return _unknown("缺少完整且同范围的版本事实", facts)
    if len(versions) != len(facts):
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
        if any(
            _section_matches(section, path)
            for section in response_sections
            for path in span.section_path
        )
    ]
    table_result = _change_reason_table_result(scanned_spans, parameter)
    if table_result is not None:
        table_status, table_evidence = table_result
        messages = {
            RuleStatus.PASS: "变更有原因",
            RuleStatus.FAIL: "变更缺少原因",
            RuleStatus.UNKNOWN: "变更表格缺少可靠表头或完整行",
        }
        return _outcome(table_status, messages[table_status], facts, table_evidence)
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
    """A prior-round opinion table must carry at least one reply status.

    Rule intent is status *existence*: a non-empty status cell (e.g. 待回复,
    an awaiting-reply state) counts as present, even when it is not one of the
    enumerated closed states.  Missing/blank cells are the completeness
    operator's concern; here a table with no status anywhere at all fails.
    """
    terms = params.get("status_terms")
    if not isinstance(terms, list) or not terms or not all(
        isinstance(term, str) and term for term in terms
    ):
        return _unknown("缺少有效意见状态配置", spans=_structure_anchors(context.spans))
    tables = _reply_status_tables(context, params)
    if tables is None or not tables:
        return _unknown("缺少审查意见回复表", spans=_structure_anchors(context.spans))
    present = [
        row["status"]
        for table in tables
        for row in table
        if row["status"] is not None and row["status"].text.strip()
    ]
    if present:
        return _outcome(RuleStatus.PASS, "意见状态存在", spans=present)
    every_cell = [span for table in tables for row in table for span in row["cells"]]
    return _outcome(RuleStatus.FAIL, "意见状态缺失", spans=every_cell)


def alias_normalization(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    canonical_name = params.get("parameter") or params.get("canonical_name")
    aliases = params.get("aliases", [])
    aliases_by_parameter = params.get("aliases_by_parameter")
    if isinstance(aliases_by_parameter, dict) and isinstance(canonical_name, str):
        aliases = aliases_by_parameter.get(canonical_name, [])
    if (
        not isinstance(canonical_name, str)
        or not canonical_name
        or not isinstance(aliases, list)
        or not all(isinstance(alias, str) and alias for alias in aliases)
    ):
        return _unknown("缺少有效术语归一配置")
    relevant_facts = [
        fact
        for fact in context.facts
        if fact.raw_name == canonical_name or fact.raw_name in aliases
    ]
    if not relevant_facts:
        return _unknown("未找到术语事实")
    alias_facts = [fact for fact in relevant_facts if fact.raw_name in aliases]
    if alias_facts:
        return _outcome(RuleStatus.FAIL, "术语未归一", alias_facts)
    return _outcome(RuleStatus.PASS, "术语已归一", relevant_facts)


def evidence_required(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    minimum = params.get("min_evidence")
    if isinstance(minimum, bool):
        return _unknown("最小证据数无效", spans=_structure_anchors(context.spans))
    try:
        required = int(minimum)
    except (TypeError, ValueError):
        return _unknown("最小证据数无效", spans=_structure_anchors(context.spans))
    if required < 1:
        return _unknown("最小证据数无效", spans=_structure_anchors(context.spans))
    parameter = params.get("parameter")
    parameters = params.get("parameters")
    if isinstance(parameter, str) and parameter:
        names = {parameter}
    elif isinstance(parameters, list) and parameters and all(
        isinstance(name, str) and name for name in parameters
    ):
        names = set(parameters)
    elif parameter is None and parameters is None:
        names = None
    else:
        return _unknown("缺少有效证据参数配置")

    relevant = [
        fact
        for fact in context.facts
        if names is None or fact.canonical_name in names or fact.raw_name in names
    ]
    if not relevant:
        return _unknown("缺少相关证据事实")

    span_by_id = {span.span_id: span for span in context.spans if span.span_id}
    valid_facts = [fact for fact in relevant if fact.source_span_id in span_by_id]
    missing_span_fact_ids = [fact.fact_id for fact in relevant if not fact.source_span_id]
    invalid_fact_ids = [
        fact.fact_id
        for fact in relevant
        if fact.source_span_id and fact.source_span_id not in span_by_id
    ]
    details = {
        "required": required,
        "available": len({fact.source_span_id for fact in valid_facts}),
        "invalid_fact_ids": invalid_fact_ids,
        "missing_span_fact_ids": missing_span_fact_ids,
    }
    if missing_span_fact_ids or invalid_fact_ids:
        return _outcome(RuleStatus.FAIL, "事实缺少有效证据定位", valid_facts, details=details)
    evidence_ids = {fact.source_span_id for fact in valid_facts}
    status = RuleStatus.PASS if len(evidence_ids) >= required else RuleStatus.FAIL
    return _outcome(
        status,
        "证据充分" if status is RuleStatus.PASS else "证据不足",
        valid_facts,
        details=details,
    )


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return list(value)


def _reply_status_tables(
    context: OperatorContext, params: dict[str, Any]
) -> list[list[dict[str, Any]]] | None:
    """Locate review-opinion tables and their per-row status cells.

    Returns ``None`` for invalid configuration, ``[]`` when no such table with a
    recognizable header exists, otherwise one entry per table: a list of data
    rows, each ``{"status": SourceSpan | None, "cells": [SourceSpan, ...]}``.
    The status column is read from the header (a row holding both an id-term and
    a status-term cell), never a fixed index, so both the completeness and the
    existence operators share one honest structural reading.
    """
    section_contains = params.get("section_contains")
    id_terms = _string_list(params.get("id_header_terms"))
    status_terms = _string_list(params.get("status_header_terms"))
    if not isinstance(section_contains, str) or not section_contains or id_terms is None or status_terms is None:
        return None

    cells = [
        span
        for span in context.spans
        if span.block_type is BlockType.TABLE_CELL
        and span.table_index is not None
        and span.row_index is not None
        and span.column_index is not None
        and any(_section_matches(section_contains, part) for part in span.section_path)
    ]
    tables: list[list[dict[str, Any]]] = []
    for table_index in sorted({span.table_index for span in cells}):
        rows: dict[int, dict[int, SourceSpan]] = {}
        for span in cells:
            if span.table_index == table_index:
                rows.setdefault(span.row_index, {})[span.column_index] = span
        header_row = None
        status_column = None
        for row_index in sorted(rows):
            columns = rows[row_index]
            has_id = any(any(term in span.text for term in id_terms) for span in columns.values())
            status_cols = [
                column
                for column, span in columns.items()
                if any(term in span.text for term in status_terms)
            ]
            if has_id and status_cols:
                header_row = row_index
                status_column = min(status_cols)
                break
        if header_row is None or status_column is None:
            continue
        data_rows = [
            {
                "status": rows[row_index].get(status_column),
                "cells": [span for _, span in sorted(rows[row_index].items())],
            }
            for row_index in sorted(rows)
            if row_index > header_row
        ]
        tables.append(data_rows)
    return tables


def reply_table_status_complete(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    """Every data row of a review-opinion table must carry a status cell.

    The status column is identified from the header row (a row that contains
    both an id-term cell and a status-term cell), never a hardcoded index.  A
    data row whose status cell is missing or blank fails with that row's spans
    as evidence.  Absence of such a table is UNKNOWN, not a silent pass.
    """
    tables = _reply_status_tables(context, params)
    if tables is None:
        return _unknown("缺少有效回复表配置", spans=_structure_anchors(context.spans))
    if not tables:
        return _unknown("缺少审查意见回复表", spans=_structure_anchors(context.spans))

    failing: list[SourceSpan] = []
    for table in tables:
        for row in table:
            status_span = row["status"]
            if status_span is None or not status_span.text.strip():
                failing.extend(row["cells"])
    if failing:
        return _outcome(RuleStatus.FAIL, "审查意见回复缺少状态", spans=failing)
    every_cell = [span for table in tables for row in table for span in row["cells"]]
    return _outcome(RuleStatus.PASS, "审查意见回复完整", spans=every_cell)


def prose_alias_unnormalized(
    context: OperatorContext, params: dict[str, Any]
) -> OperatorOutcome:
    """Flag a distinct alias term used in body prose instead of the canonical name.

    Generalizes ``alias_normalization`` (which only sees extracted facts) to the
    case where an alias appears in narrative text without a unit, so no fact is
    produced.  Only *distinct* aliases are considered — an alias that is a
    substring of its own canonical name (e.g. 生产井 ⊂ 生产井数) is a generic word,
    not a divergent term, and would false-positive on clean documents.  Evaluated
    per document; every distinct-alias occurrence in prose is a divergence from
    the canonical vocabulary, regardless of whether the canonical also appears.
    """
    terms = params.get("terms")
    if not isinstance(terms, list) or not terms:
        return _unknown("缺少术语别名配置")
    entries: list[tuple[str, list[str]]] = []
    for entry in terms:
        if not isinstance(entry, dict):
            return _unknown("术语别名配置无效")
        canonical = entry.get("canonical")
        aliases = _string_list(entry.get("aliases"))
        if not isinstance(canonical, str) or not canonical or aliases is None:
            return _unknown("术语别名配置无效")
        distinct = [alias for alias in aliases if alias not in canonical]
        entries.append((canonical, distinct))

    paragraphs = [span for span in context.spans if span.block_type is BlockType.PARAGRAPH]
    if not paragraphs:
        return _unknown("缺少正文段落", spans=_structure_anchors(context.spans))

    failing: list[SourceSpan] = []
    for _, aliases in entries:
        for span in paragraphs:
            if any(alias in span.text for alias in aliases):
                failing.append(span)
    if failing:
        return _outcome(RuleStatus.FAIL, "参数别名未在正文归一", spans=failing)
    return _outcome(RuleStatus.PASS, "正文未见未归一别名", spans=paragraphs)


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
    "reply_table_status_complete": reply_table_status_complete,
    "prose_alias_unnormalized": prose_alias_unnormalized,
}
OPERATOR_REGISTRY = dict(_OPERATORS)
OPERATOR_NAMES = frozenset(_OPERATORS)


def get_operator(name: str) -> Callable[[OperatorContext, dict[str, Any]], OperatorOutcome]:
    if name not in OPERATOR_REGISTRY:
        # Lazy import avoids a circular dependency: V1.2 operators reuse the
        # context/outcome contracts defined above.
        from app.rules.v12_operators import V12_OPERATORS

        OPERATOR_REGISTRY.update(V12_OPERATORS)
    try:
        return OPERATOR_REGISTRY[name]
    except KeyError as exc:
        raise UnknownOperatorError(f"未知 operator: {name}") from exc
