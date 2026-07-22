from __future__ import annotations

import pytest

from app.domain.enums import BlockType, LLMStatus, Origin, ReviewStatus, Severity
from app.domain.schemas import Finding, SourceSpan
from app.llm.provider import LLMProviderError
from app.parsers.docx_parser import ParsedDocument
from app.persistence.db import create_session
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun
from app.review.pipeline import ReviewPipeline


def _run(
    case_id: str,
    title: str,
    *,
    status: str = "READY_FOR_HUMAN_REVIEW",
    finding_id: str = "same-finding",
) -> ReviewRun:
    return ReviewRun(
        case_id,
        final_status=status,
        llm_provider="fake",
        llm_model="fake-model",
        llm_status=LLMStatus.COMPLETED,
        llm_finding_count=1,
        findings=[Finding(
            finding_id=finding_id,
            origin=Origin.LLM,
            category="capacity",
            severity=Severity.HIGH,
            title=title,
            evidence_span_ids=["s1"],
            needs_human_review=True,
        )],
    )


def test_three_runs_are_append_only_and_survive_restart(tmp_path):
    database = tmp_path / "history.db"
    repo = ReviewRepository(create_session(database))
    runs = [_run("CASE-history", f"run {index}") for index in range(3)]
    for run in runs:
        repo.save_run(run)

    restarted = ReviewRepository(create_session(database))
    stored = restarted.list_runs("CASE-history")
    assert len(stored) == 3
    assert {item.run_id for item in stored} == {item.run_id for item in runs}
    for run in runs:
        loaded = restarted.get_run_for_case("CASE-history", run.run_id)
        assert loaded.findings[0].run_id == run.run_id
        assert loaded.findings[0].title == run.findings[0].title


def test_new_review_does_not_overwrite_expert_review(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "history.db"))
    first = _run("CASE-review", "first")
    repo.save_run(first)
    repo.update_finding_review(
        first.case_id,
        first.run_id,
        "same-finding",
        ReviewStatus.CONFIRMED,
        "paragraph one\n\nparagraph two",
    )
    second = _run("CASE-review", "second")
    repo.save_run(second)

    loaded_first = repo.get_run(first.run_id)
    loaded_second = repo.get_run(second.run_id)
    assert loaded_first.findings[0].review_status is ReviewStatus.CONFIRMED
    assert loaded_first.findings[0].human_note == "paragraph one\n\nparagraph two"
    assert loaded_first.findings[0].reviewed_at is not None
    assert loaded_second.findings[0].review_status is ReviewStatus.PENDING


def test_latest_failed_run_does_not_hide_latest_success(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "history.db"))
    successful = _run("CASE-latest", "success")
    failed = _run("CASE-latest", "failed", status="FAILED")
    repo.save_run(successful)
    repo.save_run(failed)

    assert repo.get_latest_run("CASE-latest").run_id == failed.run_id
    assert repo.get_latest_successful_run("CASE-latest").run_id == successful.run_id


def test_cross_run_and_cross_case_review_updates_are_rejected_without_changes(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "history.db"))
    first = _run("CASE-A", "first")
    other_run = _run("CASE-A", "second", finding_id="other-finding")
    other_case = _run("CASE-B", "third")
    for run in (first, other_run, other_case):
        repo.save_run(run)

    with pytest.raises(KeyError):
        repo.update_finding_review(
            first.case_id, other_run.run_id, first.findings[0].finding_id,
            ReviewStatus.REJECTED, "must not write",
        )
    with pytest.raises(KeyError):
        repo.update_finding_review(
            first.case_id, other_case.run_id, other_case.findings[0].finding_id,
            ReviewStatus.REJECTED, "must not write",
        )

    assert repo.get_run(first.run_id).findings[0].review_status is ReviewStatus.PENDING
    assert repo.get_run(other_run.run_id).findings[0].review_status is ReviewStatus.PENDING
    assert repo.get_run(other_case.run_id).findings[0].review_status is ReviewStatus.PENDING


def test_provider_error_metadata_is_sanitized_and_persists_with_ready_run(tmp_path):
    class ProviderFailure:
        provider_name = "anthropic"
        model_name = "safe-model"

        def review(self, _request):
            raise LLMProviderError(
                "timeout C:\\private\\case.docx api_key=sk-test-secret request_body=full text"
            )

    span = SourceSpan(
        span_id="s1",
        document_id="D",
        block_type=BlockType.PARAGRAPH,
        text="safe text",
        text_hash="a" * 64,
    )
    document = ParsedDocument("D", "safe.docx", [span], [span], [])
    run = ReviewPipeline().run("CASE-provider", [document], [], ProviderFailure())
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_status is LLMStatus.PROVIDER_ERROR
    assert run.llm_error_summary
    for forbidden in ("private", "case.docx", "api_key", "sk-test-secret", "request_body", "full text"):
        assert forbidden not in run.llm_error_summary

    database = tmp_path / "provider.db"
    ReviewRepository(create_session(database)).save_run(run)
    stored = ReviewRepository(create_session(database)).get_run(run.run_id)
    assert stored.llm_provider == "anthropic"
    assert stored.llm_model == "safe-model"
    assert stored.llm_status is LLMStatus.PROVIDER_ERROR
    assert stored.llm_finding_count == 0
    assert stored.llm_error_summary == run.llm_error_summary
