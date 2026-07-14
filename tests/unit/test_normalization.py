import pytest

from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact
from app.extraction.normalization import normalize_facts_units, normalize_value


def test_gas_rate_converts_to_cubic_meter_per_day():
    value, unit = normalize_value("5", "万m³/d")
    assert value == pytest.approx(50000.0)
    assert unit == "m^3/day"


def test_comma_number_is_parsed():
    value, unit = normalize_value("1,200", "口")
    assert value == 1200.0
    assert unit == "口"


def test_unknown_unit_does_not_guess():
    value, unit = normalize_value("5", "神秘单位")
    assert value is None
    assert unit is None


def test_fact_update_is_immutable():
    original = ParameterFact(
        fact_id="f", canonical_name="高峰产量", raw_name="高峰产量", raw_value="5",
        raw_unit="万m³/d", source_document="D", source_span_id="s",
        extraction_method=ExtractionMethod.TABLE,
    )
    result = normalize_facts_units([original])[0]
    assert original.normalized_value is None
    assert result.normalized_value == 50000
