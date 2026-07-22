from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.enums import Origin, Severity
from app.domain.schemas import Finding
from app.persistence.models import Base, CaseRecord
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun


def _repository() -> ReviewRepository:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    repo = ReviewRepository(session)
    repo.save_case(CaseRecord(case_id="CASE-terminal"))
    return repo


def _finding() -> Finding:
    return Finding(
        finding_id="F-terminal",
        origin=Origin.RULE,
        category="consistency",
        severity=Severity.MEDIUM,
        title="参数一致性问题",
        description="正文与表格中的参数不一致。",
        suggestion="请核对参数及其证据位置。",
        evidence_span_ids=["document:p:1"],
        needs_human_review=True,
    )


def _oversized_ledger() -> dict:
    entries = [
        {
            "packet_id": f"packet-{index}",
            "source_span_ids": [f"document:p:{index}"],
            "stage": "selected",
            "decision": "selected",
        }
        for index in range(121)
    ]
    return {
        "ledger_schema_version": "v1",
        "ledger_entry_count": len(entries),
        "ledger_truncated": False,
        "ledger_size_bytes": 16_000,
        "entries": entries,
        "summary": {},
    }


def _claimed_ready_run(repo: ReviewRepository) -> tuple[ReviewRun, str]:
    run = ReviewRun(
        "CASE-terminal",
        final_status="READY_FOR_HUMAN_REVIEW",
        findings=[_finding()],
        packet_lifecycle_ledger=_oversized_ledger(),
        ai_candidate_lifecycle_ledger=_oversized_ledger(),
        batch_count=5,
    )
    repo.create_running_run(run.case_id, run.run_id)
    token = "11111111-1111-4111-8111-111111111111"
    assert repo.claim_running_run(run.run_id, token)
    return run, token


def test_large_lifecycle_ledgers_do_not_block_ready_commit() -> None:
    repo = _repository()
    run, token = _claimed_ready_run(repo)

    repo.finish_running_run(run, token)

    persisted = repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "READY_FOR_HUMAN_REVIEW"
    assert len(persisted.findings) == 1
    assert len(persisted.packet_lifecycle_ledger["entries"]) == 121
    assert len(persisted.ai_candidate_lifecycle_ledger["entries"]) == 121
    repo.session.close()


def test_observability_failure_after_ready_data_is_degraded_without_losing_findings(
    monkeypatch,
) -> None:
    repo = _repository()
    run, token = _claimed_ready_run(repo)

    def fail_ledger(*_args, **_kwargs):
        raise RuntimeError("diagnostic store unavailable")

    monkeypatch.setattr("app.persistence.repository._sanitize_ledger", fail_ledger)
    repo.finish_running_run(run, token)

    persisted = repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "READY_FOR_HUMAN_REVIEW"
    assert len(persisted.findings) == 1
    assert persisted.packet_lifecycle_ledger["ledger_truncated"] is True
    assert persisted.ai_candidate_lifecycle_ledger["ledger_truncated"] is True
    repo.session.close()


def test_failure_cas_cannot_overwrite_ready_terminal_state() -> None:
    repo = _repository()
    run, token = _claimed_ready_run(repo)
    repo.finish_running_run(run, token)

    repo.set_running_run_failed(run.run_id, token)

    persisted = repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "READY_FOR_HUMAN_REVIEW"
    repo.session.close()


def test_duplicate_finish_callback_is_idempotent_for_terminal_state() -> None:
    repo = _repository()
    run, token = _claimed_ready_run(repo)
    repo.finish_running_run(run, token)
    repo.finish_running_run(run, token)

    persisted = repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "READY_FOR_HUMAN_REVIEW"
    assert [item.finding_id for item in persisted.findings] == ["F-terminal"]
    repo.session.close()
