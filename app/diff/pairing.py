"""Deterministic document-version pairing and human-confirmation safeguards."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from pathlib import PurePath
import re


FILENAME_WEIGHT = 0.55
TITLE_OR_DIRECTORY_WEIGHT = 0.25
TEXT_FINGERPRINT_WEIGHT = 0.20
HIGH_CONFIDENCE_THRESHOLD = 0.80
CONFIRMATION_THRESHOLD = 0.50

_VERSION_PATTERN = re.compile(r"(?:^|[_-])v(?P<version>\d+)(?=\.|[_-]|$)", re.IGNORECASE)
_DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>20\d{2})[-_.]?(?P<month>0[1-9]|1[0-2])[-_.]?(?P<day>0[1-9]|[12]\d|3[01])(?!\d)"
)


class PairingTier(str, Enum):
    """The fixed score bands defined for a candidate document pair."""

    HIGH_CONFIDENCE = "high_confidence"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REJECTED = "rejected"


class PairingConfirmationRequired(ValueError):
    """Raised when a pair is used for a version comparison without approval."""


@dataclass(frozen=True)
class PairingAssessment:
    """An auditable score and explicit confirmation state for one candidate pair."""

    old_document: str
    new_document: str
    filename_match: bool
    title_or_directory_match: bool
    text_fingerprint_match: bool
    score: float
    tier: PairingTier
    confirmed: bool = False

    def confirm(self) -> "PairingAssessment":
        """Return an explicitly human-confirmed copy of this assessment."""
        return replace(self, confirmed=True)


def assess_pair(
    old_document: str,
    new_document: str,
    *,
    filename_match: bool | None = None,
    title_or_directory_match: bool = False,
    text_fingerprint_match: bool = False,
) -> PairingAssessment:
    """Score a candidate using only the three mandated matching signals.

    Filenames contribute 0.55, title/directory agreement contributes 0.25, and
    a text-fingerprint match contributes 0.20. Score tiers are >=0.80,
    0.50--<0.80, and <0.50 respectively. Every tier still requires human
    confirmation before it may drive a version comparison.
    """
    if filename_match is None:
        filename_match = _document_stem(old_document) == _document_stem(new_document)

    score = (
        FILENAME_WEIGHT * filename_match
        + TITLE_OR_DIRECTORY_WEIGHT * title_or_directory_match
        + TEXT_FINGERPRINT_WEIGHT * text_fingerprint_match
    )
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        tier = PairingTier.HIGH_CONFIDENCE
    elif score >= CONFIRMATION_THRESHOLD:
        tier = PairingTier.REQUIRES_CONFIRMATION
    else:
        tier = PairingTier.REJECTED

    return PairingAssessment(
        old_document=old_document,
        new_document=new_document,
        filename_match=filename_match,
        title_or_directory_match=title_or_directory_match,
        text_fingerprint_match=text_fingerprint_match,
        score=score,
        tier=tier,
    )


def assert_pairing_confirmed(assessment: PairingAssessment) -> PairingAssessment:
    """Reject use of a candidate pair until a human has confirmed it."""
    if not assessment.confirmed:
        raise PairingConfirmationRequired(
            "Document pairing must be explicitly confirmed by a human before comparison."
        )
    return assessment


def pair_documents(files: list[str]) -> list[tuple[str, str]]:
    """Pair adjacent versions from each filename stem by V marker or date.

    Unversioned files are ignored. Version markers take precedence over dates
    within a stem. A stem never mixes marker families: if it contains any V
    markers, date-only files are ignored; otherwise date files are sorted by
    calendar date. This makes mixed V/date input deterministic and avoids
    comparing unlike token types.
    """
    grouped: dict[str, list[tuple[str, int | date, str]]] = {}
    for file_name in files:
        token = _version_token(file_name)
        if token is None:
            continue
        stem = _document_stem(file_name)
        grouped.setdefault(stem, []).append(token)

    pairs: list[tuple[str, str]] = []
    for versions in grouped.values():
        family = "v" if any(kind == "v" for kind, _, _ in versions) else "date"
        selected = [item for item in versions if item[0] == family]
        selected.sort(key=lambda item: (item[1], item[2]))
        pairs.extend(
            (selected[index][2], selected[index + 1][2])
            for index in range(len(selected) - 1)
        )
    return pairs


def _document_stem(file_name: str) -> str:
    stem = PurePath(file_name).stem
    stem = _VERSION_PATTERN.sub("", stem)
    stem = _DATE_PATTERN.sub("", stem)
    return stem.rstrip("_.- ").casefold()


def _version_token(file_name: str) -> tuple[str, int | date, str] | None:
    stem = PurePath(file_name).stem
    version_match = _VERSION_PATTERN.search(stem)
    if version_match:
        return "v", int(version_match.group("version")), file_name

    date_match = _DATE_PATTERN.search(stem)
    if not date_match:
        return None
    try:
        token = date(
            int(date_match.group("year")),
            int(date_match.group("month")),
            int(date_match.group("day")),
        )
    except ValueError:
        return None
    return "date", token, file_name
