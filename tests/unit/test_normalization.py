import math

import pytest

from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact
from app.extraction import normalization
from app.extraction.normalization import (
    coalesce_redundant_unscoped_facts,
    normalize_facts_units,
    normalize_value,
)


def test_gas_rate_converts_to_cubic_meter_per_day():
    value, unit = normalize_value("5", "万m³/d")
    assert value == pytest.approx(50000.0)
    assert unit == "m^3/day"


def test_ascii_gas_rate_converts_to_cubic_meter_per_day():
    value, unit = normalize_value("5", "万m3/d")
    assert value == pytest.approx(50000.0)
    assert unit == "m^3/day"


@pytest.mark.parametrize("raw_unit", ["亿m³/a", "亿m3/a"])
def test_annual_flow_uses_365_day_year(raw_unit):
    value, unit = normalize_value("10", raw_unit)
    assert value == pytest.approx(2739726.02739726)
    assert unit == "m^3/day"


@pytest.mark.parametrize("raw_unit", ["万m³/d", "万m3/d", "m³/d", "m3/d"])
def test_all_daily_flow_aliases_are_canonical(raw_unit):
    value, unit = normalize_value("3", raw_unit)
    expected = 30000.0 if raw_unit.startswith("万") else 3.0
    assert value == pytest.approx(expected)
    assert unit == "m^3/day"


def test_comma_number_is_parsed():
    value, unit = normalize_value("1,200", "口")
    assert value == 1200.0
    assert unit == "口"


def test_value_without_unit_keeps_numeric_value():
    assert normalize_value("12.5", None) == (12.5, None)


@pytest.mark.parametrize("raw_value", ["1,2", "not-a-number", "NaN", "inf", "-inf", "1e999"])
def test_malformed_or_nonfinite_number_does_not_normalize(raw_value):
    assert normalize_value(raw_value, "口") == (None, None)


def test_unknown_or_incompatible_unit_does_not_guess():
    assert normalize_value("5", "神秘单位") == (None, None)
    # A mass cannot be interpreted as a volumetric flow without an explicit
    # density; no dimensional conversion or business guess is permitted.
    assert normalize_value("5", "kg") == (None, None)


def test_incompatible_source_target_mapping_is_rejected(monkeypatch):
    monkeypatch.setitem(
        normalization._UNIT_MAP,
        "故意不兼容",
        normalization.UnitDefinition(1.0, "meter", "second", "故意不兼容", "length"),
    )
    assert normalize_value("5", "故意不兼容") == (None, None)


def test_fact_update_is_immutable_including_canonical_unit():
    original = ParameterFact(
        fact_id="f", canonical_name="高峰产量", raw_name="高峰产量", raw_value="5",
        normalized_value=7, raw_unit="万m³/d", canonical_unit="old-unit",
        source_document="D", source_span_id="s",
        extraction_method=ExtractionMethod.TABLE,
    )
    result = normalize_facts_units([original])[0]
    assert original.normalized_value == 7
    assert original.canonical_unit == "old-unit"
    assert result.normalized_value == 50000
    assert result.canonical_unit == "m^3/day"
    assert result.unit_category == "flow"
    assert result is not original


def test_normalized_values_are_finite():
    value, _ = normalize_value("1e300", "万m3/d")
    assert value is not None and math.isfinite(value)


def _fact(
    fact_id: str,
    span_id: str,
    value: str,
    *,
    method: ExtractionMethod,
    raw_unit: str | None = "万m3/d",
    complete: bool = False,
) -> ParameterFact:
    return ParameterFact(
        fact_id=fact_id,
        canonical_name="高峰产量",
        raw_name="高峰产量",
        raw_value=value,
        raw_unit=raw_unit,
        subject="气田_A" if complete else None,
        time_scope="达产期" if complete else None,
        statistical_scope="日峰值" if complete else None,
        source_document="D",
        source_version="V1.0",
        source_span_id=span_id,
        extraction_method=method,
    )


def test_exact_unscoped_prose_duplicate_is_merged_with_traceability():
    table = _fact("table-fact", "table-span", "5", method=ExtractionMethod.TABLE, complete=True)
    prose = _fact("prose-fact", "prose-span", "5", method=ExtractionMethod.REGEX)

    merged = coalesce_redundant_unscoped_facts(normalize_facts_units([table, prose]))

    assert len(merged) == 1
    assert merged[0].fact_id == "table-fact"
    assert merged[0].merged_fact_ids == ["prose-fact"]
    assert merged[0].merged_span_ids == ["prose-span"]


@pytest.mark.parametrize(
    "prose",
    [
        _fact("different-value", "value-span", "6", method=ExtractionMethod.REGEX),
        _fact("missing-unit", "unit-span", "5", method=ExtractionMethod.REGEX, raw_unit=None),
    ],
)
def test_conflicting_or_unit_incomplete_prose_fact_is_not_merged(prose):
    table = _fact("table-fact", "table-span", "5", method=ExtractionMethod.TABLE, complete=True)

    remaining = coalesce_redundant_unscoped_facts(normalize_facts_units([table, prose]))

    assert [item.fact_id for item in remaining] == ["table-fact", prose.fact_id]
