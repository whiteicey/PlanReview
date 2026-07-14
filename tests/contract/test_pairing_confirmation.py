import pytest

from app.diff.pairing import (
    PairingConfirmationRequired,
    PairingTier,
    assess_pair,
    assert_pairing_confirmed,
)
from app.diff.parameter_diff import diff_parameters
from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact


@pytest.mark.parametrize(
    ("filename_match", "title_or_directory_match", "text_fingerprint_match", "score", "tier"),
    [
        (True, True, False, 0.80, PairingTier.HIGH_CONFIDENCE),
        (True, False, False, 0.55, PairingTier.REQUIRES_CONFIRMATION),
        (False, True, True, 0.45, PairingTier.REJECTED),
    ],
)
def test_pairing_score_weights_and_decision_thresholds_are_contractual(
    filename_match: bool,
    title_or_directory_match: bool,
    text_fingerprint_match: bool,
    score: float,
    tier: PairingTier,
):
    assessment = assess_pair(
        "old.docx",
        "new.docx",
        filename_match=filename_match,
        title_or_directory_match=title_or_directory_match,
        text_fingerprint_match=text_fingerprint_match,
    )

    assert assessment.score == pytest.approx(score)
    assert assessment.tier is tier
    assert assessment.confirmed is False


def test_all_pairing_decisions_require_explicit_human_confirmation_before_diffing():
    for assessment in (
        assess_pair("old.docx", "new.docx", filename_match=True, title_or_directory_match=True),
        assess_pair("old.docx", "new.docx", filename_match=True),
        assess_pair("old.docx", "new.docx"),
    ):
        with pytest.raises(PairingConfirmationRequired):
            assert_pairing_confirmed(assessment)


def test_diff_parameters_rejects_an_unconfirmed_pairing_assessment():
    fact = ParameterFact(
        fact_id="F1",
        canonical_name="capacity",
        raw_name="capacity",
        raw_value="1",
        normalized_value=1,
        subject="all",
        time_scope="annual",
        statistical_scope="cumulative",
        condition="standard",
        source_document="old.docx",
        source_span_id="S1",
        extraction_method=ExtractionMethod.TABLE,
    )
    assessment = assess_pair("old.docx", "new.docx", filename_match=True)

    with pytest.raises(PairingConfirmationRequired):
        diff_parameters([fact], [fact], pairing=assessment)

    assert diff_parameters([fact], [fact], pairing=assessment.confirm())
