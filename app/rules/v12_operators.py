"""Independent conservative operators for the six V1.2 capabilities."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
import math
import re
from typing import Any, Callable

from app.domain.enums import BlockType, RuleStatus
from app.rules.operators import OperatorContext, OperatorOutcome
from app.rules.semantic import SemanticObservation, equivalent_units, same_scope


def _unknown(message: str, spans: list[str] | None = None) -> OperatorOutcome:
    return OperatorOutcome(
        status=RuleStatus.UNKNOWN,
        message=message,
        evidence_span_ids=list(dict.fromkeys(spans or [])),
        details={"reason": "INSUFFICIENT_SEMANTIC_EVIDENCE"},
    )


def _result(
    status: RuleStatus,
    message: str,
    observations: list[SemanticObservation],
    spans: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> OperatorOutcome:
    evidence = [span for item in observations for span in item.source_span_ids]
    evidence.extend(spans or [])
    return OperatorOutcome(
        status=status,
        message=message,
        evidence_span_ids=list(dict.fromkeys(evidence)),
        involved_fact_ids=list(dict.fromkeys(item.fact_id for item in observations)),
        details=details or {},
    )


def _facts(context: OperatorContext, names: object) -> list[SemanticObservation]:
    if not isinstance(names, list) or not names or not all(isinstance(item, str) and item for item in names):
        return []
    wanted = {item.casefold() for item in names}
    if context.semantic_index is None:
        return []
    by_id = {fact.fact_id.casefold(): fact for fact in context.facts}
    return [
        observation
        for observation in context.semantic_index.observations
        if by_id.get(observation.fact_id.casefold()) is not None
        and by_id[observation.fact_id.casefold()].canonical_name.casefold() in wanted
    ]


def _parameter_observations(context: OperatorContext, parameter: object) -> list[SemanticObservation]:
    if not isinstance(parameter, str) or not parameter or context.semantic_index is None:
        return []
    names = {parameter.casefold()}
    return [
        observation
        for observation in context.semantic_index.observations
        if any(
            fact.fact_id == observation.fact_id
            and fact.canonical_name.casefold() in names
            for fact in context.facts
        )
    ]


def _same_scope_group(observations: list[SemanticObservation]) -> list[SemanticObservation] | None:
    if not observations:
        return None
    groups: dict[str, list[SemanticObservation]] = {}
    for item in observations:
        groups.setdefault(item.scope_key, []).append(item)
    if len(groups) != 1:
        return None
    return next(iter(groups.values()))


def cross_source_param(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    observations = _parameter_observations(context, params.get("parameter"))
    grouped = _same_scope_group(observations)
    if grouped is None or len(grouped) < 2:
        return _unknown("cross-source comparison requires two same-scope observations")
    source_types = {
        context.semantic_index.spans_by_id[span_id].block_type
        for item in grouped
        for span_id in item.source_span_ids
        if context.semantic_index is not None and span_id in context.semantic_index.spans_by_id
    }
    if BlockType.TABLE_CELL not in source_types or BlockType.PARAGRAPH not in source_types:
        return _unknown("cross-source comparison sides are incomplete", [span for item in grouped for span in item.source_span_ids])
    if any(not equivalent_units(grouped[0], item) for item in grouped[1:]):
        return _unknown("units or dimensions cannot be normalized", [span for item in grouped for span in item.source_span_ids])
    values = [item.normalized_value for item in grouped]
    if any(value is None for value in values):
        return _unknown("normalized values are incomplete")
    tolerance = float(params.get("relative_tolerance", 1e-6))
    reference = values[0]
    mismatch = any(
        not math.isclose(reference, value, rel_tol=tolerance, abs_tol=1e-9)
        for value in values[1:]
    )
    return _result(
        RuleStatus.FAIL if mismatch else RuleStatus.PASS,
        "cross-source parameter mismatch" if mismatch else "cross-source parameter consistent",
        grouped,
        details={"normalized_values": values, "scope_key": grouped[0].scope_key},
    )


def summary_detail(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    target = _facts(context, [params.get("target")])
    components = _facts(context, params.get("components"))
    selected = [*target, *components]
    if not target or not components or len(target) != 1 or any(len(_facts(context, [name])) != 1 for name in params.get("components", [])):
        return _unknown("summary and detail facts are incomplete")
    if len({item.scope_key for item in selected}) != 1:
        return _unknown("summary and detail scopes differ", [span for item in selected for span in item.source_span_ids])
    if any(not equivalent_units(target[0], item) for item in components):
        return _unknown("summary and detail units are not compatible")
    if any(item.normalized_value is None for item in selected):
        return _unknown("summary and detail values are incomplete")
    expected = sum(item.normalized_value for item in components)
    actual = target[0].normalized_value
    tolerance = float(params.get("absolute_tolerance", 0.0))
    relative = float(params.get("relative_tolerance", 1e-6))
    matches = math.isclose(actual, expected, rel_tol=relative, abs_tol=tolerance)
    return _result(
        RuleStatus.FAIL if not matches else RuleStatus.PASS,
        "summary does not equal detail" if not matches else "summary equals detail",
        selected,
        details={"summary": actual, "detail_sum": expected, "tolerance": tolerance},
    )


def unit_magnitude(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    observations = _parameter_observations(context, params.get("parameter"))
    grouped = _same_scope_group(observations)
    if grouped is None or len(grouped) < 2:
        return _unknown("magnitude comparison requires two same-scope observations")
    if any(not equivalent_units(grouped[0], item) for item in grouped[1:]):
        return _unknown("units cannot be converted to one dimension")
    values = [item.normalized_value for item in grouped]
    if any(value is None or value == 0 for value in values):
        return _unknown("magnitude values are incomplete or zero")
    ratio = max(values) / min(values)
    threshold = float(params.get("ratio_threshold", 10.0))
    mismatch = ratio >= threshold
    return _result(
        RuleStatus.FAIL if mismatch else RuleStatus.PASS,
        "same-object magnitude conflict" if mismatch else "same-object magnitude consistent",
        grouped,
        details={"ratio": ratio, "ratio_threshold": threshold},
    )


def _parse_month(value: str | None) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(20\d{2})\D{0,3}(1[0-2]|0?[1-9])", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _months_inclusive(start: tuple[int, int], end: tuple[int, int]) -> int:
    return (end[0] - start[0]) * 12 + end[1] - start[1] + 1


def schedule(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    names = [params.get("start"), params.get("end"), params.get("duration")]
    if not all(isinstance(item, str) and item for item in names):
        return _unknown("schedule fields are not configured")
    observations = [
        item for name in names[:2] for item in _parameter_observations(context, name)
    ]
    start = _parameter_observations(context, names[0])
    end = _parameter_observations(context, names[1])
    duration = _parameter_observations(context, names[2])
    if len(start) != 1 or len(end) != 1 or len(duration) != 1:
        return _unknown("schedule fields are incomplete")
    start_month = _parse_month(start[0].source_text)
    end_month = _parse_month(end[0].source_text)
    duration_value = duration[0].normalized_value
    if start_month is None or end_month is None or duration_value is None:
        return _unknown("schedule dates or duration cannot be normalized")
    inclusive = bool(params.get("inclusive_months", True))
    expected = _months_inclusive(start_month, end_month) if inclusive else max(0, _months_inclusive(start_month, end_month) - 1)
    actual_months = duration_value / 30.0 if duration[0].unit_dimension == "duration" else duration_value
    matches = math.isclose(actual_months, expected, rel_tol=float(params.get("relative_tolerance", 0.05)), abs_tol=0.1)
    selected = [start[0], end[0], duration[0]]
    return _result(
        RuleStatus.FAIL if not matches else RuleStatus.PASS,
        "schedule duration conflicts with endpoints" if not matches else "schedule is consistent",
        selected,
        details={"expected_months": expected, "actual_months": actual_months},
    )


def equipment_redundancy(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    names = [params.get(key) for key in ("total", "running", "standby", "single_capacity", "demand")]
    if not all(isinstance(item, str) and item for item in names):
        return _unknown("equipment relation fields are not configured")
    selected = [item for name in names for item in _parameter_observations(context, name)]
    if len(selected) != len(names):
        return _unknown("equipment relation fields are incomplete")
    by_name = {name: _parameter_observations(context, name)[0] for name in names}
    if any(item.normalized_value is None for item in selected):
        return _unknown("equipment values are incomplete")
    total = by_name[names[0]].normalized_value
    running = by_name[names[1]].normalized_value
    standby = by_name[names[2]].normalized_value
    single = by_name[names[3]].normalized_value
    demand = by_name[names[4]].normalized_value
    count_values = [by_name[name] for name in names[:3]]
    if any(item.unit_dimension != "count" for item in count_values) or by_name[names[3]].unit_dimension is None:
        return _unknown("equipment units are not comparable")
    valid = math.isclose(total, running + standby, rel_tol=1e-6, abs_tol=1e-9) and running * single >= demand
    return _result(
        RuleStatus.PASS if valid else RuleStatus.FAIL,
        "equipment redundancy is consistent" if valid else "equipment redundancy conflicts with demand",
        selected,
        details={"total": total, "running": running, "standby": standby, "available_capacity": running * single, "demand": demand},
    )


def reference(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    references = params.get("references")
    if not isinstance(references, list) or not references:
        return _unknown("reference targets are not configured")
    section_values = [part.casefold() for span in context.spans for part in span.section_path]
    table_values = {
        span.table_index for span in context.spans
        if span.block_type is BlockType.TABLE_CELL and span.table_index is not None
    }
    relevant: list[str] = []
    missing: list[str] = []
    for reference_item in references:
        if (
            not isinstance(reference_item, dict)
            or not isinstance(reference_item.get("target"), str)
            or not reference_item.get("target", "").strip()
        ):
            return _unknown("reference target is malformed")
        target = reference_item["target"]
        relevant.extend(reference_item.get("evidence_span_ids") or [])
        table_match = re.search(r"(?:table|表)\s*[-#]?(\d+)", target, re.I)
        exists = (
            any(target.casefold() in section for section in section_values)
            or (table_match is not None and int(table_match.group(1)) in table_values)
            or any(target.casefold() in span.text.casefold() for span in context.spans)
        )
        if not exists:
            missing.append(target)
    return _result(
        RuleStatus.FAIL if missing else RuleStatus.PASS,
        "reference target missing" if missing else "reference targets exist",
        [],
        spans=relevant,
        details={"missing_targets": missing},
    )


V12_OPERATORS: dict[str, Callable[[OperatorContext, dict[str, Any]], OperatorOutcome]] = {
    "reference_v12": reference,
    "summary_detail_v12": summary_detail,
    "cross_source_param_v12": cross_source_param,
    "unit_magnitude_v12": unit_magnitude,
    "schedule_v12": schedule,
    "equipment_redundancy_v12": equipment_redundancy,
}
