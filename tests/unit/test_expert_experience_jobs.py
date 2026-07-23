from __future__ import annotations

from datetime import timedelta
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from docx import Document
from pydantic import ValidationError

from app.domain.enums import Origin, ReviewStatus, Severity
from app.domain.schemas import Finding
from app.experience.repository import ExperienceRepository, utc_now
from app.experience.evidence import resolve_evidence
from app.experience.schemas import ExperienceSummary
from app.parsers.docx_parser import DocxParser
from app.persistence.db import create_session
from app.persistence.models import CaseRecord
from app.persistence.repository import ReviewRepository
from app.review.parsed_cache import ParsedDocumentCache
from app.review.pipeline import format_span_location
from app.review.pipeline import ReviewRun
from app.settings import Settings
from app.storage.case_files import StoredFile


def _summary() -> ExperienceSummary:
    return ExperienceSummary(
        experience_title="容量口径必须统一",
        problem_pattern="同一处理能力在不同章节使用了不一致的口径。",
        judgment_basis=["设备表和技术要求中的数值不一致"],
        recommended_action=["统一数值并复核关联章节"],
        applicable_scope="含设备能力参数的技术方案",
        keywords=["容量", "一致性"],
    )


def _repository(tmp_path, *, evidence_hash: str = "a" * 64):
    session = create_session(tmp_path / "review.db")
    review = ReviewRepository(session)
    run = ReviewRun(
        "CASE-experience-job",
        findings=[Finding(
            finding_id="F-1", origin=Origin.RULE, category="capacity",
            severity=Severity.HIGH, title="处理能力不一致", description="两处数值不同",
            suggestion="统一参数", evidence_span_ids=["document:p:0"],
            needs_human_review=True,
        )],
        final_status="READY_FOR_HUMAN_REVIEW",
    )
    run.evidence_text_hashes = {"document:p:0": evidence_hash}
    run.evidence_locations = {"document:p:0": "正文第1段"}
    review.save_run(run)
    review.update_finding_review(
        run.case_id, run.run_id, "F-1", ReviewStatus.CONFIRMED, "专家确认", True,
    )
    return session, review, ExperienceRepository(session), run


def _evidence_repository(tmp_path):
    storage = tmp_path / "storage"
    relative = "cases/CASE-evidence/input.docx"
    path = storage / relative
    path.parent.mkdir(parents=True)
    document = Document()
    document.add_paragraph("设计处理能力为每日五万吨，设备表应保持一致。")
    document.save(path)
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    session = create_session(storage / "review.db")
    review = ReviewRepository(session)
    review.save_case(CaseRecord(case_id="CASE-evidence", files=[StoredFile(
        storage_relative_path=relative, sha256=file_hash, size=path.stat().st_size,
        safe_name="input.docx",
    )]))
    parsed = DocxParser().parse(path, document_id="CASE-evidence-0")
    span = parsed.spans[0]
    run = ReviewRun(
        "CASE-evidence",
        findings=[Finding(
            finding_id="F-evidence", origin=Origin.RULE, category="capacity",
            severity=Severity.HIGH, title="能力参数不一致", description="设备表口径不同",
            suggestion="统一处理能力", evidence_span_ids=[span.span_id],
            needs_human_review=True,
        )], final_status="READY_FOR_HUMAN_REVIEW",
    )
    run.evidence_text_hashes = {span.span_id: span.text_hash}
    run.evidence_locations = {span.span_id: format_span_location(span)}
    review.save_run(run)
    review.update_finding_review(
        run.case_id, run.run_id, "F-evidence", ReviewStatus.CONFIRMED, "专家确认", True,
    )
    repository = ExperienceRepository(session)
    requested = repository.synchronize_after_review(run.case_id, run.run_id, "F-evidence")
    assert requested and repository.claim(requested.job_id, "worker")
    job = repository.get_job(requested.job_id)
    finding = repository._finding(run.case_id, run.run_id, "F-evidence")
    settings = Settings(storage_root=storage, runtime_root=tmp_path / "runtime", db_path=storage / "review.db")
    return repository, job, finding, parsed, path, settings


def test_model_schema_cannot_generate_or_overwrite_expert_judgment():
    payload = _summary().model_dump()
    payload["expert_judgment"] = "模型自行改判"
    with pytest.raises(ValidationError):
        ExperienceSummary.model_validate(payload)


def test_source_hash_contains_evidence_content_hash(tmp_path):
    session, _, repository, run = _repository(tmp_path)
    finding = repository._finding(run.case_id, run.run_id, "F-1")
    assert finding is not None
    first = repository.source_hash_for(finding)
    finding.review_run.evidence_text_hashes = {"document:p:0": "b" * 64}
    session.commit()
    assert repository.source_hash_for(finding) != first


def test_source_hash_rejects_missing_evidence_content_hash(tmp_path):
    _, _, repository, run = _repository(tmp_path)
    finding = repository._finding(run.case_id, run.run_id, "F-1")
    assert finding is not None
    finding.review_run.evidence_text_hashes = {}
    repository.session.commit()
    with pytest.raises(ValueError, match="content hash"):
        repository.source_hash_for(finding)


def test_unexpired_lease_is_not_recovered_and_expired_lease_is(tmp_path):
    session, _, repository, run = _repository(tmp_path)
    requested = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert requested and repository.claim(requested.job_id, "worker-a")
    assert requested.job_id not in repository.recover_expired_jobs()
    job = repository.get_job(requested.job_id)
    assert job is not None
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()
    assert not repository.complete(requested.job_id, "worker-a", _summary(), "mock", "mock")
    assert repository.get_job(requested.job_id).status == "RUNNING"
    assert requested.job_id in repository.recover_expired_jobs()
    assert repository.get_job(requested.job_id).status == "PENDING"


def test_old_worker_and_wrong_token_cannot_complete_after_recovery(tmp_path):
    session, _, repository, run = _repository(tmp_path)
    requested = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert requested and repository.claim(requested.job_id, "old-worker")
    job = repository.get_job(requested.job_id)
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()
    repository.recover_expired_jobs()
    assert repository.claim(requested.job_id, "new-worker")
    assert not repository.complete(requested.job_id, "old-worker", _summary(), "mock", "mock")
    assert not repository.complete(requested.job_id, "wrong-worker", _summary(), "mock", "mock")
    assert repository.complete(requested.job_id, "new-worker", _summary(), "mock", "mock")
    assert repository.active_count() == 1


def test_cancelling_during_summary_discards_worker_result(tmp_path):
    _, review, repository, run = _repository(tmp_path)
    requested = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert requested and repository.claim(requested.job_id, "worker")
    review.update_finding_review(
        run.case_id, run.run_id, "F-1", ReviewStatus.CONFIRMED, "取消沉淀", False,
    )
    repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert not repository.complete(requested.job_id, "worker", _summary(), "mock", "mock")
    assert repository.active_count() == 0


def test_deleting_during_summary_revokes_lease_and_never_counts(tmp_path):
    _, _, repository, run = _repository(tmp_path)
    requested = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert requested and repository.claim(requested.job_id, "worker")
    repository.delete(requested.job_id, "人工删除")
    assert not repository.complete(requested.job_id, "worker", _summary(), "mock", "mock")
    assert repository.active_count() == 0


def test_review_change_switches_pointer_and_stales_old_worker(tmp_path):
    _, review, repository, run = _repository(tmp_path)
    first = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert first and repository.claim(first.job_id, "old-worker")
    review.update_finding_review(
        run.case_id, run.run_id, "F-1", ReviewStatus.MODIFIED, "专家修正了结论", True,
    )
    second = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert second and second.job_id != first.job_id
    assert not repository.complete(first.job_id, "old-worker", _summary(), "mock", "mock")
    assert repository.get_job(first.job_id).status == "STALE"


def test_two_page_saves_reuse_same_job_and_completed_restore_skips_model(tmp_path):
    _, _, repository, run = _repository(tmp_path)
    first = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    second = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert first and second and first.job_id == second.job_id
    assert repository.claim(first.job_id, "worker")
    assert repository.complete(first.job_id, "worker", _summary(), "mock", "mock")
    repository.delete(first.job_id, None)
    restored, requested = repository.restore(first.job_id)
    assert requested is None
    assert restored.status == "COMPLETED"
    assert repository.active_count() == 1


def test_two_pages_concurrently_reuse_one_job(tmp_path):
    session, _, repository, run = _repository(tmp_path)
    session.close()
    barrier = Barrier(2)

    def save_from_page() -> str:
        local = create_session(tmp_path / "review.db")
        try:
            barrier.wait()
            requested = ExperienceRepository(local).synchronize_after_review(
                run.case_id, run.run_id, "F-1"
            )
            assert requested is not None
            return requested.job_id
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = list(pool.map(lambda _: save_from_page(), range(2)))
    assert len(set(job_ids)) == 1


def test_completion_and_delete_race_never_restores_deleted_experience(tmp_path):
    session, _, repository, run = _repository(tmp_path)
    requested = repository.synchronize_after_review(run.case_id, run.run_id, "F-1")
    assert requested and repository.claim(requested.job_id, "worker")
    session.close()
    barrier = Barrier(2)

    def complete_job():
        local = create_session(tmp_path / "review.db")
        try:
            barrier.wait()
            return ExperienceRepository(local).complete(
                requested.job_id, "worker", _summary(), "mock", "mock"
            )
        finally:
            local.close()

    def delete_job():
        local = create_session(tmp_path / "review.db")
        try:
            barrier.wait()
            ExperienceRepository(local).delete(requested.job_id, "并发删除")
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete_job), pool.submit(delete_job)]
        for future in futures:
            future.result()
    final_session = create_session(tmp_path / "review.db")
    final = ExperienceRepository(final_session)
    job = final.get_job(requested.job_id)
    assert job is not None and job.experience_is_deleted is True
    assert job.status == "DELETED"
    assert final.active_count() == 0
    final_session.close()


def test_evidence_cache_precedes_reparse_and_snapshot_precedes_cache(tmp_path, monkeypatch):
    repository, job, finding, parsed, _, settings = _evidence_repository(tmp_path)
    cache = ParsedDocumentCache()
    cache.put("CASE-evidence", finding.review_run.case.files[0].sha256, [parsed])
    # cache_key for this one-file case equals its document SHA-256.
    monkeypatch.setattr(DocxParser, "parse", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reparse")))
    snapshot = resolve_evidence(repository, job, finding, cache, settings)
    assert snapshot[0]["text"].startswith("设计处理能力")
    repository.save_evidence_snapshot(job.job_id, "worker", snapshot)
    empty_cache = ParsedDocumentCache()
    assert resolve_evidence(repository, job, finding, empty_cache, settings) == snapshot


def test_document_hash_mismatch_fails_before_reparse(tmp_path, monkeypatch):
    repository, job, finding, _, path, settings = _evidence_repository(tmp_path)
    path.write_bytes(path.read_bytes() + b"tampered")
    called = False

    def unexpected_parse(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(DocxParser, "parse", unexpected_parse)
    with pytest.raises(ValueError, match="DOCX"):
        resolve_evidence(repository, job, finding, ParsedDocumentCache(), settings)
    assert called is False


def test_anchor_mismatch_fails_without_persisting_snapshot(tmp_path):
    repository, job, finding, parsed, _, settings = _evidence_repository(tmp_path)
    finding.review_run.evidence_locations = {parsed.spans[0].span_id: "错误位置"}
    repository.session.commit()
    cache = ParsedDocumentCache()
    cache.put("CASE-evidence", finding.review_run.case.files[0].sha256, [parsed])
    with pytest.raises(ValueError):
        resolve_evidence(repository, job, finding, cache, settings)
    assert not job.evidence_snapshot
