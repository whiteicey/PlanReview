from __future__ import annotations

import pytest
import hashlib
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.enums import ExtractionMethod, LLMStatus, Origin, ReviewStatus, RuleStatus, Severity
from app.domain.schemas import ParameterFact
from app.domain.schemas import Finding, RuleResult
from app.persistence.db import create_session
from app.persistence.models import Base, CaseRecord
from app.persistence.repository import ReviewRepository
from app.experience.repository import ExperienceRepository
from app.experience.schemas import ExperienceSummary
from app.review.pipeline import ReviewRun
from app.storage.case_files import StoredFile


def _complete_experience(repo: ReviewRepository, run: ReviewRun, finding_id: str) -> str:
    experiences = ExperienceRepository(repo.session)
    finding = experiences._finding(run.case_id, run.run_id, finding_id)
    assert finding is not None
    hashes = dict(finding.review_run.evidence_text_hashes or {})
    for span_id in finding.evidence_span_ids:
        hashes.setdefault(span_id, hashlib.sha256(span_id.encode("utf-8")).hexdigest())
    finding.review_run.evidence_text_hashes = hashes
    repo.session.commit()
    requested = experiences.synchronize_after_review(run.case_id, run.run_id, finding_id)
    assert requested is not None
    token = str(uuid4())
    assert experiences.claim(requested.job_id, token)
    summary = ExperienceSummary(
        experience_title="专家经验归纳",
        problem_pattern="同类问题会重复出现。",
        judgment_basis=["专家已确认"],
        recommended_action=["按专家意见处理"],
        applicable_scope="同类技术方案",
        keywords=["专家经验", "复核"],
    )
    assert experiences.complete(requested.job_id, token, summary, "mock", "mock")
    return requested.job_id


def test_ai_batch_checkpoint_is_immediately_persisted_and_final_save_replaces_it():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = ReviewRepository(Session(engine, expire_on_commit=False))
    repo.save_case(CaseRecord(case_id="CASE-checkpoint"))
    run = ReviewRun("CASE-checkpoint")
    repo.create_running_run(run.case_id, run.run_id)
    token = str(uuid4())
    assert repo.claim_running_run(run.run_id, token)
    run.llm_status = LLMStatus.COMPLETED_PARTIAL
    run.llm_finding_count = 1
    run.candidate_count = 1
    run.valid_count = 1
    run.rejected_count = 0
    run.batch_count = 5
    run.batch_metrics = [{"batch_id": "v111-01", "valid_count": 1}]
    partial = Finding(
        finding_id="llm-b01-0", origin=Origin.LLM, category="consistency",
        severity=Severity.MEDIUM, title="批次结果", description="批次结果",
        suggestion="复核", evidence_span_ids=["span-1"], needs_human_review=True,
    )

    repo.checkpoint_running_run(run, token, [partial])
    persisted = repo.get_run(run.run_id)
    assert persisted is not None
    assert persisted.llm_status is LLMStatus.COMPLETED_PARTIAL
    assert persisted.batch_metrics[0]["batch_id"] == "v111-01"
    assert [finding.finding_id for finding in persisted.findings] == ["llm-b01-0"]

    run.findings = []
    run.final_status = "READY_FOR_HUMAN_REVIEW"
    repo.finish_running_run(run, token)
    final = repo.get_run(run.run_id)
    assert final is not None and final.findings == []
    repo.session.close()
    engine.dispose()


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
        validation_reason_code="invalid_json",
        candidate_count=None,
        valid_count=None,
        rejected_count=None,
        available_span_count=135,
        selected_span_count=40,
        selected_character_count=2610,
        coverage_ratio=round(40 / 135, 4),
    )

    assert repo.save_run(run) == run.run_id
    # A new session proves this is an SQLite round-trip rather than a process cache.
    loaded = ReviewRepository(create_session(db)).get_run(run.run_id)

    assert loaded is not None
    assert loaded.case_id == "CASE-1"
    assert loaded.final_status == "READY_FOR_HUMAN_REVIEW"
    assert loaded.rule_results[0].rule_id == "R1"
    assert loaded.findings[0].finding_id == "F1"
    assert loaded.validation_reason_code == "invalid_json"
    assert loaded.candidate_count is None
    assert (loaded.available_span_count, loaded.selected_span_count, loaded.selected_character_count) == (135, 40, 2610)
    assert loaded.coverage_ratio == round(40 / 135, 4)

    repo.update_finding_review("CASE-1", run.run_id, "F1", ReviewStatus.CONFIRMED, "专家确认")
    reviewed = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert reviewed is not None
    assert reviewed.findings[0].review_status is ReviewStatus.CONFIRMED
    assert reviewed.findings[0].human_note == "专家确认"


def test_expert_experience_summary_uses_live_non_pending_findings_only(tmp_path):
    """Experience is a review-side flag, never a duplicate record or cached total."""
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    run = ReviewRun(
        "CASE-experience",
        findings=[
            Finding(
                finding_id="F-experience", origin=Origin.RULE, category="capacity",
                severity=Severity.MEDIUM, title="需要专家复核", evidence_span_ids=["span-1"],
                needs_human_review=True,
            )
        ],
        final_status="READY_FOR_HUMAN_REVIEW",
    )
    repo.save_run(run)

    empty = repo.get_expert_experience_summary()
    assert (empty.total_count, empty.updated_at) == (0, None)

    saved = repo.update_finding_review(
        run.case_id, run.run_id, "F-experience", ReviewStatus.CONFIRMED, "专家确认", True,
    )
    assert (saved.total_count, saved.updated_at) == (0, None)
    _complete_experience(repo, run, "F-experience")
    assert repo.get_expert_experience_summary().total_count == 1
    first = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert first is not None and first.findings[0].is_expert_experience is True
    saved_at = first.findings[0].experience_saved_at

    # Re-saving the same run/finding updates the review record but never adds a count.
    repeated = repo.update_finding_review(
        run.case_id, run.run_id, "F-experience", ReviewStatus.CONFIRMED, "补充专家备注", True,
    )
    assert repeated.total_count == 1
    ExperienceRepository(repo.session).synchronize_after_review(
        run.case_id, run.run_id, "F-experience"
    )
    assert repo.get_expert_experience_summary().total_count == 0
    _complete_experience(repo, run, "F-experience")
    assert repo.get_expert_experience_summary().total_count == 1
    second = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert second is not None
    assert second.findings[0].human_note == "补充专家备注"

    # Pending is never an effective experience, even if the client checked the box.
    cancelled = repo.update_finding_review(
        run.case_id, run.run_id, "F-experience", ReviewStatus.PENDING, "等待补充证据", True,
    )
    assert (cancelled.total_count, cancelled.updated_at) == (0, None)
    restarted = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert restarted is not None
    assert restarted.findings[0].is_expert_experience is False
    assert restarted.findings[0].human_note == "等待补充证据"


def test_expert_experience_digest_groups_statuses_categories_and_hides_recycled_cases(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    run = ReviewRun("CASE-digest", findings=[
        Finding(
            finding_id="F-capacity", origin=Origin.RULE, category="capacity",
            severity=Severity.MEDIUM, title="处理能力不足", rule_id="CAPACITY-001",
            evidence_span_ids=["span-1"], needs_human_review=True,
        ),
        Finding(
            finding_id="F-term", origin=Origin.RULE, category="terminology",
            severity=Severity.LOW, title="术语需统一", rule_id="TERM-001",
            evidence_span_ids=["span-2"], needs_human_review=True,
        ),
    ], final_status="READY_FOR_HUMAN_REVIEW")
    repo.save_run(run)
    repo.update_finding_review(
        run.case_id, run.run_id, "F-capacity", ReviewStatus.CONFIRMED, "按峰值工况校核", True,
    )
    repo.update_finding_review(
        run.case_id, run.run_id, "F-term", ReviewStatus.REJECTED, "属于可接受别名", True,
    )

    _complete_experience(repo, run, "F-capacity")
    _complete_experience(repo, run, "F-term")
    digest = repo.get_expert_experience_digest(recent_limit=1)
    assert digest.total_count == 2
    assert digest.status_counts["confirmed"] == 1
    assert digest.status_counts["rejected"] == 1
    assert [(item.category, item.count) for item in digest.categories] == [
        ("capacity", 1), ("terminology", 1),
    ]
    assert len(digest.recent_conclusions) == 1
    assert digest.recent_conclusions[0].expert_note == "属于可接受别名"
    assert not hasattr(digest.recent_conclusions[0], "case_id")
    assert not hasattr(digest.recent_conclusions[0], "evidence_span_ids")

    repo.delete_case_to_recycle_bin(run.case_id)
    assert repo.get_expert_experience_summary().total_count == 0
    assert repo.get_expert_experience_digest().total_count == 0


def test_expert_experience_review_failure_rolls_back_without_changing_live_summary(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    run = ReviewRun(
        "CASE-experience-rollback",
        findings=[Finding(
            finding_id="F-rollback", origin=Origin.RULE, category="capacity", severity=Severity.LOW,
            title="待复核", evidence_span_ids=["span-1"], needs_human_review=True,
        )],
        final_status="READY_FOR_HUMAN_REVIEW",
    )
    repo.save_run(run)
    repo.update_finding_review(run.case_id, run.run_id, "F-rollback", ReviewStatus.CONFIRMED, "有效备注", True)
    _complete_experience(repo, run, "F-rollback")
    with pytest.raises(ValueError):
        repo.update_finding_review(
            run.case_id, run.run_id, "F-rollback", ReviewStatus.REJECTED, "api_key=not-a-real-key", False,
        )
    summary = ReviewRepository(create_session(db)).get_expert_experience_summary()
    assert summary.total_count == 1


def test_merged_fact_provenance_survives_database_restart(tmp_path):
    db = tmp_path / "review.db"
    fact = ParameterFact(
        fact_id="table-fact",
        canonical_name="高峰产量",
        raw_name="高峰产量",
        raw_value="5",
        normalized_value=50_000.0,
        canonical_unit="m^3/day",
        unit_category="flow",
        subject="气田_A",
        time_scope="达产期",
        statistical_scope="日峰值",
        source_document="D",
        source_span_id="table-span",
        extraction_method=ExtractionMethod.TABLE,
        merged_fact_ids=["prose-fact"],
        merged_span_ids=["prose-span"],
    )
    run = ReviewRun("CASE-provenance", facts=[fact])

    ReviewRepository(create_session(db)).save_run(run)
    loaded = ReviewRepository(create_session(db)).get_run(run.run_id)

    assert loaded is not None
    assert loaded.facts[0].merged_fact_ids == ["prose-fact"]
    assert loaded.facts[0].merged_span_ids == ["prose-span"]


def test_whole_document_rule_result_evidence_persists(tmp_path):
    # Whole-document rules (required sections, evidence gate) and the Mock legiti-
    # mately reference every span in the document; a real DOCX already has >100
    # spans, so the evidence list bound must accommodate a full document.
    db = tmp_path / "review.db"
    span_ids = [f"D:p:{index}" for index in range(400)]
    run = ReviewRun(
        "CASE-many-spans",
        rule_results=[
            RuleResult(
                rule_id="COMPLETENESS-001",
                status=RuleStatus.PASS,
                severity=Severity.MEDIUM,
                category="completeness",
                message="章节齐全",
                evidence_span_ids=span_ids,
            )
        ],
    )
    ReviewRepository(create_session(db)).save_run(run)
    loaded = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert loaded is not None
    assert len(loaded.rule_results[0].evidence_span_ids) == 400


def test_absurdly_large_evidence_list_is_still_rejected(tmp_path):
    db = tmp_path / "review.db"
    run = ReviewRun(
        "CASE-too-many",
        rule_results=[
            RuleResult(
                rule_id="R",
                status=RuleStatus.PASS,
                severity=Severity.LOW,
                category="completeness",
                message="x",
                evidence_span_ids=[f"D:p:{index}" for index in range(20_001)],
            )
        ],
    )
    with pytest.raises(ValueError):
        ReviewRepository(create_session(db)).save_run(run)


def test_rule_result_and_finding_with_chinese_parameter_persist(tmp_path):
    # Rule results and findings carry a Chinese parameter name (高峰产量); these
    # must persist like fact vocabulary, not be rejected as unsafe identifiers.
    db = tmp_path / "review.db"
    run = ReviewRun(
        "CASE-cn-param",
        rule_results=[
            RuleResult(
                rule_id="CAPACITY-001",
                status=RuleStatus.FAIL,
                severity=Severity.HIGH,
                category="cross_domain",
                parameter="高峰产量",
                message="高峰产量超过地面处理能力",
                evidence_span_ids=["span-1"],
            )
        ],
        findings=[
            Finding(
                finding_id="F-cn",
                origin=Origin.RULE,
                category="cross_domain",
                severity=Severity.HIGH,
                parameter="高峰产量",
                title="高峰产量需复核",
                description="高峰产量超过地面处理能力",
                suggestion="请补充证据并由专家复核",
                rule_id="CAPACITY-001",
                evidence_span_ids=["span-1"],
                needs_human_review=True,
            )
        ],
    )
    ReviewRepository(create_session(db)).save_run(run)
    loaded = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert loaded is not None
    assert loaded.rule_results[0].parameter == "高峰产量"
    assert loaded.findings[0].parameter == "高峰产量"


def test_facts_round_trip_with_source_document_in_fresh_session(tmp_path):
    db = tmp_path / "review.db"
    fact = ParameterFact(
        fact_id="fact-1", canonical_name="capacity", raw_name="Capacity", raw_value="10",
        source_document="方案.docx", source_span_id="span-1", extraction_method=ExtractionMethod.REGEX,
    )
    run = ReviewRun("CASE-facts", facts=[fact])
    ReviewRepository(create_session(db)).save_run(run)
    loaded = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert loaded is not None
    assert loaded.facts[0].source_document == "方案.docx"
    assert loaded.facts[0].fact_id == "fact-1"
    assert loaded.facts[0].source_span_id == "span-1"


def test_facts_with_chinese_domain_vocabulary_persist(tmp_path):
    # Real extracted facts use Chinese canonical names and scope labels; these
    # must persist, not be rejected as unsafe identifiers.
    db = tmp_path / "review.db"
    fact = ParameterFact(
        fact_id="fact-cap",
        canonical_name="高峰产量",
        raw_name="高峰产量",
        raw_value="230",
        normalized_value=2_300_000.0,
        raw_unit="万m³/d",
        canonical_unit="m^3/day",
        subject="气田_A",
        time_scope="达产期",
        statistical_scope="设计工况",
        condition="峰值",
        source_document="方案.docx",
        source_version="V1.0",
        source_span_id="DEMO:t:1:1:1",
        extraction_method=ExtractionMethod.TABLE,
    )
    run = ReviewRun("CASE-cn", facts=[fact])
    ReviewRepository(create_session(db)).save_run(run)
    loaded = ReviewRepository(create_session(db)).get_run(run.run_id)
    assert loaded is not None
    stored = loaded.facts[0]
    assert stored.canonical_name == "高峰产量"
    assert stored.subject == "气田_A"
    assert stored.time_scope == "达产期"
    assert stored.statistical_scope == "设计工况"
    assert stored.condition == "峰值"
    assert stored.raw_unit == "万m³/d"


def test_facts_with_control_chars_or_path_in_vocabulary_are_rejected(tmp_path):
    # The relaxed validator still fails closed on control characters and
    # secret-like content in persisted vocabulary metadata. A slash is allowed
    # because units legitimately contain it (万m³/d), so it is not tested here.
    db = tmp_path / "review.db"
    for bad in ("高峰\n产量", "高峰\t产量", "api_key=sk-abcdefghijkl"):
        fact = ParameterFact(
            fact_id="fact-bad",
            canonical_name=bad,
            raw_name="x",
            raw_value="1",
            source_document="方案.docx",
            source_span_id="span-1",
            extraction_method=ExtractionMethod.REGEX,
        )
        with pytest.raises(ValueError):
            ReviewRepository(create_session(db)).save_run(ReviewRun("CASE-bad", facts=[fact]))


def test_same_finding_id_is_scoped_to_each_append_only_run(tmp_path):
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
    restarted = ReviewRepository(create_session(db))
    runs = restarted.list_runs("CASE-rerun")
    assert {item.run_id for item in runs} == {first.run_id, second.run_id}
    assert restarted.get_run(first.run_id).findings[0].title == "first"
    assert restarted.get_run(second.run_id).findings[0].title == "second"


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
    with pytest.raises(ValueError, match="recycled"):
        repo.save_case(case)

    restarted = ReviewRepository(create_session(db))
    assert restarted.list_runs("CASE-2") == []
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


def test_unicode_safe_name_persists(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    case = CaseRecord(case_id="CASE-unicode", files=[StoredFile(
        storage_relative_path="cases/CASE-unicode/documents/方案.docx",
        sha256="a" * 64, size=1, safe_name="方案.docx",
    )])
    assert repo.save_case(case) == "CASE-unicode"


@pytest.mark.parametrize("safe_name", ["../outside.pdf", "a.pdf", "a\\\\b.docx", "a" * 252 + ".docx"])
def test_safe_name_must_be_portable_docx_basename(tmp_path, safe_name):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    with pytest.raises(ValueError, match="safe_name"):
        repo.save_case(CaseRecord(
            case_id="CASE-name",
            files=[StoredFile(
                storage_relative_path="cases/CASE-name/documents/a.docx",
                sha256="a" * 64, size=1, safe_name=safe_name,
            )],
        ))


def test_human_note_rejects_explicit_credentials_and_request_dumps(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    run = ReviewRun("CASE-note", findings=[Finding(
        finding_id="F-note", origin=Origin.RULE, category="other", severity=Severity.LOW,
        title="t", evidence_span_ids=[], needs_human_review=True,
    )])
    repo.save_run(run)

    forbidden_notes = [
        "api_key=sk-test-secret-value",
        "token: abcdefghijklmnop",
        "Authorization: Bearer abcdefghijklmnop",
        'request body: {"messages": ["full body"]}',
    ]
    for note in forbidden_notes:
        with pytest.raises(ValueError, match="sensitive"):
            repo.update_finding_review("CASE-note", run.run_id, "F-note", ReviewStatus.CONFIRMED, note)


def test_human_note_length_and_normal_business_prose_boundaries(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    run = ReviewRun("CASE-note-boundary", findings=[Finding(
        finding_id="F-note", origin=Origin.RULE, category="other", severity=Severity.LOW,
        title="t", evidence_span_ids=[], needs_human_review=True,
    )])
    repo.save_run(run)

    for length in (3_999, 4_000):
        note = "审" * length
        repo.update_finding_review(
            "CASE-note-boundary", run.run_id, "F-note", ReviewStatus.CONFIRMED, note
        )
        assert repo.get_run(run.run_id).findings[0].human_note == note
    with pytest.raises(ValueError, match="4000"):
        repo.update_finding_review(
            "CASE-note-boundary", run.run_id, "F-note", ReviewStatus.CONFIRMED, "审" * 4_001
        )

    prose = ("第一段：token 是普通业务术语。\n\n第二段：继续说明。\n\n" + "正常意见" * 300)
    repo.update_finding_review(
        "CASE-note-boundary", run.run_id, "F-note", ReviewStatus.CONFIRMED, prose
    )
    assert repo.get_run(run.run_id).findings[0].human_note == prose


def test_update_finding_review_requires_case_scope(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    run = ReviewRun("CASE-scope", findings=[Finding(
        finding_id="local", origin=Origin.RULE, category="other", severity=Severity.LOW,
        title="t", evidence_span_ids=[], needs_human_review=True,
    )])
    repo.save_run(run)
    with pytest.raises(KeyError):
        repo.update_finding_review("CASE-wrong", run.run_id, "local", ReviewStatus.CONFIRMED, "safe")


def test_save_run_validation_failure_does_not_replace_existing_run(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    kept = ReviewRun("CASE-atomic", findings=[Finding(
        finding_id="kept", origin=Origin.RULE, category="other", severity=Severity.LOW,
        title="kept", evidence_span_ids=[], needs_human_review=True,
    )])
    repo.save_run(kept)
    with pytest.raises(ValueError):
        repo.save_run(ReviewRun("CASE-atomic", findings=[Finding(
            finding_id="bad id", origin=Origin.RULE, category="other", severity=Severity.LOW,
            title="replacement", evidence_span_ids=[], needs_human_review=True,
        )]))
    loaded = ReviewRepository(create_session(db)).get_run(kept.run_id)
    assert loaded is not None and loaded.findings[0].finding_id == "kept"


def test_same_local_finding_id_is_allowed_in_two_cases(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    run_ids = {}
    for case_id in ("CASE-A", "CASE-B"):
        run = ReviewRun(case_id, findings=[Finding(
            finding_id="local-id", origin=Origin.RULE, category="other", severity=Severity.LOW,
            title="t", evidence_span_ids=[], needs_human_review=True,
        )])
        run_ids[case_id] = repo.save_run(run)
    restarted = ReviewRepository(create_session(db))
    assert restarted.get_run(run_ids["CASE-A"]).findings[0].finding_id == "local-id"
    assert restarted.get_run(run_ids["CASE-B"]).findings[0].finding_id == "local-id"


def test_recycled_case_cannot_be_saved_without_restore(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    repo.save_run(ReviewRun("CASE-recycled"))
    repo.delete_case_to_recycle_bin("CASE-recycled")
    with pytest.raises(ValueError, match="recycled"):
        repo.save_run(ReviewRun("CASE-recycled"))
    repo.save_run(ReviewRun("CASE-fresh"))


@pytest.mark.parametrize("bad_identifier", ["bad id", "../escape", "api/key", "原始文本"])
def test_identifier_fields_fail_closed(tmp_path, bad_identifier):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    with pytest.raises(ValueError):
        repo.save_run(ReviewRun("CASE-ident", findings=[Finding(
            finding_id=bad_identifier, origin=Origin.RULE, category="other", severity=Severity.LOW,
            title="safe", evidence_span_ids=["safe-span"], needs_human_review=True,
        )]))


def test_repository_never_accepts_secret_field(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))

    assert not hasattr(repo, "save_api_key")
    assert "api_key" not in repo.persisted_field_names


def test_finding_text_boundaries_and_business_terms_survive_round_trip(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    ordinary = "token: 是分页令牌的普通业务字段；document text 表示待审文档文字。"
    accepted = ReviewRun("CASE-finding-text", findings=[Finding(
        finding_id="safe", origin=Origin.RULE, category="other", severity=Severity.LOW,
        title="题" * 200, description=ordinary, suggestion="建" * 4_000,
        evidence_span_ids=["safe-span"], needs_human_review=True,
    )])

    repo.save_run(accepted)
    loaded = ReviewRepository(create_session(db)).get_run(accepted.run_id)
    assert loaded is not None
    assert loaded.findings[0].title == "题" * 200
    assert loaded.findings[0].description == ordinary
    assert loaded.findings[0].suggestion == "建" * 4_000

    with pytest.raises(ValueError, match="title"):
        repo.save_run(ReviewRun("CASE-title-too-long", findings=[Finding(
            finding_id="long", origin=Origin.RULE, category="other", severity=Severity.LOW,
            title="题" * 201, evidence_span_ids=["safe-span"], needs_human_review=True,
        )]))
