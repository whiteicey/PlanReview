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
                category="safe", message=prohibited, details={"nested": prohibited},
            )],
            findings=[Finding(
                finding_id="F-content", origin=Origin.RULE, category="safe",
                severity=Severity.HIGH, title=prohibited, description=prohibited,
                suggestion=prohibited, evidence_span_ids=[], needs_human_review=True,
                original_ai_snapshot={"nested": [prohibited]},
            )],
        ))

    repo.save_run(ReviewRun("CASE-note", findings=[Finding(
        finding_id="F-note", origin=Origin.RULE, category="safe", severity=Severity.LOW,
        title="safe", evidence_span_ids=[], needs_human_review=True,
    )]))
    with pytest.raises(ValueError, match="forbidden"):
        repo.update_finding_review("F-note", ReviewStatus.CONFIRMED, prohibited)


def test_prohibited_values_are_not_retained_in_safe_case_metadata(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    with pytest.raises(ValueError, match="forbidden"):
        repo.save_case(CaseRecord(
            case_id="CASE-metadata",
            files=[StoredFile(
                storage_relative_path="cases/CASE-metadata/documents/a.docx",
                sha256="a" * 64, size=1, safe_name="safe.docx",
            )],
            statistics={"safe": PROHIBITED_VALUES[0]},
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
