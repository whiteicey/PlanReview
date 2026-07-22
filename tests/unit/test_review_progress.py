from __future__ import annotations

from uuid import uuid4

import pytest

from app.persistence.db import create_session
from app.persistence.models import CaseRecord
from app.persistence.repository import ReviewRepository


def repository(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "progress.db"))
    repo.save_case(CaseRecord(case_id="CASE-1"))
    return repo


def test_progress_events_are_append_only_incremental_and_strictly_sequenced(tmp_path):
    repo = repository(tmp_path)
    run_id = str(uuid4())
    repo.create_running_run("CASE-1", run_id)
    first = repo.append_progress_event(run_id, "INPUT_VALIDATION", "TASK_CREATED", "running", "任务已创建")
    second = repo.append_progress_event(run_id, "INPUT_VALIDATION", "STAGE_COMPLETED", "completed", "输入校验完成")

    assert (first.sequence, second.sequence) == (1, 2)
    assert [item.sequence for item in repo.list_progress_events(run_id, 1)] == [2]
    assert repo.last_progress_sequence(run_id) == 2


def test_only_one_worker_can_claim_a_run(tmp_path):
    repo = repository(tmp_path)
    run_id = str(uuid4())
    repo.create_running_run("CASE-1", run_id)
    assert repo.claim_running_run(run_id, str(uuid4())) is True
    assert repo.claim_running_run(run_id, str(uuid4())) is False


def test_startup_interrupts_orphaned_running_run_and_appends_safe_event(tmp_path):
    repo = repository(tmp_path)
    run_id = str(uuid4())
    repo.create_running_run("CASE-1", run_id)
    repo.append_progress_event(run_id, "INPUT_VALIDATION", "TASK_CREATED", "running", "任务已创建")

    assert repo.interrupt_orphaned_runs() == 1
    assert repo.get_run(run_id).final_status == "INTERRUPTED"
    events = repo.list_progress_events(run_id)
    assert events[-1].event_type == "TASK_INTERRUPTED"
    assert events[-1].sequence == 2


def test_progress_details_reject_sensitive_keys(tmp_path):
    repo = repository(tmp_path)
    run_id = str(uuid4())
    repo.create_running_run("CASE-1", run_id)
    with pytest.raises(ValueError):
        repo.append_progress_event(
            run_id, "AI_REVIEW", "STAGE_COMPLETED", "completed", "AI 调用完成",
            {"raw_prompt": "secret"},
        )


def test_progress_allows_only_whitelisted_safe_response_diagnostics(tmp_path):
    repo = repository(tmp_path)
    run_id = str(uuid4())
    repo.create_running_run("CASE-1", run_id)
    event = repo.append_progress_event(
        run_id, "AI_VALIDATION", "STAGE_COMPLETED", "failed", "AI 已返回内容，但未通过结构化格式校验",
        {
            "validation_reason_code": "truncated_json",
            "http_status": 200,
            "response_character_count": 2610,
            "stop_reason": "max_tokens",
            "content_block_count": 1,
        },
    )
    assert event.details["validation_reason_code"] == "truncated_json"
    assert event.details["response_character_count"] == 2610
    with pytest.raises(ValueError):
        repo.append_progress_event(
            run_id, "AI_REVIEW", "STAGE_COMPLETED", "completed", "AI 响应已返回",
            {"raw_response": "do not persist"},
        )
