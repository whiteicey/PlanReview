import math

import pytest

from app.diff.parameter_diff import diff_parameters
from app.diff.pairing import PairingConfirmationRequired, assess_pair
from app.domain.enums import DiffKind, ExtractionMethod
from app.domain.schemas import ParameterFact
from app.extraction.normalization import normalize_facts_units


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


def confirmed_pair():
    return assess_pair("方案_V1.docx", "方案_V2.docx", filename_match=True).confirm()


def test_unconfirmed_or_missing_pairing_is_rejected():
    old = make_fact("o", "高峰产量", 170)
    new = make_fact("n", "高峰产量", 170)

    with pytest.raises(TypeError):
        diff_parameters([old], [new])
    with pytest.raises(PairingConfirmationRequired):
        diff_parameters([old], [new], pairing=assess_pair("old.docx", "new.docx", filename_match=True))


def test_scope_difference_is_unknown_not_added_or_removed():
    old = make_fact("o", "高峰产量", 170, time_scope="试运行")
    new = make_fact("n", "高峰产量", 170, time_scope="达产期")

    difference = diff_parameters([old], [new], pairing=confirmed_pair())

    assert len(difference) == 1
    assert difference[0].old is old
    assert difference[0].new is new
    assert difference[0].kind is DiffKind.UNKNOWN_SCOPE


def test_missing_condition_is_unknown_scope_even_when_values_match():
    old = make_fact("o", "高峰产量", 170, condition=None)
    new = make_fact("n", "高峰产量", 170)

    assert diff_parameters([old], [new], pairing=confirmed_pair())[0].kind is DiffKind.UNKNOWN_SCOPE


def test_missing_subject_is_unknown_scope_not_added_or_removed():
    old = make_fact("o", "高峰产量", 170, subject=None)
    new = make_fact("n", "高峰产量", 170)

    difference = diff_parameters([old], [new], pairing=confirmed_pair())

    assert len(difference) == 1
    assert difference[0].kind is DiffKind.UNKNOWN_SCOPE


def test_value_difference_is_changed_after_unit_normalization():
    old = make_fact("o", "高峰产量", 170, raw_unit="万m³/d", canonical_unit="m^3/day", normalized_value=1_700_000)
    new = make_fact("n", "高峰产量", 1700, raw_unit="m³/d", canonical_unit="m^3/day", normalized_value=1_700)

    assert diff_parameters([old], [new], pairing=confirmed_pair())[0].kind is DiffKind.CHANGED


def test_complete_identical_five_dimensions_are_unchanged():
    old = make_fact("o", "高峰产量", 170)
    new = make_fact("n", "高峰产量", 170)

    difference = diff_parameters([old], [new], pairing=confirmed_pair())[0]

    assert difference.key == ("高峰产量", "全区", "全生命周期", "累计", "标准工况")
    assert difference.kind is DiffKind.UNCHANGED


def test_subject_only_scope_change_is_unknown_not_added_or_removed():
    old = make_fact("o", "高峰产量", 170, subject="一区")
    new = make_fact("n", "高峰产量", 170, subject="二区")

    differences = diff_parameters([old], [new], pairing=confirmed_pair())

    assert len(differences) == 1
    assert differences[0].kind is DiffKind.UNKNOWN_SCOPE


def test_unmatched_name_remains_added_or_removed():
    old = make_fact("o", "高峰产量", 170)
    new = make_fact("n", "低峰产量", 170)

    differences = diff_parameters([old], [new], pairing=confirmed_pair())

    assert sorted(difference.kind for difference in differences) == sorted(
        [DiffKind.REMOVED, DiffKind.ADDED]
    )


def test_normalization_is_required_for_finite_compatible_comparisons():
    old = make_fact("o", "高峰产量", 170, raw_unit="万m³/d", normalized_value=None)
    new = make_fact("n", "高峰产量", 1700, raw_unit="m³/d", normalized_value=None)
    old_normalized, new_normalized = normalize_facts_units([old, new])

    assert old_normalized.normalized_value == 1_700_000
    assert new_normalized.normalized_value == 1700
    assert diff_parameters([old_normalized], [new_normalized], pairing=confirmed_pair())[0].kind is DiffKind.CHANGED


def test_nonfinite_or_incompatible_units_are_unknown_scope():
    old = make_fact("o", "高峰产量", 170, normalized_value=math.nan, canonical_unit="m^3/day")
    new = make_fact("n", "高峰产量", 170, normalized_value=170, canonical_unit="m^3/day")
    assert diff_parameters([old,], [new], pairing=confirmed_pair())[0].kind is DiffKind.UNKNOWN_SCOPE

    incompatible = make_fact("i", "高峰产量", 170, raw_unit="个月", normalized_value=170, canonical_unit="个月")
    assert diff_parameters([incompatible], [new], pairing=confirmed_pair())[0].kind is DiffKind.UNKNOWN_SCOPE


def test_duplicate_keys_and_unequal_cardinality_are_ambiguous_unknowns():
    old_one = make_fact("o1", "高峰产量", 170)
    old_two = make_fact("o2", "高峰产量", 175)
    new_one = make_fact("n1", "高峰产量", 170)

    differences = diff_parameters([old_one, old_two], [new_one], pairing=confirmed_pair())

    assert len(differences) == 1
    assert differences[0].kind is DiffKind.UNKNOWN_SCOPE
    assert differences[0].old is None or differences[0].new is None


def test_reordered_duplicate_keys_do_not_pair_by_fact_id():
    old_one = make_fact("z", "高峰产量", 170)
    old_two = make_fact("a", "高峰产量", 220)
    new_one = make_fact("b", "高峰产量", 220)
    new_two = make_fact("y", "高峰产量", 170)

    differences = diff_parameters([old_one, old_two], [new_one, new_two], pairing=confirmed_pair())

    assert len(differences) == 1
    assert differences[0].kind is DiffKind.UNKNOWN_SCOPE
