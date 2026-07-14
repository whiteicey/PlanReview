from app.diff.parameter_diff import diff_parameters
from app.domain.enums import DiffKind, ExtractionMethod
from app.domain.schemas import ParameterFact


def make_fact(fid: str, name: str, value: float | None, **overrides: object) -> ParameterFact:
    values = {
        "fact_id": fid,
        "canonical_name": name,
        "raw_name": name,
        "raw_value": str(value),
        "normalized_value": value,
        "source_document": "D",
        "source_span_id": fid,
        "extraction_method": ExtractionMethod.TABLE,
        "subject": "全区",
        "time_scope": "全生命周期",
        "statistical_scope": "累计",
        "condition": "标准工况",
    }
    values.update(overrides)
    return ParameterFact(**values)


def test_scope_difference_is_unknown_not_added_or_removed():
    old = make_fact("o", "高峰产量", 170, time_scope="试运行")
    new = make_fact("n", "高峰产量", 170, time_scope="达产期")

    difference = diff_parameters([old], [new])

    assert len(difference) == 1
    assert difference[0].old is old
    assert difference[0].new is new
    assert difference[0].kind is DiffKind.UNKNOWN_SCOPE


def test_missing_condition_is_unknown_scope_even_when_values_match():
    old = make_fact("o", "高峰产量", 170, condition=None)
    new = make_fact("n", "高峰产量", 170)

    assert diff_parameters([old], [new])[0].kind is DiffKind.UNKNOWN_SCOPE


def test_missing_subject_is_unknown_scope_not_added_or_removed():
    old = make_fact("o", "高峰产量", 170, subject=None)
    new = make_fact("n", "高峰产量", 170)

    difference = diff_parameters([old], [new])

    assert len(difference) == 1
    assert difference[0].kind is DiffKind.UNKNOWN_SCOPE


def test_value_difference_is_changed_after_unit_normalization():
    old = make_fact("o", "高峰产量", 170, raw_unit="万m³/d", canonical_unit="m^3/day", normalized_value=1_700_000)
    new = make_fact("n", "高峰产量", 1700, raw_unit="m³/d", canonical_unit="m^3/day", normalized_value=1_700)

    assert diff_parameters([old], [new])[0].kind is DiffKind.CHANGED


def test_complete_identical_five_dimensions_are_unchanged():
    old = make_fact("o", "高峰产量", 170)
    new = make_fact("n", "高峰产量", 170)

    difference = diff_parameters([old], [new])[0]

    assert difference.key == ("高峰产量", "全区", "全生命周期", "累计", "标准工况")
    assert difference.kind is DiffKind.UNCHANGED


def test_unmatched_name_or_subject_remains_added_or_removed():
    old = make_fact("o", "高峰产量", 170, subject="一区")
    new = make_fact("n", "高峰产量", 170, subject="二区")

    differences = diff_parameters([old], [new])

    assert [difference.kind for difference in differences] == [DiffKind.REMOVED, DiffKind.ADDED]
