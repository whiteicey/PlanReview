import pytest

from app.diff.pairing import (
    PairingConfirmationRequired,
    PairingTier,
    assess_pair,
    assert_pairing_confirmed,
    pair_documents,
)


def test_pairs_adjacent_versions_with_the_same_document_stem():
    assert pair_documents(["方案_V2.docx", "附件.docx", "方案_V1.docx", "方案_V3.docx"]) == [
        ("方案_V1.docx", "方案_V2.docx"),
        ("方案_V2.docx", "方案_V3.docx"),
    ]


def test_pairs_date_versions_when_no_v_marker_exists():
    assert pair_documents(["方案_2024-10-01.docx", "方案_2024-01-01.docx", "附件.docx"]) == [
        ("方案_2024-01-01.docx", "方案_2024-10-01.docx")
    ]


def test_v_marker_precedes_dates_in_mixed_stem_deterministically():
    assert pair_documents(
        ["方案_2024-01-01.docx", "方案_V1.docx", "方案_V2.docx", "方案_2024-02-01.docx"]
    ) == [("方案_V1.docx", "方案_V2.docx")]


def test_pair_assessment_uses_explicit_weighted_score_and_thresholds():
    high = assess_pair("方案_V1.docx", "方案_V2.docx", title_or_directory_match=True)
    medium = assess_pair("方案_V1.docx", "方案_V2.docx")
    low = assess_pair(
        "甲_V1.docx", "乙_V2.docx", filename_match=False, title_or_directory_match=False
    )

    assert high.score == pytest.approx(0.80)
    assert high.tier is PairingTier.HIGH_CONFIDENCE
    assert medium.score == pytest.approx(0.55)
    assert medium.tier is PairingTier.REQUIRES_CONFIRMATION
    assert low.score == pytest.approx(0.0)
    assert low.tier is PairingTier.REJECTED


def test_pairing_cannot_be_used_for_comparison_until_human_confirmed():
    assessment = assess_pair("方案_V1.docx", "方案_V2.docx", title_or_directory_match=True)

    with pytest.raises(PairingConfirmationRequired):
        assert_pairing_confirmed(assessment)

    confirmed = assessment.confirm()
    assert_pairing_confirmed(confirmed) == confirmed
