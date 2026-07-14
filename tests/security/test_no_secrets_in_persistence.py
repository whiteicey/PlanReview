from __future__ import annotations

from sqlalchemy import inspect, select

from app.domain.enums import Origin, Severity
from app.domain.schemas import Finding
from app.persistence.db import create_session
from app.persistence.models import FindingORM, ReviewRunORM
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun


def test_persistence_schema_excludes_secrets_and_raw_document_bodies(tmp_path):
    session = create_session(tmp_path / "review.db")
    columns = {
        column["name"]
        for table in inspect(session.bind).get_table_names()
        for column in inspect(session.bind).get_columns(table)
    }

    forbidden = {
        "api_key",
        "secret",
        "token",
        "password",
        "raw_docx",
        "document_body",
        "request_body",
        "external_request_body",
    }
    assert not columns & forbidden


def test_saved_values_do_not_include_secret_or_full_request_body(tmp_path):
    session = create_session(tmp_path / "review.db")
    repo = ReviewRepository(session)
    raw_body = "This full external request body must never enter SQLite"
    secret = "sk-this-must-not-be-persisted"
    run = ReviewRun(
        "CASE-safe",
        findings=[
            Finding(
                finding_id="F-safe",
                origin=Origin.RULE,
                category="capacity",
                severity=Severity.HIGH,
                title="Safe summary",
                description="A short finding summary",
                suggestion="Review the documented evidence",
                evidence_span_ids=["span-1"],
                needs_human_review=True,
                original_ai_snapshot={
                    "api_key": secret,
                    "request_body": raw_body,
                    "safe_label": "retained",
                },
            )
        ],
    )

    repo.save_run(run)
    persisted = "\n".join(
        str(value)
        for value in (
            session.scalars(select(ReviewRunORM)).all()
            + session.scalars(select(FindingORM)).all()
        )
    )
    finding_row = session.scalar(select(FindingORM).where(FindingORM.finding_id == "F-safe"))

    assert secret not in persisted
    assert raw_body not in persisted
    assert finding_row is not None
    assert finding_row.ai_snapshot == {"safe_label": "retained"}
