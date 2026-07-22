from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from app.domain.enums import Origin, ReviewStatus, RuleStatus, Severity
from app.domain.schemas import Finding, RuleResult
from app.persistence.db import create_session
from app.persistence.models import CaseRecord, FindingORM, ReviewRunORM
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun
from app.storage.case_files import StoredFile


PROHIBITED_VALUES = (
    "api_key=sk-test-secret-value",
    "token: abcdefghijklmnop",
    "authorization: Bearer abcdefghijklmnop",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_abcdefghijklmnopqrstuvwxyz",
    "github_pat_abcdefghijklmnopqrstuvwxyz",
    "AIzaSyAbcdefghijklmnopqrstuvwxyz012345",
    "embedded eyJhbGciOiJIUzI1NiJ9.abc.def payload",
    "-----BEGIN PRIVATE KEY-----",
    'request body: {"messages": ["full body"]}',
    "document content: 原始 DOCX 全文",
)


def test_persistence_schema_excludes_secrets_and_raw_document_bodies(tmp_path):
    session = create_session(tmp_path / "review.db")
    columns = {
        column["name"]
        for table in inspect(session.bind).get_table_names()
        for column in inspect(session.bind).get_columns(table)
    }

    forbidden = {
        "api_key", "secret", "token", "password", "raw_docx",
        "document_body", "request_body", "external_request_body",
    }
    assert not columns & forbidden


@pytest.mark.parametrize("prohibited", PROHIBITED_VALUES)
def test_prohibited_values_rejected_in_every_persisted_prose_field(tmp_path, prohibited):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))

    with pytest.raises(ValueError, match="forbidden"):
        repo.save_run(ReviewRun(
            "CASE-content",
            rule_results=[RuleResult(
                rule_id="R-content", status=RuleStatus.FAIL, severity=Severity.HIGH,
                category="other", message=prohibited, details={"nested": prohibited},
            )],
            findings=[Finding(
                finding_id="F-content", origin=Origin.RULE, category="other",
                severity=Severity.HIGH, title=prohibited, description=prohibited,
                suggestion=prohibited, evidence_span_ids=[], needs_human_review=True,
                original_ai_snapshot={"nested": [prohibited]},
            )],
        ))

    run = ReviewRun("CASE-note", findings=[Finding(
        finding_id="F-note", origin=Origin.RULE, category="other", severity=Severity.LOW,
        title="safe", evidence_span_ids=[], needs_human_review=True,
    )])
    repo.save_run(run)
    if prohibited.startswith("document content:"):
        repo.update_finding_review(
            "CASE-note", run.run_id, "F-note", ReviewStatus.CONFIRMED, prohibited
        )
    else:
        with pytest.raises(ValueError, match="sensitive"):
            repo.update_finding_review(
                "CASE-note", run.run_id, "F-note", ReviewStatus.CONFIRMED, prohibited
            )


def test_prohibited_values_are_not_retained_in_safe_case_metadata(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    with pytest.raises(ValueError, match="forbidden"):
        repo.save_case(CaseRecord(
            case_id="CASE-metadata",
            files=[StoredFile(
                storage_relative_path="cases/CASE-metadata/documents/a.docx",
                sha256="a" * 64, size=1, safe_name="safe.docx",
            )],
            statistics={"response_status": PROHIBITED_VALUES[0]},
        ))


def test_safe_json_filters_sensitive_keys_and_persists_no_body_values(tmp_path):
    session = create_session(tmp_path / "review.db")
    repo = ReviewRepository(session)
    raw_body = "This full external request body must never enter SQLite"
    secret = "sk-this-must-not-be-persisted"
    repo.save_run(ReviewRun(
        "CASE-safe",
        findings=[Finding(
            finding_id="F-safe", origin=Origin.RULE, category="capacity",
            severity=Severity.HIGH, title="Safe summary",
            description="A short finding summary", suggestion="Review evidence",
            evidence_span_ids=["span-1"], needs_human_review=True,
            original_ai_snapshot={"api_key": secret, "request_body": raw_body, "safe_label": "retained"},
        )],
    ))
    persisted = "\n".join(str(value) for value in (
        session.scalars(select(ReviewRunORM)).all() + session.scalars(select(FindingORM)).all()
    ))
    finding_row = session.scalar(select(FindingORM).where(FindingORM.finding_id == "F-safe"))

    assert secret not in persisted
    assert raw_body not in persisted
    assert finding_row is not None
    assert finding_row.ai_snapshot == {"safe_label": "retained"}


def test_structured_metadata_preserves_safe_names_and_filters_exact_secrets():
    from app.persistence.repository import sanitize_persistence_metadata

    cleaned = sanitize_persistence_metadata({
        "document_count": 2,
        "response_status": "ok",
        "response_sections": ["summary"],
        "request_count": 3,
        "business_document_label": "retained",
        "api_key": "sk-secret",
        "nested": {"provider_token": "secret", "rule_count": 4},
    })

    assert cleaned == {
        "document_count": 2,
        "response_status": "ok",
        "response_sections": ["summary"],
        "request_count": 3,
        "business_document_label": "retained",
        "nested": {"rule_count": 4},
    }


def test_statistics_are_allowlisted_and_round_trip_after_database_reopen(tmp_path):
    db = tmp_path / "review.db"
    first = create_session(db)
    ReviewRepository(first).save_case(CaseRecord(
        case_id="CASE-statistics",
        statistics={
            "document_count": 1,
            "response_status": "complete",
            "response_sections": "summary",
            "request_count": 2,
            "rule_count": 3,
            "fact_count": 4,
            "finding_count": 5,
        },
    ))
    first.close()

    second = create_session(db)
    loaded = ReviewRepository(second).get_case("CASE-statistics")
    second.close()
    assert loaded is not None
    assert loaded.statistics["document_count"] == 1
    assert loaded.statistics["response_status"] == "complete"
    assert loaded.statistics["finding_count"] == 5


def test_statistics_reject_unknown_fields(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    with pytest.raises(ValueError, match="unsupported fields"):
        repo.save_case(CaseRecord(case_id="CASE-unknown-stat", statistics={"safe": 1}))
