from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from pint import DimensionalityError, UnitRegistry
from pint.errors import PintError

from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact


DAYS_PER_YEAR = 365.0

# Keep Pint's registry local to this module and expose only units that have an
# explicit business meaning in extracted facts.  The custom dimensions prevent
# calendar months and counts from being converted to unrelated quantities.
_UREG = UnitRegistry(autoconvert_offset_to_baseunit=True)
_UREG.define("calendar_month = [calendar_month]")
_UREG.define("count = [count]")


@dataclass(frozen=True)
class UnitDefinition:
    factor: float
    source_expression: str
    target_expression: str
    public_unit: str
    category: str


# raw unit -> explicit conversion and unit-category metadata.  Both Pint
# expressions are intentional: conversion is the dimensionality validation
# boundary and must fail closed for incompatible mappings.
_UNIT_MAP: dict[str, UnitDefinition] = {
    "亿m³/a": UnitDefinition(
        100000000.0 / DAYS_PER_YEAR,
        "meter ** 3 / day",
        "meter ** 3 / day",
        "m^3/day",
        "flow",
    ),
    "亿m3/a": UnitDefinition(
        100000000.0 / DAYS_PER_YEAR,
        "meter ** 3 / day",
        "meter ** 3 / day",
        "m^3/day",
        "flow",
    ),
    "万m³/d": UnitDefinition(10000.0, "meter ** 3 / day", "meter ** 3 / day", "m^3/day", "flow"),
    "万m3/d": UnitDefinition(10000.0, "meter ** 3 / day", "meter ** 3 / day", "m^3/day", "flow"),
    "m³/d": UnitDefinition(1.0, "meter ** 3 / day", "meter ** 3 / day", "m^3/day", "flow"),
    "m3/d": UnitDefinition(1.0, "meter ** 3 / day", "meter ** 3 / day", "m^3/day", "flow"),
    "口": UnitDefinition(1.0, "count", "count", "口", "count"),
    "座": UnitDefinition(1.0, "count", "count", "座", "count"),
    "台": UnitDefinition(1.0, "count", "count", "台", "count"),
    "套": UnitDefinition(1.0, "count", "count", "套", "count"),
    "个月": UnitDefinition(1.0, "calendar_month", "calendar_month", "个月", "calendar"),
    "月": UnitDefinition(1.0, "calendar_month", "calendar_month", "个月", "calendar"),
    "%": UnitDefinition(1.0, "percent", "percent", "%", "percent"),
}

_NUMBER = re.compile(
    r"^[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


def _parse_number(raw_value: Any) -> float | None:
    if not isinstance(raw_value, (str, int, float)) or isinstance(raw_value, bool):
        return None
    text = str(raw_value).strip()
    if not text or not _NUMBER.fullmatch(text):
        return None
    try:
        value = float(text.replace(",", ""))
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _normalize_value_typed(
    raw_value: str, raw_unit: str | None
) -> tuple[float | None, str | None, str | None, str | None]:
    """Parse and explicitly normalize a value without guessing its unit."""

    text = str(raw_value).strip()
    if re.fullmatch(r"\d{4}-\d{1,2}", text):
        year, month = (int(part) for part in text.split("-"))
        if not 1 <= month <= 12:
            return None, None, None, None
        return float(year * 12 + month), "month_index", "date", None
    value = _parse_number(raw_value)
    if value is None:
        return None, None, None, None
    if raw_unit is None:
        return value, None, None, None
    if not isinstance(raw_unit, str):
        return None, None, None, None
    mapping = _UNIT_MAP.get(raw_unit.strip())
    if mapping is None:
        return None, None, None, None

    try:
        quantity = (value * mapping.factor) * _UREG.parse_units(mapping.source_expression)
        normalized = quantity.to(_UREG.parse_units(mapping.target_expression))
    except (DimensionalityError, PintError, TypeError, ValueError):
        return None, None, None, None
    normalized_value = normalized.magnitude
    if not isinstance(normalized_value, (int, float)) or not math.isfinite(normalized_value):
        return None, None, None, None
    return float(normalized_value), mapping.public_unit, None, mapping.category


def normalize_value(raw_value: str, raw_unit: str | None) -> tuple[float | None, str | None]:
    value, unit, _, _ = _normalize_value_typed(raw_value, raw_unit)
    return value, unit


def normalize_facts_units(facts: list[ParameterFact]) -> list[ParameterFact]:
    """Return updated fact copies, leaving source facts and fields unchanged."""

    normalized: list[ParameterFact] = []
    for fact in facts:
        value, unit, normalized_type, unit_category = _normalize_value_typed(
            fact.raw_value, fact.raw_unit
        )
        normalized.append(
            fact.model_copy(
                update={
                    "normalized_value": value,
                    "canonical_unit": unit,
                    "unit_category": unit_category,
                    "normalized_type": normalized_type,
                }
            )
        )
    return normalized


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _deduplication_key(fact: ParameterFact) -> tuple[object, ...] | None:
    if (
        fact.normalized_value is None
        or fact.canonical_unit is None
        or fact.unit_category is None
    ):
        return None
    return (
        fact.source_document,
        fact.source_version,
        fact.canonical_name,
        fact.normalized_value,
        fact.canonical_unit,
        fact.unit_category,
    )


def coalesce_redundant_unscoped_facts(
    facts: list[ParameterFact],
) -> list[ParameterFact]:
    """Merge only exact unscoped prose duplicates into complete table facts.

    The surviving table fact retains the absorbed fact and span identifiers so
    rule outcomes can cite every source occurrence. Conflicting, unit-incomplete,
    scoped, cross-document, or cross-version facts are never removed.
    """

    table_fact_by_key: dict[tuple[object, ...], str] = {}
    for fact in facts:
        key = _deduplication_key(fact)
        if (
            key is not None
            and fact.extraction_method is ExtractionMethod.TABLE
            and fact.has_complete_key
        ):
            table_fact_by_key.setdefault(key, fact.fact_id)

    absorbed_by_fact: dict[str, tuple[list[str], list[str]]] = {}
    absorbed_ids: set[str] = set()
    for fact in facts:
        key = _deduplication_key(fact)
        if (
            key is None
            or fact.extraction_method is not ExtractionMethod.REGEX
            or any(
                value is not None
                for value in (
                    fact.subject,
                    fact.time_scope,
                    fact.statistical_scope,
                    fact.condition,
                )
            )
        ):
            continue
        survivor_id = table_fact_by_key.get(key)
        if survivor_id is None or survivor_id == fact.fact_id:
            continue
        fact_ids, span_ids = absorbed_by_fact.setdefault(survivor_id, ([], []))
        fact_ids.extend([fact.fact_id, *fact.merged_fact_ids])
        span_ids.extend([fact.source_span_id, *fact.merged_span_ids])
        absorbed_ids.add(fact.fact_id)

    output: list[ParameterFact] = []
    for fact in facts:
        if fact.fact_id in absorbed_ids:
            continue
        additions = absorbed_by_fact.get(fact.fact_id)
        if additions is not None:
            fact_ids, span_ids = additions
            fact = fact.model_copy(
                update={
                    "merged_fact_ids": _stable_unique([*fact.merged_fact_ids, *fact_ids]),
                    "merged_span_ids": _stable_unique([*fact.merged_span_ids, *span_ids]),
                }
            )
        output.append(fact)
    return output
