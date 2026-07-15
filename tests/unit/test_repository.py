from __future__ import annotations

import pytest

from app.domain.enums import ExtractionMethod, Origin, ReviewStatus, RuleStatus, Severity
from app.domain.schemas import ParameterFact
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

    repo.update_finding_review("CASE-1", "F1", ReviewStatus.CONFIRMED, "专家确认")
    reviewed = ReviewRepository(create_session(db)).get_run("CASE-1")
    assert reviewed is not None
    assert reviewed.findings[0].review_status is ReviewStatus.CONFIRMED
    assert reviewed.findings[0].human_note == "专家确认"


def test_facts_round_trip_with_source_document_in_fresh_session(tmp_path):
    db = tmp_path / "review.db"
    fact = ParameterFact(
        fact_id="fact-1", canonical_name="capacity", raw_name="Capacity", raw_value="10",
        source_document="方案.docx", source_span_id="span-1", extraction_method=ExtractionMethod.REGEX,
    )
    ReviewRepository(create_session(db)).save_run(ReviewRun("CASE-facts", facts=[fact]))
    loaded = ReviewRepository(create_session(db)).get_run("CASE-facts")
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
    ReviewRepository(create_session(db)).save_run(ReviewRun("CASE-cn", facts=[fact]))
    loaded = ReviewRepository(create_session(db)).get_run("CASE-cn")
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
    with pytest.raises(ValueError, match="recycled"):
        repo.save_case(case)

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
            repo.update_finding_review("CASE-note", "F-note", ReviewStatus.CONFIRMED, note)


def test_update_finding_review_requires_case_scope(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))
    repo.save_run(ReviewRun("CASE-scope", findings=[Finding(
        finding_id="local", origin=Origin.RULE, category="c", severity=Severity.LOW,
        title="t", evidence_span_ids=[], needs_human_review=True,
    )]))
    with pytest.raises(KeyError):
        repo.update_finding_review("CASE-wrong", "local", ReviewStatus.CONFIRMED, "safe")


def test_save_run_validation_failure_does_not_replace_existing_run(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    repo.save_run(ReviewRun("CASE-atomic", findings=[Finding(
        finding_id="kept", origin=Origin.RULE, category="c", severity=Severity.LOW,
        title="kept", evidence_span_ids=[], needs_human_review=True,
    )]))
    with pytest.raises(ValueError):
        repo.save_run(ReviewRun("CASE-atomic", findings=[Finding(
            finding_id="bad id", origin=Origin.RULE, category="c", severity=Severity.LOW,
            title="replacement", evidence_span_ids=[], needs_human_review=True,
        )]))
    loaded = ReviewRepository(create_session(db)).get_run("CASE-atomic")
    assert loaded is not None and loaded.findings[0].finding_id == "kept"


def test_same_local_finding_id_is_allowed_in_two_cases(tmp_path):
    db = tmp_path / "review.db"
    repo = ReviewRepository(create_session(db))
    for case_id in ("CASE-A", "CASE-B"):
        repo.save_run(ReviewRun(case_id, findings=[Finding(
            finding_id="local-id", origin=Origin.RULE, category="c", severity=Severity.LOW,
            title="t", evidence_span_ids=[], needs_human_review=True,
        )]))
    assert ReviewRepository(create_session(db)).get_run("CASE-A").findings[0].finding_id == "local-id"
    assert ReviewRepository(create_session(db)).get_run("CASE-B").findings[0].finding_id == "local-id"


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
            finding_id=bad_identifier, origin=Origin.RULE, category="c", severity=Severity.LOW,
            title="safe", evidence_span_ids=["safe-span"], needs_human_review=True,
        )]))


def test_repository_never_accepts_secret_field(tmp_path):
    repo = ReviewRepository(create_session(tmp_path / "review.db"))

    assert not hasattr(repo, "save_api_key")
    assert "api_key" not in repo.persisted_field_names
