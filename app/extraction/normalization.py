from __future__ import annotations

import re
from typing import Any

from app.domain.schemas import ParameterFact


# Units are deliberately opt-in.  In particular, a unit that Pint happens to
# understand is not accepted unless it is present here: this prevents a parser
# from silently assigning a business meaning to an unfamiliar unit.
_UNIT_MAP: dict[str, tuple[float, str]] = {
    "万m³/d": (10000.0, "m^3/day"),
    "万m3/d": (10000.0, "m^3/day"),
    "m³/d": (1.0, "m^3/day"),
    "m3/d": (1.0, "m^3/day"),
    "口": (1.0, "口"),
    "个月": (1.0, "个月"),
    "%": (1.0, "%"),
}

# Accept ordinary decimal values and scientific notation, with commas only in
# valid three-digit groups.  This keeps comma removal from turning malformed
# input such as ``1,2`` into a plausible value.
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
        return float(text.replace(",", ""))
    except (OverflowError, ValueError):
        return None


def normalize_value(
    raw_value: str, raw_unit: str | None
) -> tuple[float | None, str | None]:
    """Parse a value and convert only explicitly supported units.

    Returning ``(None, None)`` for either an invalid number or an unknown unit
    is intentional: normalization must never infer a unit or conversion.
    """
    value = _parse_number(raw_value)
    if value is None:
        return None, None
    if raw_unit is None:
        return value, None
    if not isinstance(raw_unit, str):
        return None, None
    unit = raw_unit.strip()
    if unit not in _UNIT_MAP:
        return None, None
    factor, canonical_unit = _UNIT_MAP[unit]
    return value * factor, canonical_unit


def normalize_facts_units(facts: list[ParameterFact]) -> list[ParameterFact]:
    """Return updated fact copies, leaving the source facts unchanged."""
    normalized: list[ParameterFact] = []
    for fact in facts:
        value, unit = normalize_value(fact.raw_value, fact.raw_unit)
        normalized.append(
            fact.model_copy(
                update={"normalized_value": value, "canonical_unit": unit}
            )
        )
    return normalized
