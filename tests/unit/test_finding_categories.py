from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.domain.enums import FindingCategory, Origin, Severity
from app.domain.schemas import Finding
from app.llm.provider import validate_findings
from app.persistence.db import create_session
from app.persistence.models import FindingORM
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun


def test_finding_category_taxonomy_has_fixed_order_and_legacy_mapping():
    assert [category.value for category in FindingCategory] == [
        "completeness",
        "consistency",
        "aggregation",
        "cross_domain",
        "capacity",
        "version_change",
        "terminology",
        "evidence",
        "traceability",
        "unknown_scope",
        "other",
    ]
    assert FindingCategory("version-change") is FindingCategory.VERSION_CHANGE
    assert FindingCategory("unknown") is FindingCategory.UNKNOWN_SCOPE


def test_finding_model_and_llm_validation_fail_closed_for_unknown_categories():
    with pytest.raises(ValidationError):
        Finding(
            finding_id="F-invalid",
            origin=Origin.LLM,
            category="invented",
            severity=Severity.LOW,
            title="invalid",
            evidence_span_ids=[],
            needs_human_review=True,
        )

    with pytest.raises(ValueError) as exc:
        validate_findings(
            [{
                "category": "invented",
                "severity": "low",
                "title": "invalid",
                "description": "invalid",
                "suggestion": "review",
                "evidence_span_ids": ["s1"],
            }],
            ["s1"],
        )
    assert exc.value.reason_code == "invalid_category"


def test_llm_validation_canonicalizes_only_supported_legacy_category():
    finding = validate_findings(
        [{
            "category": "version-change",
            "severity": "low",
            "title": "legacy",
            "description": "legacy",
            "suggestion": "review",
            "evidence_span_ids": ["s1"],
        }],
        ["s1"],
    )[0]
    assert finding["category"] == "version_change"


def test_legacy_database_category_is_mapped_on_read_and_future_write_is_canonical(tmp_path):
    session = create_session(tmp_path / "review.db")
    repository = ReviewRepository(session)
    run = ReviewRun(
        "CASE-category",
        findings=[Finding(
            finding_id="F-legacy",
            origin=Origin.RULE,
            category="version_change",
            severity=Severity.LOW,
            title="legacy",
            evidence_span_ids=[],
            needs_human_review=True,
        )],
    )
    repository.save_run(run)
    session.execute(update(FindingORM).values(category="version-change"))
    session.commit()

    loaded = repository.get_run(run.run_id)
    assert loaded is not None
    assert loaded.findings[0].category is FindingCategory.VERSION_CHANGE
