"""Shared, conservative semantic normalization for V1.2 rules.

The layer is deliberately data-driven: it normalizes extracted facts and
stable document anchors, but never decides PASS/FAIL and never reads test
manifests or file names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Iterable

from app.domain.schemas import ParameterFact, SourceSpan


_SPACE_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


@dataclass(frozen=True)
class NormalizedUnit:
    unit: str
    dimension: str
    multiplier: float


@dataclass(frozen=True)
class SemanticObservation:
    fact_id: str
    source_span_ids: tuple[str, ...]
    canonical_object_key: str
    parameter_semantic_type: str
    normalized_value: float | None
    normalized_unit: str | None
    unit_dimension: str | None
    scope_key: str
    operating_condition: str | None
    time_scope: str | None
    statistical_scope: str | None
    condition: str | None
    stable_evidence_anchors: tuple[str, ...]
    source_text: str


@dataclass
class SemanticIndex:
    observations: list[SemanticObservation] = field(default_factory=list)
    spans_by_id: dict[str, SourceSpan] = field(default_factory=dict)

    def by_object(self, key: str | None) -> list[SemanticObservation]:
        return [item for item in self.observations if item.canonical_object_key == key]

    def by_parameter_type(self, parameter_type: str) -> list[SemanticObservation]:
        return [item for item in self.observations if item.parameter_semantic_type == parameter_type]


_UNIT_ALIASES: dict[str, NormalizedUnit] = {
    "m": NormalizedUnit("m", "length", 1.0),
    "meter": NormalizedUnit("m", "length", 1.0),
    "meters": NormalizedUnit("m", "length", 1.0),
    "km": NormalizedUnit("m", "length", 1000.0),
    "mm": NormalizedUnit("m", "length", 0.001),
    "cm": NormalizedUnit("m", "length", 0.01),
    "pa": NormalizedUnit("Pa", "pressure", 1.0),
    "kpa": NormalizedUnit("Pa", "pressure", 1000.0),
    "mpa": NormalizedUnit("Pa", "pressure", 1_000_000.0),
    "bar": NormalizedUnit("Pa", "pressure", 100_000.0),
    "day": NormalizedUnit("day", "duration", 1.0),
    "days": NormalizedUnit("day", "duration", 1.0),
    "d": NormalizedUnit("day", "duration", 1.0),
    "month": NormalizedUnit("day", "duration", 30.0),
    "months": NormalizedUnit("day", "duration", 30.0),
    "月": NormalizedUnit("day", "duration", 30.0),
    "天": NormalizedUnit("day", "duration", 1.0),
    "year": NormalizedUnit("day", "duration", 365.0),
    "years": NormalizedUnit("day", "duration", 365.0),
    "年": NormalizedUnit("day", "duration", 365.0),
    "cny": NormalizedUnit("CNY", "currency", 1.0),
    "元": NormalizedUnit("CNY", "currency", 1.0),
    "万元": NormalizedUnit("CNY", "currency", 10_000.0),
    "count": NormalizedUnit("count", "count", 1.0),
    "个": NormalizedUnit("count", "count", 1.0),
    "台": NormalizedUnit("count", "count", 1.0),
    "套": NormalizedUnit("count", "count", 1.0),
}


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE_RE.sub(" ", value.strip().casefold())


def canonical_object_key(
    canonical_name: str | None,
    subject: str | None = None,
    *,
    parameter_semantic_type: str | None = None,
) -> str | None:
    """Return a stable object key while preserving scope separately."""
    name = _clean(canonical_name)
    if not name:
        return None
    subject_key = _clean(subject)
    type_key = _clean(parameter_semantic_type)
    parts = [part for part in (name, subject_key, type_key) if part]
    return "::".join(parts)


def parameter_semantic_type(
    canonical_name: str | None,
    unit: str | None = None,
    unit_category: str | None = None,
) -> str:
    unit_meta = normalize_unit(unit, unit_category)
    if unit_meta is not None:
        return unit_meta.dimension
    text = _clean(canonical_name)
    if any(term in text for term in ("pressure", "压力")):
        return "pressure"
    if any(term in text for term in ("length", "distance", "diameter", "长度", "管径")):
        return "length"
    if any(term in text for term in ("duration", "schedule", "工期", "周期", "时间")):
        return "duration"
    if any(term in text for term in ("amount", "cost", "investment", "金额", "投资")):
        return "currency"
    if any(term in text for term in ("count", "quantity", "number", "数量", "台数")):
        return "count"
    return "unknown"


def normalize_unit(unit: str | None, unit_category: str | None = None) -> NormalizedUnit | None:
    text = _clean(unit).replace("³", "^3")
    if text in _UNIT_ALIASES:
        return _UNIT_ALIASES[text]
    if unit_category == "count":
        return _UNIT_ALIASES["count"]
    return None


def normalized_value(raw_value: object, unit: str | None = None, unit_category: str | None = None) -> tuple[float | None, str | None, str | None]:
    try:
        value = float(raw_value) if not isinstance(raw_value, str) else float(_NUMBER_RE.search(raw_value).group(0))
    except (AttributeError, TypeError, ValueError):
        return None, None, None
    if not math.isfinite(value):
        return None, None, None
    metadata = normalize_unit(unit, unit_category)
    if metadata is None:
        return value, None, None
    return value * metadata.multiplier, metadata.unit, metadata.dimension


def scope_key(
    *,
    subject: str | None,
    time_scope: str | None,
    statistical_scope: str | None,
    condition: str | None,
) -> str:
    values = (
        _clean(subject) or "<none>",
        _clean(time_scope) or "<none>",
        _clean(statistical_scope) or "<none>",
        _clean(condition) or "<none>",
    )
    return "|".join(values)


def stable_evidence_anchor(span: SourceSpan) -> str:
    location = (
        f"table:{span.table_index}:row:{span.row_index}:col:{span.column_index}"
        if span.table_index is not None
        else f"paragraph:{span.paragraph_index}"
    )
    return "|".join([span.document_id, "/".join(span.section_path), location])


def build_semantic_index(
    facts: Iterable[ParameterFact],
    spans: Iterable[SourceSpan],
) -> SemanticIndex:
    span_list = list(spans)
    index = SemanticIndex(spans_by_id={span.span_id: span for span in span_list})
    for fact in facts:
        source = index.spans_by_id.get(fact.source_span_id)
        semantic_type = parameter_semantic_type(
            fact.canonical_name, fact.canonical_unit or fact.raw_unit, fact.unit_category
        )
        object_key = canonical_object_key(
            fact.canonical_name, fact.subject, parameter_semantic_type=semantic_type
        )
        if object_key is None:
            continue
        value, unit, dimension = normalized_value(
            fact.normalized_value if fact.normalized_value is not None else fact.raw_value,
            fact.canonical_unit or fact.raw_unit,
            fact.unit_category,
        )
        anchors = []
        if source is not None:
            anchors.append(stable_evidence_anchor(source))
        anchors.extend(
            stable_evidence_anchor(index.spans_by_id[item])
            for item in fact.merged_span_ids
            if item in index.spans_by_id
        )
        index.observations.append(
            SemanticObservation(
                fact_id=fact.fact_id,
                source_span_ids=tuple(dict.fromkeys([fact.source_span_id, *fact.merged_span_ids])),
                canonical_object_key=object_key,
                parameter_semantic_type=semantic_type,
                normalized_value=value,
                normalized_unit=unit,
                unit_dimension=dimension,
                scope_key=scope_key(
                    subject=fact.subject,
                    time_scope=fact.time_scope,
                    statistical_scope=fact.statistical_scope,
                    condition=fact.condition,
                ),
                operating_condition=fact.condition,
                time_scope=fact.time_scope,
                statistical_scope=fact.statistical_scope,
                condition=fact.condition,
                stable_evidence_anchors=tuple(dict.fromkeys(anchors)),
                source_text=source.text if source is not None else fact.raw_value,
            )
        )
    return index


def same_scope(left: SemanticObservation, right: SemanticObservation) -> bool:
    return left.scope_key == right.scope_key


def equivalent_units(left: SemanticObservation, right: SemanticObservation) -> bool:
    return (
        left.unit_dimension is not None
        and left.unit_dimension == right.unit_dimension
        and left.normalized_value is not None
        and right.normalized_value is not None
    )
