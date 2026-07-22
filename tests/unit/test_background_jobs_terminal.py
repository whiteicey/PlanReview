from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from app.domain.schemas import Finding
from app.parsers.docx_parser import DocxParser
from app.persistence.db import DatabaseRuntime
from app.persistence.models import CaseRecord
from app.persistence.repository import ReviewRepository
from app.review.parsed_cache import ParsedDocumentCache
from app.review.pipeline import ReviewRun
from app.storage.case_files import StoredFile
from app.review import background_jobs
from tests.unit.test_v12_real_terminal_payload_replay import (
    _fixture,
    _review_run,
    _write_case_document,
)


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Runtime:
    def __init__(self, session: _Session) -> None:
        self._session = session

    def session(self) -> _Session:
        return self._session


class _Cache:
    def get(self, _case_id: str, _key: str):
        return []


@dataclass
class _FakeRepository:
    state: str = "RUNNING"

    def __post_init__(self) -> None:
        self.events: list[tuple] = []
        self.finish_calls = 0
        self.progress_attempts = 0
        self.case = CaseRecord(
            case_id="CASE-background",
            files=[
                StoredFile(
                    storage_relative_path="cases/CASE-background/input.docx",
                    sha256="0" * 64,
                    size=1,
                    safe_name="input.docx",
                )
            ],
        )

    def claim_running_run(self, _run_id: str, _token: str) -> bool:
        return self.state == "RUNNING"

    def get_case(self, _case_id: str) -> CaseRecord:
        return self.case

    def append_progress_event(self, *args, **kwargs):
        self.progress_attempts += 1
        if self.progress_attempts == 1:
            raise RuntimeError("progress sink unavailable")
        self.events.append((args, kwargs))
        return None

    def finish_running_run(self, run: ReviewRun, _token: str) -> str:
        assert self.state == "RUNNING"
        self.finish_calls += 1
        self.state = run.final_status
        return run.run_id

    def get_run(self, _run_id: str):
        return SimpleNamespace(final_status=self.state)

    def set_running_run_failed(self, _run_id: str, _token: str) -> None:
        if self.state == "RUNNING":
            self.state = "FAILED"


def test_successful_pipeline_keeps_ready_state_when_progress_write_fails(
    monkeypatch,
) -> None:
    repository = _FakeRepository()
    session = _Session()
    runtime = _Runtime(session)
    run_id = "a60a19ef-4749-4a78-854a-0b3471553c89"

    class _Pipeline:
        def __init__(self, _terminology) -> None:
            pass

        def run(self, case_id, documents, rules, provider, **kwargs) -> ReviewRun:
            assert case_id == "CASE-background"
            return ReviewRun(case_id, run_id=run_id, final_status="READY_FOR_HUMAN_REVIEW")

    monkeypatch.setattr(background_jobs, "ReviewRepository", lambda _session: repository)
    monkeypatch.setattr(background_jobs, "ReviewPipeline", _Pipeline)

    execute_args = (
        runtime,
        _Cache(),
        SimpleNamespace(storage_root=Path(".")),
        "CASE-background",
        run_id,
        None,
        object(),
    )
    background_jobs.execute_review_job(*execute_args)

    assert repository.state == "READY_FOR_HUMAN_REVIEW"
    assert repository.finish_calls == 1
    assert session.closed is True
    assert not any(
        args[0] == "AI_REVIEW" and args[1] == "STAGE_COMPLETED" and args[2] == "failed"
        for args, _kwargs in repository.events
    )

    # A duplicate completion callback cannot claim or alter the terminal Run.
    background_jobs.execute_review_job(*execute_args)
    assert repository.state == "READY_FOR_HUMAN_REVIEW"
    assert repository.finish_calls == 1


def test_background_job_completes_real_terminal_payload_without_failed_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = DatabaseRuntime(tmp_path / "background-real-payload.db")
    runtime.initialize()
    run = _review_run()
    fixture = _fixture()
    storage_root = tmp_path / "storage"
    stored = _write_case_document(storage_root, run.case_id)

    setup_session = runtime.session()
    setup_repository = ReviewRepository(setup_session)
    setup_repository.save_case(CaseRecord(case_id=run.case_id, files=[stored]))
    setup_repository.create_running_run(run.case_id, run.run_id)
    setup_session.close()

    parsed_cache = ParsedDocumentCache()
    parsed = DocxParser().parse(
        storage_root / stored.storage_relative_path,
        document_id=f"{run.case_id}-0",
    )
    case = CaseRecord(case_id=run.case_id, files=[stored])
    parsed_cache.put(run.case_id, background_jobs.cache_key(case), [parsed])

    class _RealPayloadPipeline:
        def __init__(self, _terminology) -> None:
            pass

        def run(self, case_id, documents, rules, provider, **kwargs) -> ReviewRun:
            assert case_id == run.case_id
            assert documents
            assert provider is not None
            progress = kwargs["progress"]
            checkpoint = kwargs["checkpoint"]
            checkpoint_findings = [
                Finding.model_validate(item) for item in fixture["checkpoint_findings"]
            ]
            for size in fixture["checkpoint_sizes"]:
                checkpoint(run, checkpoint_findings[:size])
            progress(
                "HUMAN_REVIEW",
                "TASK_COMPLETED",
                "completed",
                "智能初审任务执行完成",
                {"final_finding_count": 25},
            )
            return run

    monkeypatch.setattr(background_jobs, "ReviewPipeline", _RealPayloadPipeline)
    settings = SimpleNamespace(storage_root=storage_root)
    execute_args = (
        runtime,
        parsed_cache,
        settings,
        run.case_id,
        run.run_id,
        None,
        object(),
    )

    background_jobs.execute_review_job(*execute_args)

    read_session = runtime.session()
    read_repository = ReviewRepository(read_session)
    persisted = read_repository.get_run(run.run_id)
    events = read_repository.list_progress_events(run.run_id)
    assert persisted is not None
    assert persisted.final_status == "READY_FOR_HUMAN_REVIEW"
    assert len(persisted.findings) == 25
    assert len(persisted.rule_results) == 29
    assert sum(event.event_type == "TASK_COMPLETED" for event in events) == 1
    assert sum(event.event_type == "TASK_FAILED" for event in events) == 0
    assert not any("审查任务执行失败" in event.message for event in events)
    assert not any("AI服务调用失败" in event.message for event in events)
    original_event_count = len(events)
    read_session.close()

    # The terminal Run cannot be claimed again, so a duplicate dispatch is a no-op.
    background_jobs.execute_review_job(*execute_args)
    verify_session = runtime.session()
    verify_repository = ReviewRepository(verify_session)
    duplicate = verify_repository.get_run(run.run_id)
    duplicate_events = verify_repository.list_progress_events(run.run_id)
    assert duplicate is not None
    assert duplicate.final_status == "READY_FOR_HUMAN_REVIEW"
    assert len(duplicate.findings) == 25
    assert len(duplicate.rule_results) == 29
    assert len(duplicate_events) == original_event_count
    assert sum(event.event_type == "TASK_COMPLETED" for event in duplicate_events) == 1
    verify_session.close()
    runtime.dispose()
