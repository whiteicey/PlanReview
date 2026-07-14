from __future__ import annotations

import pytest

from app.domain.enums import Origin, ReviewStatus, RuleStatus, Severity
from app.domain.schemas import Finding, RuleResult
from app.persistence.db import create_session
from app.persistence.models import CaseRecord
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun
from app.storage.case_files import StoredFile


def test_round_trip_run_and_human_review(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    run = ReviewRun(
        "CASE-1",
        rule_results=[
            RuleResult(
                rule_id="R1",
                status=RuleStatus.FAIL,
                severity=Severity.HIGH,
                category="capacity",
                message="capacity differs",
                evidence_span_ids=["span-1"],
                details={"difference": 20},
            )
        ],
        findings=[
            Finding(
                finding_id="F1",
                origin=Origin.RULE,
                category="capacity",
                severity=Severity.HIGH,
                title="Capacity mismatch",
                description="d",
                suggestion="s",
                evidence_span_ids=["span-1"],
                needs_human_review=True,
            )
        ],
        final_status="READY_FOR_HUMAN_REVIEW",
    )

    assert repo.save_run(run) == "CASE-1"
    # A new session proves this is an SQLite round-trip rather than a process cache.
    loaded = ReviewRepository(create_session(db)).get_run("CASE-1")

    assert loaded is not None
    assert loaded.case_id == "CASE-1"
    assert loaded.final_status == "READY_FOR_HUMAN_REVIEW"
    assert loaded.rule_results[0].rule_id == "R1"
    assert loaded.findings[0].finding_id == "F1"

    repo.update_finding_review("F1", ReviewStatus.CONFIRMED, "专家确认")
    reviewed = ReviewRepository(create_session(db)).get_run("CASE-1")
    assert reviewed is not None
    assert reviewed.findings[0].review_status is ReviewStatus.CONFIRMED
    assert reviewed.findings[0].human_note == "专家确认"


def test_save_run_is_idempotent_for_same_finding_id(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    first = ReviewRun(
        "CASE-rerun",
        findings=[
            Finding(
                finding_id="same-id",
                origin=Origin.RULE,
                category="capacity",
                severity=Severity.HIGH,
                title="first",
                description="first",
                suggestion="s",
                evidence_span_ids=["s1"],
                needs_human_review=True,
            )
        ],
    )
    second = first.__class__(
        "CASE-rerun",
        findings=[
            Finding(
                finding_id="same-id",
                origin=Origin.RULE,
                category="capacity",
                severity=Severity.HIGH,
                title="second",
                description="second",
                suggestion="s",
                evidence_span_ids=["s2"],
                needs_human_review=True,
            )
        ],
    )

    repo.save_run(first)
    repo.save_run(second)
    loaded = ReviewRepository(create_session(db)).get_run("CASE-rerun")

    assert loaded is not None
    assert len(loaded.findings) == 1
    assert loaded.findings[0].finding_id == "same-id"
    assert loaded.findings[0].title == "second"


def test_case_metadata_only_stores_relative_file_paths_and_recycle_bin(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    case = CaseRecord(
        case_id="CASE-2",
        files=[
            StoredFile(
                storage_relative_path="cases/CASE-2/documents/a.docx",
                sha256="a" * 64,
                size=7,
                safe_name="a.docx",
            )
        ],
        statistics={"document_count": 1},
    )

    assert repo.save_case(case) == "CASE-2"
    repo.save_run(ReviewRun("CASE-2"))
    repo.delete_case_to_recycle_bin("CASE-2")

    restarted = ReviewRepository(create_session(db))
    assert restarted.get_run("CASE-2") is None
    assert restarted.recycle_bin_case_ids() == ["CASE-2"]

    restarted.permanently_delete_case("CASE-2", confirmation="DELETE CASE-2")
    assert restarted.recycle_bin_case_ids() == []


@pytest.mark.parametrize("storage_path", ["C:/secret/a.docx", "\\\\server\\share\\a.docx", "\\\\?\\C:\\a.docx", "/root/a.docx", "cases\\CASE-3\\a.docx"])
def test_absolute_storage_path_and_unconfirmed_delete_are_rejected(tmp_path, storage_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    absolute_case = CaseRecord(
        case_id="CASE-3",
        files=[
            StoredFile(
                storage_relative_path=storage_path,
                sha256="b" * 64,
                size=1,
                safe_name="a.docx",
            )
        ],
    )

    with pytest.raises(ValueError, match="relative"):
        repo.save_case(absolute_case)

    repo.save_case(CaseRecord(case_id="CASE-3"))
    repo.delete_case_to_recycle_bin("CASE-3")
    try:
        repo.permanently_delete_case("CASE-3", confirmation="DELETE")
    except ValueError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("permanent deletion must require exact confirmation")


def test_human_note_rejects_secret_tokens_bodies_and_document_content(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    repo.save_run(ReviewRun("CASE-note", findings=[Finding(
        finding_id="F-note", origin=Origin.RULE, category="c", severity=Severity.LOW,
        title="t", evidence_span_ids=[], needs_human_review=True,
    )]))

    forbidden_notes = [
        "api_key=sk-test-secret-value",
        "token: abcdefghijklmnop",
        "Authorization: Bearer abcdefghijklmnop",
        'request body: {"messages": ["full body"]}',
        "document content: 原始 DOCX 全文",
    ]
    for note in forbidden_notes:
        with pytest.raises(ValueError, match="forbidden"):
            repo.update_finding_review("F-note", ReviewStatus.CONFIRMED, note)


def test_repository_never_accepts_secret_field(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))

    assert not hasattr(repo, "save_api_key")
    assert "api_key" not in repo.persisted_field_names
