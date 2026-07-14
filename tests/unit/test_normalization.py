import math

import pytest

from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact
from app.extraction import normalization
from app.extraction.normalization import normalize_facts_units, normalize_value


def test_gas_rate_converts_to_cubic_meter_per_day():
    value, unit = normalize_value("5", "万m³/d")
    assert value == pytest.approx(50000.0)
    assert unit == "m^3/day"


def test_ascii_gas_rate_converts_to_cubic_meter_per_day():
    value, unit = normalize_value("5", "万m3/d")
    assert value == pytest.approx(50000.0)
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
        (1.0, "meter", "second", "故意不兼容"),
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
    assert result is not original


def test_normalized_values_are_finite():
    value, _ = normalize_value("1e300", "万m3/d")
    assert value is not None and math.isfinite(value)
