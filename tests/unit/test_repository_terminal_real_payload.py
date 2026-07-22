from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import (
    FindingCategory,
    LLMStatus,
    Origin,
    PipelineStage,
    RuleStatus,
    Severity,
)
from app.domain.schemas import Finding, RuleResult, StageRecord
from app.persistence.models import (
    Base,
    CaseRecord,
    FindingORM,
    ReviewRunORM,
    RuleResultORM,
)
from app.persistence.repository import ReviewRepository
from app.review.pipeline import ReviewRun


def _ledger(kind: str, count: int) -> dict:
    entries = [
        {
            "packet_id": f"{kind}-{index}",
            "candidate_id": f"candidate-{index}",
            "source_span_ids": [f"document:p:{index}"],
            "stage": "selected",
            "decision": "selected",
            "reason_code": "REAL_PAYLOAD_TEST",
            "batch_id": f"batch-{index % 5 + 1}",
        }
        for index in range(count)
    ]
    return {
        "ledger_schema_version": "v1",
        "ledger_entry_count": len(entries),
        "ledger_truncated": False,
        "ledger_size_bytes": count * 256,
        "entries": entries,
        "summary": {},
    }


def _findings(count: int) -> list[Finding]:
    return [
        Finding(
            finding_id=f"F-real-{index:02d}",
            origin=Origin.LLM if index >= 18 else Origin.RULE,
            category=FindingCategory.CONSISTENCY,
            severity=Severity.MEDIUM,
            parameter=None,
            title=f"Finding {index}",
            description=f"Detected issue {index}",
            suggestion=f"Review evidence {index}",
            rule_id=f"RULE-{index:03d}" if index < 18 else None,
            evidence_span_ids=[f"document:p:{index}"],
            needs_human_review=True,
            original_ai_snapshot={"candidate_sequence": index},
        )
        for index in range(count)
    ]


def _rule_results(count: int) -> list[RuleResult]:
    return [
        RuleResult(
            rule_id=f"RULE-{index:03d}",
            rule_version="1.2.0",
            status=RuleStatus.FAIL if index < 6 else RuleStatus.PASS,
            severity=Severity.MEDIUM,
            category=FindingCategory.CONSISTENCY,
            # Reproduces V1.2 operators that use an empty optional parameter.
            parameter="" if index % 3 == 0 else f"parameter-{index}",
            message=f"Rule result {index}",
            evidence_span_ids=[f"document:p:{index}"],
            involved_fact_ids=[],
            needs_human_review=index < 6,
            details={"executed": True},
        )
        for index in range(count)
    ]


def _ready_run(case_id: str) -> ReviewRun:
    now = datetime.now(timezone.utc)
    stage_records = [
        StageRecord(
            stage=stage,
            started_at=now,
            ended_at=now,
            status="completed",
        )
        for stage in list(PipelineStage)[:14]
    ]
    return ReviewRun(
        case_id=case_id,
        final_status="READY_FOR_HUMAN_REVIEW",
        rule_results=_rule_results(18),
        findings=_findings(24),
        stage_records=stage_records,
        llm_provider="test-provider",
        llm_model="test-model",
        llm_status=LLMStatus.COMPLETED,
        llm_finding_count=24,
        candidate_count=36,
        valid_count=36,
        rejected_count=0,
        available_span_count=1719,
        selected_span_count=488,
        selected_character_count=65000,
        coverage_ratio=488 / 1719,
        evidence_selector_version="structured-packets-v1.2",
        max_tokens=8192,
        timeout=120.0,
        temperature=0.0,
        batch_count=5,
        batch_metrics=[
            {
                "batch_id": f"batch-{index + 1}",
                "source_span_count": 96 + index,
                "character_count": 12000 + index,
                "candidate_count": 7 + (index % 2),
            }
            for index in range(5)
        ],
        premerge_finding_count=42,
        postmerge_finding_count=24,
        deduplicated_finding_count=18,
        stop_reason="end_turn",
        packet_lifecycle_ledger=_ledger("packet", 150),
        ai_candidate_lifecycle_ledger=_ledger("candidate", 140),
        rule_metrics={
            f"RULE-{index:03d}": {
                "enabled": True,
                "executed_count": 1,
                "pass_count": int(index >= 6),
                "fail_count": int(index < 6),
                "unknown_count": 0,
            }
            for index in range(18)
        },
    )


def _counts(session: Session, run_id: str) -> tuple[int, int]:
    review_run_id = session.scalar(
        select(ReviewRunORM.id).where(ReviewRunORM.run_id == run_id)
    )
    assert review_run_id is not None
    findings = session.scalar(
        select(func.count()).select_from(FindingORM).where(
            FindingORM.review_run_id == review_run_id
        )
    )
    results = session.scalar(
        select(func.count()).select_from(RuleResultORM).where(
            RuleResultORM.review_run_id == review_run_id
        )
    )
    return int(findings or 0), int(results or 0)


def test_finish_running_run_persists_realistic_terminal_payload_idempotently(tmp_path) -> None:
    database = tmp_path / "terminal-real-payload.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)

    session = Session(engine, expire_on_commit=False)
    repo = ReviewRepository(session)
    case_id = "CASE-real-payload"
    repo.save_case(CaseRecord(case_id=case_id))
    run = _ready_run(case_id)
    token = "22222222-2222-4222-8222-222222222222"
    repo.create_running_run(case_id, run.run_id)
    assert repo.claim_running_run(run.run_id, token)

    # Exercise cumulative batch checkpoint replacement with stable finding IDs.
    repo.checkpoint_running_run(run, token, run.findings[:8])
    repo.checkpoint_running_run(run, token, run.findings[:16])
    repo.finish_running_run(run, token)
    session.close()

    refreshed_session = Session(engine, expire_on_commit=False)
    refreshed_repo = ReviewRepository(refreshed_session)
    persisted = refreshed_repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "READY_FOR_HUMAN_REVIEW"
    assert len(persisted.findings) == 24
    assert len(persisted.rule_results) == 18
    assert len(persisted.packet_lifecycle_ledger["entries"]) == 150
    assert len(persisted.ai_candidate_lifecycle_ledger["entries"]) == 140
    assert len(persisted.batch_metrics) == 5
    assert len(persisted.rule_metrics) == 18
    assert len(persisted.stage_records) == 14
    assert _counts(refreshed_session, run.run_id) == (24, 18)
    assert persisted.rule_results[0].parameter is None

    # A duplicate completion callback must be a no-op, not duplicate children.
    refreshed_repo.finish_running_run(run, token)
    refreshed_session.expire_all()
    assert _counts(refreshed_session, run.run_id) == (24, 18)
    assert refreshed_repo.get_run(run.run_id).final_status == "READY_FOR_HUMAN_REVIEW"
    refreshed_session.close()


def test_finish_running_run_rolls_back_entire_payload_on_child_insert_failure(tmp_path) -> None:
    database = tmp_path / "terminal-rollback.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)

    session = Session(engine, expire_on_commit=False)
    repo = ReviewRepository(session)
    case_id = "CASE-terminal-rollback"
    repo.save_case(CaseRecord(case_id=case_id))
    run = _ready_run(case_id)
    token = "33333333-3333-4333-8333-333333333333"
    repo.create_running_run(case_id, run.run_id)
    assert repo.claim_running_run(run.run_id, token)
    repo.checkpoint_running_run(run, token, run.findings[:8])

    duplicate = run.findings[0].model_copy(deep=True)
    run.findings = [run.findings[0], duplicate]
    with pytest.raises(IntegrityError):
        repo.finish_running_run(run, token)
    session.close()

    refreshed_session = Session(engine, expire_on_commit=False)
    refreshed_repo = ReviewRepository(refreshed_session)
    persisted = refreshed_repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "RUNNING"
    assert len(persisted.findings) == 8
    assert len(persisted.rule_results) == 0
    assert _counts(refreshed_session, run.run_id) == (8, 0)
    refreshed_session.close()
