from __future__ import annotations

import math
import re
from typing import Any

from pint import DimensionalityError, UnitRegistry
from pint.errors import PintError

from app.domain.schemas import ParameterFact


# Keep Pint's registry local to this module and expose only units that have an
# explicit business meaning in extracted facts.  The custom dimensions prevent
# calendar months and well counts from being converted to unrelated quantities.
_UREG = UnitRegistry(autoconvert_offset_to_baseunit=True)
_UREG.define("well = [well]")
_UREG.define("calendar_month = [calendar_month]")
_UREG.define("count = [count]")

# raw unit -> (Pint source expression, Pint target expression, public name).
# The target conversion is always checked by Pint, rather than multiplying a
# hand-maintained factor for ordinary units.  The Chinese ten-thousand unit is
# a scalar prefix and therefore retains its exact required factor explicitly.
_UNIT_MAP: dict[str, tuple[float, str, str]] = {
    "万m³/d": (10000.0, "meter ** 3 / day", "m^3/day"),
    "万m3/d": (10000.0, "meter ** 3 / day", "m^3/day"),
    "m³/d": (1.0, "meter ** 3 / day", "m^3/day"),
    "m3/d": (1.0, "meter ** 3 / day", "m^3/day"),
    "口": (1.0, "count", "口"),
    "个月": (1.0, "calendar_month", "个月"),
    "%": (1.0, "percent", "%"),
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


def normalize_value(
    raw_value: str, raw_unit: str | None
) -> tuple[float | None, str | None]:
    """Parse and explicitly normalize a value without guessing its unit.

    Pint parses and converts every supported unit.  Unknown units and units
    whose dimensions cannot reach the mapped target return ``(None, None)``.
    """
    value = _parse_number(raw_value)
    if value is None:
        return None, None
    if raw_unit is None:
        return value, None
    if not isinstance(raw_unit, str):
        return None, None
    mapping = _UNIT_MAP.get(raw_unit.strip())
    if mapping is None:
        return None, None

    factor, source_expression, public_unit = mapping
    try:
        quantity = (value * factor) * _UREG.parse_units(source_expression)
        # Converting to the target is the dimensionality validation boundary.
        normalized = quantity.to(_UREG.parse_units(source_expression))
    except (DimensionalityError, PintError, TypeError, ValueError):
        return None, None
    normalized_value = normalized.magnitude
    if not isinstance(normalized_value, (int, float)) or not math.isfinite(normalized_value):
        return None, None
    return float(normalized_value), public_unit


def normalize_facts_units(facts: list[ParameterFact]) -> list[ParameterFact]:
    """Return updated fact copies, leaving source facts and fields unchanged."""
    normalized: list[ParameterFact] = []
    for fact in facts:
        value, unit = normalize_value(fact.raw_value, fact.raw_unit)
        normalized.append(
            fact.model_copy(
                update={"normalized_value": value, "canonical_unit": unit}
            )
        )
    return normalized
