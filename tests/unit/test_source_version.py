from __future__ import annotations

import pytest

from app.extraction.source_version import parse_source_version


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("方案V1.docx", "V1"),
        ("方案v1.docx", "V1"),
        ("方案 V 1.0.docx", "V1.0"),
        ("方案V1.0.2.docx", "V1.0.2"),
        ("方案第1版.docx", "V1"),
        ("方案第 2.1 版.docx", "V2.1"),
    ],
)
def test_source_version_normalizes_complete_supported_tokens(value, expected):
    assert parse_source_version(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "方案2026.docx",
        "井数 36.docx",
        "archiveV.docx",
        "ABCv1.docx",
        "方案V1..2.docx",
        "方案最终版.docx",
    ],
)
def test_source_version_does_not_guess_from_ordinary_numbers(value):
    assert parse_source_version(value) is None
