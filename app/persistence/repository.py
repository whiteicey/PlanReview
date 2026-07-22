"""Repository for durable, deliberately limited review metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
import logging
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import (
    BlockType,
    ExtractionMethod,
    LLMStatus,
    Origin,
    PipelineStage,
    ReviewStatus,
    RuleStatus,
    Severity,
)
from app.domain.ids import normalize_review_run_id
from app.domain.schemas import Finding, ParameterFact, RuleResult, SourceSpan, StageRecord
from app.persistence.models import (
    CaseFileORM,
    CaseORM,
    CaseRecord,
    FindingORM,
    FileOperationAuditORM,
    RecycleBinORM,
    ReviewProgressEventORM,
    ReviewRunORM,
    RuleResultORM,
)
from app.review.pipeline import ReviewRun
from app.review.ledgers import (
    DEFAULT_LEDGER_MAX_ENTRIES,
    LEDGER_SCHEMA_VERSION,
)
from app.llm.provider import VALIDATION_REASON_CODES
from app.review.progress import ProgressEvent, safe_progress_payload, utc_now
from app.security.finding_text import validate_finding_text
from app.storage.case_files import StoredFile

# Evidence span identifiers use colon-separated parser coordinates (for example,
# ``document:p:0``); they remain metadata identifiers and never file paths.
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

# Upper bound on an evidence/fact id list. A 300-page DOCX can have thousands of
# spans and a whole-document rule may cite them all, so this is generous; it only
# exists to reject pathological, non-document input.
_MAX_EVIDENCE_ITEMS = 20_000


@dataclass(frozen=True)
class ExpertExperienceSummary:
    total_count: int
    updated_at: datetime | None

# Metadata keys are matched exactly (or by an explicitly dangerous suffix).
_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "authorization",
        "request_body",
        "response_body",
        "external_request",
        "raw_docx",
        "document_body",
        "payload",
        "messages",
        "document_content",
        "document_text",
        "raw_content",
        "full_body",
        "request",
        "response",
        "raw_prompt",
        "raw_response",
    }
)
_SENSITIVE_METADATA_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_token",
    "_secret",
    "_password",
    "_authorization",
    "_request_body",
    "_response_body",
    "_raw_prompt",
    "_raw_response",
)
_STATISTICS_ALLOWED_KEYS = frozenset(
    {
        "document_count",
        "response_status",
        "response_sections",
        "request_count",
        "rule_count",
        "fact_count",
        "finding_count",
    }
)

LOGGER = logging.getLogger(__name__)


class ReviewRepository:
    """Persist review artefacts in SQLite without retaining source document bodies."""

    persisted_field_names = frozenset(
        {
            "case_id",
            "run_id",
            "files",
            "sha256",
            "storage_relative_path",
            "statistics",
            "facts",
            "stage_records",
            "final_status",
            "rule_results",
            "findings",
            "llm_provider",
            "llm_model",
            "llm_status",
            "llm_finding_count",
            "llm_error_summary",
            "validation_reason_code",
            "candidate_count",
            "valid_count",
            "rejected_count",
            "available_span_count",
            "selected_span_count",
            "selected_character_count",
            "coverage_ratio",
            "git_sha", "prompt_version", "evidence_selector_version",
            "max_tokens", "timeout", "temperature", "batch_count", "batch_metrics",
            "premerge_finding_count", "postmerge_finding_count", "deduplicated_finding_count",
            "stop_reason", "ai_guard_rejections", "deduplication_records",
            "packet_lifecycle_ledger", "ai_candidate_lifecycle_ledger",
            "rule_metrics",
            "review_status",
            "human_note",
        }
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_case(self, case: CaseRecord) -> str:
        """Commit safe case metadata and relative file references."""
        if not isinstance(case, CaseRecord) or not case.case_id:
            raise ValueError("case_id is required")
        _safe_identifier(case.case_id, "case_id")
        self._validate_case_files(case.files)
        safe_statistics = _sanitize_statistics(case.statistics)
        try:
            existing = self.session.get(CaseORM, case.case_id)
            if existing is not None and self.session.get(RecycleBinORM, case.case_id) is not None:
                raise ValueError("cannot save a recycled case without explicit restore")
            if existing is None:
                existing = CaseORM(case_id=case.case_id, statistics=safe_statistics)
                self.session.add(existing)
            else:
                existing.statistics = safe_statistics
                existing.files.clear()
            existing.files.extend(
                CaseFileORM(
                    storage_relative_path=item.storage_relative_path,
                    sha256=item.sha256,
                    size=item.size,
                    safe_name=item.safe_name,
                )
                for item in case.files
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(existing)
        return existing.case_id

    def save_run(self, run: ReviewRun) -> str:
        """Append one immutable review run and its run-scoped children."""
        if not isinstance(run, ReviewRun) or not run.case_id:
            raise ValueError("run.case_id is required")
        _safe_identifier(run.case_id, "case_id")
        run_id = normalize_review_run_id(run.run_id)
        _safe_text(run.final_status, "final status")
        # Validate every payload before touching ORM state. The commit block below
        # still rolls back database errors; this preflight prevents a validation
        # exception from leaving pending replacement rows in the Session.
        _validate_run_payload(run, tolerate_observability=True)
        try:
            case = self.session.get(CaseORM, run.case_id)
            if case is None:
                case = CaseORM(case_id=run.case_id, statistics={})
                self.session.add(case)
            if self.session.get(RecycleBinORM, run.case_id) is not None:
                raise ValueError("cannot save a recycled case without explicit restore")
            if self.session.scalar(select(ReviewRunORM.id).where(ReviewRunORM.run_id == run_id)):
                raise ValueError("run_id already exists")
            record = ReviewRunORM(
                run_id=run_id,
                case=case,
                final_status=run.final_status,
                facts=_sanitize_facts(run.facts),
                stage_records=_safe_observability_json(
                    _models_to_dict(run.stage_records), "stage_records", []
                ),
                evidence_text_hashes=_sanitize_evidence_text_hashes(run.evidence_text_hashes),
                evidence_locations=_sanitize_evidence_locations(run.evidence_locations),
                llm_provider=_safe_vocabulary(run.llm_provider, "llm_provider", optional=True),
                llm_model=_safe_vocabulary(run.llm_model, "llm_model", optional=True),
                llm_status=LLMStatus(run.llm_status).value,
                llm_finding_count=_safe_nonnegative_int(run.llm_finding_count, "llm_finding_count"),
                llm_error_summary=_safe_text(run.llm_error_summary, "llm_error_summary"),
                validation_reason_code=_safe_reason_code(run.validation_reason_code),
                candidate_count=_safe_optional_nonnegative_int(run.candidate_count, "candidate_count"),
                valid_count=_safe_optional_nonnegative_int(run.valid_count, "valid_count"),
                rejected_count=_safe_optional_nonnegative_int(run.rejected_count, "rejected_count"),
                available_span_count=_safe_optional_nonnegative_int(run.available_span_count, "available_span_count"),
                selected_span_count=_safe_optional_nonnegative_int(run.selected_span_count, "selected_span_count"),
                selected_character_count=_safe_optional_nonnegative_int(run.selected_character_count, "selected_character_count"),
                coverage_ratio=_safe_optional_coverage_ratio(run.coverage_ratio),
                git_sha=_safe_identifier(run.git_sha, "git_sha", optional=True),
                prompt_version=_safe_identifier(run.prompt_version, "prompt_version", optional=True),
                evidence_selector_version=_safe_identifier(run.evidence_selector_version, "evidence_selector_version", optional=True),
                max_tokens=_safe_optional_nonnegative_int(run.max_tokens, "max_tokens"),
                timeout=_safe_optional_nonnegative_float(run.timeout, "timeout"),
                temperature=_safe_optional_nonnegative_float(run.temperature, "temperature"),
                batch_count=_safe_nonnegative_int(run.batch_count, "batch_count"),
                batch_metrics=_safe_observability_json(run.batch_metrics, "batch_metrics", []),
                premerge_finding_count=_safe_nonnegative_int(run.premerge_finding_count, "premerge_finding_count"),
                postmerge_finding_count=_safe_nonnegative_int(run.postmerge_finding_count, "postmerge_finding_count"),
                deduplicated_finding_count=_safe_nonnegative_int(run.deduplicated_finding_count, "deduplicated_finding_count"),
                stop_reason=_safe_vocabulary(run.stop_reason, "stop_reason", optional=True),
                ai_guard_rejections=_safe_observability_json(
                    run.ai_guard_rejections, "ai_guard_rejections", []
                ),
                deduplication_records=_safe_observability_json(
                    run.deduplication_records, "deduplication_records", []
                ),
                packet_lifecycle_ledger=_safe_observability_ledger(
                    run.packet_lifecycle_ledger, "packet_lifecycle_ledger"
                ),
                ai_candidate_lifecycle_ledger=_safe_observability_ledger(
                    run.ai_candidate_lifecycle_ledger, "ai_candidate_lifecycle_ledger"
                ),
                rule_metrics=_safe_observability_json(run.rule_metrics, "rule_metrics", {}),
            )
            self.session.add(record)
            self.session.flush()

            self.session.add_all(
                [
                    _rule_result_row(item, position, record.id, run_id)
                    for position, item in enumerate(run.rule_results)
                ]
            )
            self.session.add_all(
                [
                    _finding_row(item, position, record.id, run_id)
                    for position, item in enumerate(run.findings)
                ]
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(record)
        return run_id

    def create_running_run(self, case_id: str, run_id: str) -> str:
        """Create the durable parent before a background worker is scheduled."""
        _safe_identifier(case_id, "case_id")
        run_id = normalize_review_run_id(run_id)
        try:
            case = self.session.get(CaseORM, case_id)
            if case is None or self.session.get(RecycleBinORM, case_id) is not None:
                raise ValueError("case does not exist")
            if self.session.scalar(select(ReviewRunORM.id).where(ReviewRunORM.run_id == run_id)):
                raise ValueError("run_id already exists")
            self.session.add(
                ReviewRunORM(
                    run_id=run_id,
                    case=case,
                    final_status="RUNNING",
                    facts=[],
                    stage_records=[],
                    evidence_text_hashes={},
                    evidence_locations={},
                    llm_status=LLMStatus.NOT_RUN.value,
                    llm_finding_count=0,
                )
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return run_id

    def claim_running_run(self, run_id: str, worker_token: str) -> bool:
        run_id = normalize_review_run_id(run_id)
        worker_token = normalize_review_run_id(worker_token)
        try:
            result = self.session.execute(
                update(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == run_id,
                    ReviewRunORM.final_status == "RUNNING",
                    ReviewRunORM.worker_token.is_(None),
                )
                .values(worker_token=worker_token)
            )
            self.session.commit()
            return result.rowcount == 1
        except Exception:
            self.session.rollback()
            raise

    def finish_running_run(self, run: ReviewRun, worker_token: str) -> str:
        """CAS the claimed RUNNING placeholder to the immutable pipeline result.

        Core findings and rule results remain fail-closed. Observability fields
        are bounded best-effort metadata and must never prevent this terminal
        transition once the pipeline has produced its result.
        """
        _validate_run_payload(run, tolerate_observability=True)
        run_id = normalize_review_run_id(run.run_id)
        worker_token = normalize_review_run_id(worker_token)
        try:
            record = self.session.scalar(
                select(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == run_id,
                    ReviewRunORM.final_status == "RUNNING",
                    ReviewRunORM.worker_token == worker_token,
                )
                .options(selectinload(ReviewRunORM.rule_results), selectinload(ReviewRunORM.findings))
            )
            if record is None:
                terminal_record = self.session.scalar(
                    select(ReviewRunORM)
                    .where(
                        ReviewRunORM.run_id == run_id,
                        ReviewRunORM.worker_token == worker_token,
                    )
                    .options(
                        selectinload(ReviewRunORM.rule_results),
                        selectinload(ReviewRunORM.findings),
                    )
                )
                if (
                    terminal_record is not None
                    and terminal_record.case_id == run.case_id
                    and _terminal_payload_matches(terminal_record, run)
                ):
                    return run_id
                raise ValueError("background run is not owned by this worker")
            if record.case_id != run.case_id:
                raise ValueError("background run is not owned by this worker")
            record.final_status = run.final_status
            record.facts = _sanitize_facts(run.facts)
            record.stage_records = _safe_observability_json(
                _models_to_dict(run.stage_records), "stage_records", []
            )
            record.evidence_text_hashes = _sanitize_evidence_text_hashes(run.evidence_text_hashes)
            record.evidence_locations = _sanitize_evidence_locations(run.evidence_locations)
            record.llm_provider = _safe_vocabulary(run.llm_provider, "llm_provider", optional=True)
            record.llm_model = _safe_vocabulary(run.llm_model, "llm_model", optional=True)
            record.llm_status = LLMStatus(run.llm_status).value
            record.llm_finding_count = _safe_nonnegative_int(run.llm_finding_count, "llm_finding_count")
            record.llm_error_summary = _safe_text(run.llm_error_summary, "llm_error_summary")
            record.validation_reason_code = _safe_reason_code(run.validation_reason_code)
            record.candidate_count = _safe_optional_nonnegative_int(run.candidate_count, "candidate_count")
            record.valid_count = _safe_optional_nonnegative_int(run.valid_count, "valid_count")
            record.rejected_count = _safe_optional_nonnegative_int(run.rejected_count, "rejected_count")
            record.available_span_count = _safe_optional_nonnegative_int(run.available_span_count, "available_span_count")
            record.selected_span_count = _safe_optional_nonnegative_int(run.selected_span_count, "selected_span_count")
            record.selected_character_count = _safe_optional_nonnegative_int(run.selected_character_count, "selected_character_count")
            record.coverage_ratio = _safe_optional_coverage_ratio(run.coverage_ratio)
            record.git_sha = _safe_identifier(run.git_sha, "git_sha", optional=True)
            record.prompt_version = _safe_identifier(run.prompt_version, "prompt_version", optional=True)
            record.evidence_selector_version = _safe_identifier(run.evidence_selector_version, "evidence_selector_version", optional=True)
            record.max_tokens = _safe_optional_nonnegative_int(run.max_tokens, "max_tokens")
            record.timeout = _safe_optional_nonnegative_float(run.timeout, "timeout")
            record.temperature = _safe_optional_nonnegative_float(run.temperature, "temperature")
            record.batch_count = _safe_nonnegative_int(run.batch_count, "batch_count")
            record.batch_metrics = _safe_observability_json(
                run.batch_metrics, "batch_metrics", []
            )
            record.premerge_finding_count = _safe_nonnegative_int(run.premerge_finding_count, "premerge_finding_count")
            record.postmerge_finding_count = _safe_nonnegative_int(run.postmerge_finding_count, "postmerge_finding_count")
            record.deduplicated_finding_count = _safe_nonnegative_int(run.deduplicated_finding_count, "deduplicated_finding_count")
            record.stop_reason = _safe_vocabulary(run.stop_reason, "stop_reason", optional=True)
            record.ai_guard_rejections = _safe_observability_json(
                run.ai_guard_rejections, "ai_guard_rejections", []
            )
            record.deduplication_records = _safe_observability_json(
                run.deduplication_records, "deduplication_records", []
            )
            record.packet_lifecycle_ledger = _safe_observability_ledger(
                run.packet_lifecycle_ledger, "packet_lifecycle_ledger"
            )
            record.ai_candidate_lifecycle_ledger = _safe_observability_ledger(
                run.ai_candidate_lifecycle_ledger, "ai_candidate_lifecycle_ledger"
            )
            record.rule_metrics = _safe_observability_json(run.rule_metrics, "rule_metrics", {})
            # Batch checkpoints may already contain partial LLM findings.
            # Replace them with the authoritative reconciled result.
            record.rule_results.clear()
            record.findings.clear()
            # Execute delete-orphan removals before inserting rows whose stable
            # finding IDs may already exist in a batch checkpoint. This remains
            # one transaction, so a later failure rolls everything back.
            self.session.flush()
            record.rule_results.extend(
                _rule_result_row(item, position, record.id, run_id)
                for position, item in enumerate(run.rule_results)
            )
            record.findings.extend(
                _finding_row(item, position, record.id, run_id)
                for position, item in enumerate(run.findings)
            )
            self.session.commit()
            return run_id
        except Exception:
            self.session.rollback()
            raise

    def checkpoint_running_run(
        self,
        run: ReviewRun,
        worker_token: str,
        findings: list[Finding],
    ) -> None:
        """Persist each validated AI batch without finalizing the running Run."""
        run_id = normalize_review_run_id(run.run_id)
        worker_token = normalize_review_run_id(worker_token)
        try:
            record = self.session.scalar(
                select(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == run_id,
                    ReviewRunORM.final_status == "RUNNING",
                    ReviewRunORM.worker_token == worker_token,
                )
                .options(selectinload(ReviewRunORM.findings))
            )
            if record is None or record.case_id != run.case_id:
                raise ValueError("background run is not owned by this worker")
            record.llm_status = LLMStatus(run.llm_status).value
            record.llm_finding_count = _safe_nonnegative_int(
                run.llm_finding_count, "llm_finding_count"
            )
            record.candidate_count = _safe_optional_nonnegative_int(
                run.candidate_count, "candidate_count"
            )
            record.valid_count = _safe_optional_nonnegative_int(
                run.valid_count, "valid_count"
            )
            record.rejected_count = _safe_optional_nonnegative_int(
                run.rejected_count, "rejected_count"
            )
            record.available_span_count = _safe_optional_nonnegative_int(
                run.available_span_count, "available_span_count"
            )
            record.selected_span_count = _safe_optional_nonnegative_int(
                run.selected_span_count, "selected_span_count"
            )
            record.selected_character_count = _safe_optional_nonnegative_int(
                run.selected_character_count, "selected_character_count"
            )
            record.coverage_ratio = _safe_optional_coverage_ratio(run.coverage_ratio)
            record.evidence_selector_version = _safe_identifier(
                run.evidence_selector_version, "evidence_selector_version", optional=True
            )
            record.batch_count = _safe_nonnegative_int(run.batch_count, "batch_count")
            record.batch_metrics = _safe_observability_json(
                run.batch_metrics, "batch_metrics", []
            )
            record.packet_lifecycle_ledger = _safe_observability_ledger(
                run.packet_lifecycle_ledger, "packet_lifecycle_ledger"
            )
            record.ai_candidate_lifecycle_ledger = _safe_observability_ledger(
                run.ai_candidate_lifecycle_ledger, "ai_candidate_lifecycle_ledger"
            )
            record.rule_metrics = _safe_observability_json(run.rule_metrics, "rule_metrics", {})
            record.stop_reason = _safe_vocabulary(
                run.stop_reason, "stop_reason", optional=True
            )
            record.findings.clear()
            # Checkpoints replace the cumulative validated snapshot. Flush
            # deletions first so stable finding IDs can be reused safely.
            self.session.flush()
            record.findings.extend(
                _finding_row(item, position, record.id, run_id)
                for position, item in enumerate(findings)
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def set_running_run_failed(self, run_id: str, worker_token: str) -> None:
        run_id = normalize_review_run_id(run_id)
        worker_token = normalize_review_run_id(worker_token)
        try:
            self.session.execute(
                update(ReviewRunORM)
                .where(
                    ReviewRunORM.run_id == run_id,
                    ReviewRunORM.final_status == "RUNNING",
                    ReviewRunORM.worker_token == worker_token,
                )
                .values(final_status="FAILED", llm_error_summary="审查任务执行失败")
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def append_progress_event(
        self,
        run_id: str,
        stage: str,
        event_type: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        """Allocate sequence and insert under one SQLite write transaction."""
        run_id = normalize_review_run_id(run_id)
        stage, event_type, status, message, safe_details = safe_progress_payload(
            stage, event_type, status, message, details
        )
        engine = self.session.get_bind()
        last_error: Exception | None = None
        for _attempt in range(3):
            connection = engine.connect()
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                exists = connection.execute(
                    select(ReviewRunORM.run_id).where(ReviewRunORM.run_id == run_id)
                ).scalar_one_or_none()
                if exists is None:
                    raise ValueError("run does not exist")
                sequence = int(
                    connection.execute(
                        select(func.coalesce(func.max(ReviewProgressEventORM.sequence), 0)).where(
                            ReviewProgressEventORM.run_id == run_id
                        )
                    ).scalar_one()
                ) + 1
                created_at = utc_now()
                connection.execute(
                    ReviewProgressEventORM.__table__.insert().values(
                        run_id=run_id,
                        sequence=sequence,
                        stage=stage,
                        event_type=event_type,
                        status=status,
                        message=message,
                        details_json=safe_details,
                        created_at=created_at,
                    )
                )
                connection.commit()
                return ProgressEvent(
                    sequence, stage, event_type, status, message, safe_details, created_at
                )
            except IntegrityError as exc:
                connection.rollback()
                last_error = exc
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        raise RuntimeError("progress sequence allocation failed after retries") from last_error

    def list_progress_events(self, run_id: str, after_sequence: int = 0) -> list[ProgressEvent]:
        run_id = normalize_review_run_id(run_id)
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        rows = self.session.scalars(
            select(ReviewProgressEventORM)
            .where(
                ReviewProgressEventORM.run_id == run_id,
                ReviewProgressEventORM.sequence > after_sequence,
            )
            .order_by(ReviewProgressEventORM.sequence.asc())
        ).all()
        return [
            ProgressEvent(
                row.sequence, row.stage, row.event_type, row.status, row.message,
                dict(row.details_json), row.created_at
            )
            for row in rows
        ]

    def last_progress_sequence(self, run_id: str) -> int:
        run_id = normalize_review_run_id(run_id)
        return int(
            self.session.scalar(
                select(func.coalesce(func.max(ReviewProgressEventORM.sequence), 0)).where(
                    ReviewProgressEventORM.run_id == run_id
                )
            )
            or 0
        )

    def interrupt_orphaned_runs(self) -> int:
        """Atomically close RUNNING jobs left behind by a prior process."""
        engine = self.session.get_bind()
        connection = engine.connect()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            run_ids = list(
                connection.execute(
                    select(ReviewRunORM.run_id).where(ReviewRunORM.final_status == "RUNNING")
                ).scalars()
            )
            now = utc_now()
            for run_id in run_ids:
                sequence = int(
                    connection.execute(
                        select(func.coalesce(func.max(ReviewProgressEventORM.sequence), 0)).where(
                            ReviewProgressEventORM.run_id == run_id
                        )
                    ).scalar_one()
                ) + 1
                connection.execute(
                    ReviewProgressEventORM.__table__.insert().values(
                        run_id=run_id,
                        sequence=sequence,
                        stage="FAILED",
                        event_type="TASK_INTERRUPTED",
                        status="failed",
                        message="上次审查因应用中断未完成，请重新运行。",
                        details_json={},
                        created_at=now,
                    )
                )
                connection.execute(
                    update(ReviewRunORM)
                    .where(ReviewRunORM.run_id == run_id, ReviewRunORM.final_status == "RUNNING")
                    .values(final_status="INTERRUPTED", llm_error_summary="审查任务因应用中断未完成")
                )
            connection.commit()
            return len(run_ids)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_case(self, case_id: str) -> CaseRecord | None:
        """Hydrate active case metadata without exposing a recycled case."""
        _safe_identifier(case_id, "case_id")
        record = self.session.scalar(
            select(CaseORM)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == CaseORM.case_id)
            .where(CaseORM.case_id == case_id, RecycleBinORM.case_id.is_(None))
            .options(selectinload(CaseORM.files))
        )
        if record is None:
            return None
        return CaseRecord(
            case_id=record.case_id,
            files=[
                StoredFile(
                    storage_relative_path=item.storage_relative_path,
                    sha256=item.sha256,
                    size=item.size,
                    safe_name=item.safe_name,
                )
                for item in record.files
            ],
            statistics=dict(record.statistics),
        )

    def get_run(self, run_id: str) -> ReviewRun | None:
        """Hydrate one active review run by its external UUID."""
        run_id = normalize_review_run_id(run_id)
        record = self.session.scalar(
            select(ReviewRunORM)
            .join(CaseORM)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == CaseORM.case_id)
            .where(ReviewRunORM.run_id == run_id, RecycleBinORM.case_id.is_(None))
            .options(
                selectinload(ReviewRunORM.rule_results),
                selectinload(ReviewRunORM.findings),
            )
        )
        return None if record is None else _to_review_run(record)

    def get_run_for_case(self, case_id: str, run_id: str) -> ReviewRun | None:
        _safe_identifier(case_id, "case_id")
        run = self.get_run(run_id)
        return run if run is not None and run.case_id == case_id else None

    def list_runs(self, case_id: str) -> list[ReviewRun]:
        _safe_identifier(case_id, "case_id")
        records = self.session.scalars(
            select(ReviewRunORM)
            .join(CaseORM)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == CaseORM.case_id)
            .where(ReviewRunORM.case_id == case_id, RecycleBinORM.case_id.is_(None))
            .options(selectinload(ReviewRunORM.rule_results), selectinload(ReviewRunORM.findings))
            .order_by(ReviewRunORM.created_at.desc(), ReviewRunORM.id.desc())
        ).all()
        return [_to_review_run(record) for record in records]

    def get_latest_run(self, case_id: str) -> ReviewRun | None:
        runs = self.list_runs(case_id)
        return runs[0] if runs else None

    def get_latest_successful_run(self, case_id: str) -> ReviewRun | None:
        _safe_identifier(case_id, "case_id")
        record = self.session.scalar(
            select(ReviewRunORM)
            .join(CaseORM)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == CaseORM.case_id)
            .where(
                ReviewRunORM.case_id == case_id,
                ReviewRunORM.final_status == "READY_FOR_HUMAN_REVIEW",
                RecycleBinORM.case_id.is_(None),
            )
            .options(selectinload(ReviewRunORM.rule_results), selectinload(ReviewRunORM.findings))
            .order_by(ReviewRunORM.created_at.desc(), ReviewRunORM.id.desc())
        )
        return None if record is None else _to_review_run(record)

    def update_finding_review(
        self,
        case_id: str,
        run_id: str,
        finding_id: str,
        status: ReviewStatus,
        note: str | None,
        is_expert_experience: bool | None = None,
    ) -> ExpertExperienceSummary:
        """Persist a human-review decision; records cannot be updated from the bin."""
        _safe_identifier(case_id, "case_id")
        run_id = normalize_review_run_id(run_id)
        _safe_identifier(finding_id, "finding_id")
        if not isinstance(status, ReviewStatus):
            status = ReviewStatus(status)
        try:
            finding = self.session.scalar(
                select(FindingORM)
                .join(ReviewRunORM)
                .outerjoin(RecycleBinORM, RecycleBinORM.case_id == ReviewRunORM.case_id)
                .where(
                    FindingORM.finding_id == finding_id,
                    ReviewRunORM.case_id == case_id,
                    ReviewRunORM.run_id == run_id,
                    RecycleBinORM.case_id.is_(None),
                )
            )
            if finding is None:
                raise KeyError(f"finding not found: {finding_id}")
            now = datetime.now(timezone.utc)
            finding.review_status = status.value
            finding.human_note = _sanitize_note(note)
            finding.reviewed_at = now

            requested = finding.is_expert_experience if is_expert_experience is None else bool(is_expert_experience)
            # A pending review is never an effective experience, even when an
            # older client omits the experience field or sends a stale checkbox.
            effective = requested and status is not ReviewStatus.PENDING
            if effective:
                if not finding.is_expert_experience or finding.experience_saved_at is None:
                    finding.experience_saved_at = now
                finding.is_expert_experience = True
                finding.experience_updated_at = now
            elif finding.is_expert_experience:
                finding.is_expert_experience = False
                finding.experience_updated_at = now
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(finding)
        return self.get_expert_experience_summary()

    def get_expert_experience_summary(self) -> ExpertExperienceSummary:
        """Return the live count; no cache or process-local counter is used."""
        predicate = (
            FindingORM.is_expert_experience.is_(True),
            FindingORM.review_status != ReviewStatus.PENDING.value,
        )
        total_count, updated_at = self.session.execute(
            select(
                func.count(FindingORM.id),
                func.max(FindingORM.experience_updated_at),
            ).where(*predicate)
        ).one()
        return ExpertExperienceSummary(total_count=int(total_count), updated_at=updated_at)

    def delete_case_to_recycle_bin(self, case_id: str) -> None:
        """Hide a case from active queries while retaining it for confirmed deletion."""
        _safe_identifier(case_id, "case_id")
        try:
            if self.session.get(CaseORM, case_id) is None:
                raise KeyError(f"case not found: {case_id}")
            if self.session.get(RecycleBinORM, case_id) is None:
                self.session.add(RecycleBinORM(case_id=case_id))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def case_file_paths(self, case_id: str) -> list[str]:
        """Return only this case's persisted relative file paths, including recycled cases."""
        _safe_identifier(case_id, "case_id")
        case = self.session.scalar(
            select(CaseORM).where(CaseORM.case_id == case_id).options(selectinload(CaseORM.files))
        )
        return [] if case is None else [item.storage_relative_path for item in case.files]

    def permanently_delete_case(self, case_id: str, confirmation: str) -> None:
        """Delete only a recycled case after the explicit, case-bound confirmation."""
        _safe_identifier(case_id, "case_id")
        try:
            if confirmation != f"DELETE {case_id}":
                raise ValueError("confirmation must equal 'DELETE {case_id}'")
            recycle_entry = self.session.get(RecycleBinORM, case_id)
            if recycle_entry is None:
                raise ValueError("case must be in recycle bin before permanent deletion")
            case = self.session.get(CaseORM, case_id)
            if case is not None:
                self.session.delete(case)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def recycle_bin_case_ids(self) -> list[str]:
        """Return database-backed recycle-bin IDs for administrative confirmation."""
        return list(
            self.session.scalars(select(RecycleBinORM.case_id).order_by(RecycleBinORM.case_id))
        )

    def append_file_operation_audit(self, event) -> None:
        """Insert one immutable audit event; no update/delete API is exposed."""
        try:
            self.session.add(
                FileOperationAuditORM(
                    event_id=normalize_review_run_id(event.event_id),
                    case_id=_safe_identifier(event.case_id, "case_id"),
                    operation=_safe_identifier(event.operation, "audit operation"),
                    stage=_safe_identifier(event.stage, "audit stage"),
                    result=_safe_identifier(event.result, "audit result"),
                    created_at=datetime.fromisoformat(event.created_at),
                    summary=_safe_text(event.summary, "audit summary"),
                    recovery_required=bool(event.recovery_required),
                )
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _validate_case_files(files: list[StoredFile]) -> None:
        for item in files:
            safe_name = item.safe_name
            if (
                not isinstance(safe_name, str)
                or len(safe_name) > 255
                or not safe_name.casefold().endswith(".docx")
                or safe_name in {".", ".."}
                or "/" in safe_name
                or "\\" in safe_name
                or ".." in PureWindowsPath(safe_name).parts
                or PureWindowsPath(safe_name).is_absolute()
                or PureWindowsPath(safe_name).root
                or PureWindowsPath(safe_name).drive
                or any(ord(char) < 32 for char in safe_name)
                or safe_name.startswith(".")
            ):
                raise ValueError("safe_name must be a portable .docx basename")
            path = item.storage_relative_path
            native = Path(path)
            windows = PureWindowsPath(path)
            posix = PurePosixPath(path)
            normalized = posix.as_posix()
            if (
                not path
                or "\\" in path
                or normalized != path
                or normalized.startswith("/")
                or native.is_absolute()
                or windows.is_absolute()
                or windows.root
                or windows.drive
                or ".." in native.parts
                or ".." in windows.parts
                or ".." in posix.parts
            ):
                raise ValueError("storage_relative_path must be a normalized relative POSIX path")
            if len(item.sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in item.sha256):
                raise ValueError("sha256 must be a 64-character hexadecimal digest")


def _validate_run_payload(
    run: ReviewRun, *, tolerate_observability: bool = False
) -> None:
    run_id = normalize_review_run_id(run.run_id)
    _sanitize_facts(run.facts)
    _sanitize_evidence_text_hashes(run.evidence_text_hashes)
    _sanitize_evidence_locations(run.evidence_locations)
    _safe_vocabulary(run.llm_provider, "llm_provider", optional=True)
    _safe_vocabulary(run.llm_model, "llm_model", optional=True)
    LLMStatus(run.llm_status)
    _safe_nonnegative_int(run.llm_finding_count, "llm_finding_count")
    _safe_text(run.llm_error_summary, "llm_error_summary")
    _safe_reason_code(run.validation_reason_code)
    _safe_optional_nonnegative_int(run.candidate_count, "candidate_count")
    _safe_optional_nonnegative_int(run.valid_count, "valid_count")
    _safe_optional_nonnegative_int(run.rejected_count, "rejected_count")
    _safe_optional_nonnegative_int(run.available_span_count, "available_span_count")
    _safe_optional_nonnegative_int(run.selected_span_count, "selected_span_count")
    _safe_optional_nonnegative_int(run.selected_character_count, "selected_character_count")
    _safe_optional_coverage_ratio(run.coverage_ratio)
    _safe_identifier(run.git_sha, "git_sha", optional=True)
    _safe_identifier(run.prompt_version, "prompt_version", optional=True)
    _safe_identifier(run.evidence_selector_version, "evidence_selector_version", optional=True)
    _safe_optional_nonnegative_int(run.max_tokens, "max_tokens")
    _safe_optional_nonnegative_float(run.timeout, "timeout")
    _safe_optional_nonnegative_float(run.temperature, "temperature")
    _safe_nonnegative_int(run.batch_count, "batch_count")
    _safe_nonnegative_int(run.premerge_finding_count, "premerge_finding_count")
    _safe_nonnegative_int(run.postmerge_finding_count, "postmerge_finding_count")
    _safe_nonnegative_int(run.deduplicated_finding_count, "deduplicated_finding_count")
    _safe_vocabulary(run.stop_reason, "stop_reason", optional=True)
    if not tolerate_observability:
        _sanitize_json(_models_to_dict(run.stage_records))
        _sanitize_json(run.batch_metrics)
        _sanitize_json(run.ai_guard_rejections)
        _sanitize_json(run.deduplication_records)
        _sanitize_ledger(run.packet_lifecycle_ledger, "packet_lifecycle_ledger")
        _sanitize_ledger(run.ai_candidate_lifecycle_ledger, "ai_candidate_lifecycle_ledger")
        _sanitize_json(run.rule_metrics)
    for result in run.rule_results:
        _rule_result_row(result, 0, 0, run_id)
    for finding in run.findings:
        _finding_row(finding, 0, 0, run_id)


def _rule_result_row(
    result: RuleResult, position: int, review_run_id: int, run_id: str
) -> RuleResultORM:
    if result.run_id is not None and normalize_review_run_id(result.run_id) != run_id:
        raise ValueError("rule result belongs to a different run")
    return RuleResultORM(
        review_run_id=review_run_id,
        position=position,
        rule_id=_safe_identifier(result.rule_id, "rule_id"),
        rule_version=_safe_identifier(result.rule_version, "rule_version", optional=True),
        status=result.status.value,
        severity=result.severity.value,
        category=result.category.value,
        parameter=_safe_optional_vocabulary(result.parameter, "rule parameter"),
        message=_safe_text(result.message, "rule result message"),
        evidence_span_ids=_safe_identifier_list(result.evidence_span_ids, "evidence_span_ids"),
        involved_fact_ids=_safe_identifier_list(result.involved_fact_ids, "involved_fact_ids"),
        needs_human_review=result.needs_human_review,
        details=_sanitize_json(result.details),
    )


def _finding_row(
    finding: Finding, position: int, review_run_id: int, run_id: str
) -> FindingORM:
    if finding.run_id is not None and normalize_review_run_id(finding.run_id) != run_id:
        raise ValueError("finding belongs to a different run")
    return FindingORM(
        review_run_id=review_run_id,
        position=position,
        finding_id=_safe_identifier(finding.finding_id, "finding_id"),
        origin=finding.origin.value,
        category=finding.category.value,
        severity=finding.severity.value,
        parameter=_safe_optional_vocabulary(finding.parameter, "finding parameter"),
        title=_safe_finding_text(finding.title, "finding title"),
        description=_safe_finding_text(finding.description, "finding description"),
        suggestion=_safe_finding_text(finding.suggestion, "finding suggestion"),
        rule_id=_safe_identifier(finding.rule_id, "finding rule_id", optional=True),
        evidence_span_ids=_safe_identifier_list(finding.evidence_span_ids, "evidence_span_ids"),
        needs_human_review=finding.needs_human_review,
        review_status=finding.review_status.value,
        human_note=_sanitize_note(finding.human_note),
        reviewed_at=finding.reviewed_at,
        is_expert_experience=bool(finding.is_expert_experience),
        experience_saved_at=finding.experience_saved_at,
        experience_updated_at=finding.experience_updated_at,
        ai_snapshot=_sanitize_json(finding.original_ai_snapshot),
    )


def _fact_from_row(row: dict[str, Any]) -> ParameterFact:
    return ParameterFact.model_validate(row)


def _to_rule_result(row: RuleResultORM, run_id: str) -> RuleResult:
    return RuleResult(
        run_id=run_id,
        rule_id=row.rule_id,
        rule_version=row.rule_version,
        status=RuleStatus(row.status),
        severity=Severity(row.severity),
        category=row.category,
        parameter=row.parameter,
        message=row.message,
        evidence_span_ids=list(row.evidence_span_ids),
        involved_fact_ids=list(row.involved_fact_ids),
        needs_human_review=row.needs_human_review,
        details=dict(row.details),
    )


def _to_finding(row: FindingORM, run_id: str) -> Finding:
    return Finding(
        run_id=run_id,
        finding_id=row.finding_id,
        origin=Origin(row.origin),
        category=row.category,
        severity=Severity(row.severity),
        parameter=row.parameter,
        title=row.title,
        description=row.description,
        suggestion=row.suggestion,
        rule_id=row.rule_id,
        evidence_span_ids=list(row.evidence_span_ids),
        needs_human_review=row.needs_human_review,
        review_status=ReviewStatus(row.review_status),
        human_note=row.human_note,
        reviewed_at=row.reviewed_at,
        is_expert_experience=bool(row.is_expert_experience),
        experience_saved_at=row.experience_saved_at,
        experience_updated_at=row.experience_updated_at,
        original_ai_snapshot=dict(row.ai_snapshot),
    )


def _to_review_run(record: ReviewRunORM) -> ReviewRun:
    return ReviewRun(
        case_id=record.case_id,
        run_id=record.run_id,
        created_at=record.created_at,
        facts=[_fact_from_row(item) for item in record.facts],
        rule_results=[
            _to_rule_result(item, record.run_id)
            for item in sorted(record.rule_results, key=lambda row: row.position)
        ],
        findings=[
            _to_finding(item, record.run_id)
            for item in sorted(record.findings, key=lambda row: row.position)
        ],
        stage_records=[StageRecord.model_validate(item) for item in record.stage_records],
        final_status=record.final_status,
        evidence_text_hashes=dict(record.evidence_text_hashes),
        evidence_locations=dict(record.evidence_locations or {}),
        llm_provider=record.llm_provider,
        llm_model=record.llm_model,
        llm_status=LLMStatus(record.llm_status),
        llm_finding_count=record.llm_finding_count,
        llm_error_summary=record.llm_error_summary,
        llm_review_error=record.llm_error_summary,
        validation_reason_code=record.validation_reason_code,
        candidate_count=record.candidate_count,
        valid_count=record.valid_count,
        rejected_count=record.rejected_count,
        available_span_count=record.available_span_count,
        selected_span_count=record.selected_span_count,
        selected_character_count=record.selected_character_count,
        coverage_ratio=record.coverage_ratio,
        git_sha=record.git_sha,
        prompt_version=record.prompt_version,
        evidence_selector_version=record.evidence_selector_version,
        max_tokens=record.max_tokens,
        timeout=record.timeout,
        temperature=record.temperature,
        batch_count=record.batch_count,
        batch_metrics=list(record.batch_metrics or []),
        premerge_finding_count=record.premerge_finding_count,
        postmerge_finding_count=record.postmerge_finding_count,
        deduplicated_finding_count=record.deduplicated_finding_count,
        stop_reason=record.stop_reason,
        ai_guard_rejections=list(record.ai_guard_rejections or []),
        deduplication_records=list(record.deduplication_records or []),
        packet_lifecycle_ledger=dict(record.packet_lifecycle_ledger or {}),
        ai_candidate_lifecycle_ledger=dict(record.ai_candidate_lifecycle_ledger or {}),
        rule_metrics=dict(record.rule_metrics or {}),
    )


def _safe_optional_vocabulary(value: Any, field_name: str) -> str | None:
    """Normalize an omitted optional vocabulary value without weakening checks."""
    if isinstance(value, str) and not value.strip():
        return None
    return _safe_vocabulary(value, field_name, optional=True)


def _terminal_payload_matches(record: ReviewRunORM, run: ReviewRun) -> bool:
    """Return whether a repeated terminal callback carries the same payload."""
    if record.final_status != run.final_status:
        return False

    expected_rules = []
    for item in run.rule_results:
        payload = item.model_dump(mode="json", exclude={"run_id"})
        if isinstance(payload.get("parameter"), str) and not payload["parameter"].strip():
            payload["parameter"] = None
        expected_rules.append(payload)
    actual_rules = [
        _to_rule_result(item, record.run_id).model_dump(mode="json", exclude={"run_id"})
        for item in sorted(record.rule_results, key=lambda row: row.position)
    ]
    if actual_rules != expected_rules:
        return False

    pipeline_fields = {
        "finding_id",
        "origin",
        "category",
        "severity",
        "parameter",
        "title",
        "description",
        "suggestion",
        "rule_id",
        "evidence_span_ids",
        "needs_human_review",
        "original_ai_snapshot",
    }
    expected_findings = []
    for item in run.findings:
        payload = item.model_dump(mode="json", include=pipeline_fields)
        if isinstance(payload.get("parameter"), str) and not payload["parameter"].strip():
            payload["parameter"] = None
        expected_findings.append(payload)
    actual_findings = [
        _to_finding(item, record.run_id).model_dump(mode="json", include=pipeline_fields)
        for item in sorted(record.findings, key=lambda row: row.position)
    ]
    return actual_findings == expected_findings


def _sanitize_evidence_text_hashes(values: dict[str, str]) -> dict[str, str]:
    if not isinstance(values, dict) or len(values) > 10_000:
        raise ValueError("evidence text hashes must be a bounded mapping")
    output: dict[str, str] = {}
    for span_id, text_hash in values.items():
        safe_span_id = _safe_identifier(span_id, "evidence span id")
        if not isinstance(text_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", text_hash):
            raise ValueError("evidence text hash must be a SHA-256 digest")
        output[safe_span_id] = text_hash
    return output


def _sanitize_evidence_locations(values: dict[str, str]) -> dict[str, str]:
    """Bounded span_id -> human-readable location (section / paragraph / cell)."""
    if not isinstance(values, dict) or len(values) > 20_000:
        raise ValueError("evidence locations must be a bounded mapping")
    output: dict[str, str] = {}
    for span_id, location in values.items():
        safe_span_id = _safe_identifier(span_id, "evidence span id")
        output[safe_span_id] = _safe_vocabulary(location, "evidence location") or ""
    return output


def _sanitize_facts(values: list[ParameterFact]) -> list[dict[str, Any]]:
    facts = _facts_to_dict(values)
    # Do not pass this field-aware structure through generic key filtering:
    # source_document is a safe metadata identifier, not document content.
    return facts


def _facts_to_dict(values: list[ParameterFact]) -> list[dict[str, Any]]:
    return [
        {
            "fact_id": _safe_identifier(value.fact_id, "fact_id"),
            "canonical_name": _safe_vocabulary(value.canonical_name, "canonical_name"),
            "raw_name": _safe_text(value.raw_name, "raw_name"),
            "raw_value": _safe_text(value.raw_value, "raw_value"),
            "normalized_value": value.normalized_value,
            "raw_unit": _safe_vocabulary(value.raw_unit, "raw_unit", optional=True),
            "canonical_unit": _safe_vocabulary(value.canonical_unit, "canonical_unit", optional=True),
            "unit_category": _safe_vocabulary(value.unit_category, "unit_category", optional=True),
            "subject": _safe_vocabulary(value.subject, "subject", optional=True),
            "time_scope": _safe_vocabulary(value.time_scope, "time_scope", optional=True),
            "statistical_scope": _safe_vocabulary(value.statistical_scope, "statistical_scope", optional=True),
            "condition": _safe_vocabulary(value.condition, "condition", optional=True),
            "source_document": _safe_source_document(value.source_document),
            "source_version": _safe_identifier(value.source_version, "source_version", optional=True),
            "source_span_id": _safe_identifier(value.source_span_id, "source_span_id"),
            "extraction_method": value.extraction_method.value,
            "merged_fact_ids": _safe_identifier_list(value.merged_fact_ids, "merged_fact_ids"),
            "merged_span_ids": _safe_identifier_list(value.merged_span_ids, "merged_span_ids"),
            "confidence": value.confidence,
            "human_status": value.human_status.value,
        }
        for value in values
    ]


def _models_to_dict(values: list[Any]) -> list[dict[str, Any]]:
    return [_to_plain_json(value) for value in values]


def _to_plain_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    return value


def _safe_source_document(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise ValueError("source_document must be bounded metadata")
    if any(ord(char) < 32 for char in value) or _contains_prohibited_content(value):
        raise ValueError("source_document must be safe metadata")
    if "/" in value or "\\" in value or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).root:
        raise ValueError("source_document must be a safe metadata identifier")
    return value


def _safe_identifier(value: str | None, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a bounded safe identifier")
    return value


def _safe_vocabulary(value: str | None, field_name: str, *, optional: bool = False) -> str | None:
    """Validate a domain-vocabulary field (canonical name, scope, unit, …).

    Unlike ``_safe_identifier`` these fields are legitimately non-ASCII human
    terms (``高峰产量``, ``达产期``) and units that legitimately contain a slash
    (``万m³/d`` = per day), so neither the ASCII-only identifier pattern nor a
    path-separator ban fits.  These values are stored only as JSON payload, never
    joined into a filesystem path, so the guard is: bounded length, no control
    characters, and no secret/body content.
    """
    if value is None and optional:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise ValueError(f"{field_name} must be bounded vocabulary")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field_name} must not contain control characters")
    if _contains_prohibited_content(value):
        raise ValueError(f"{field_name} must not contain secret or body content")
    return value


def _safe_identifier_list(values: list[str], field_name: str) -> list[str]:
    # A whole-document rule (required sections, evidence gate) or the Mock may
    # cite every span in the document as evidence, and a 300-page DOCX has far
    # more than 100 spans. The bound stays generous but finite to reject only
    # pathological input, not real documents.
    if not isinstance(values, list) or len(values) > _MAX_EVIDENCE_ITEMS:
        raise ValueError(f"{field_name} must be a bounded list")
    return [_safe_identifier(value, field_name) for value in values]


def _safe_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    if len(value) > 4_000:
        raise ValueError(f"{field_name} exceeds 4000 characters")
    if _contains_prohibited_content(value):
        raise ValueError(f"{field_name} contains forbidden secret or body content")
    return value


def _safe_finding_text(value: str | None, field_name: str) -> str | None:
    """Keep finding prose bounded while allowing legitimate multi-paragraph text."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    field = field_name.removeprefix("finding ")
    if field not in {"title", "description", "suggestion"}:
        raise ValueError("unknown finding text field")
    return validate_finding_text(value, field)


def _safe_nonnegative_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _safe_optional_nonnegative_int(value: int | None, field_name: str) -> int | None:
    return None if value is None else _safe_nonnegative_int(value, field_name)


def _safe_optional_nonnegative_float(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0:
        raise ValueError(f"{field_name} must be a non-negative number")
    return float(value)


def _safe_optional_coverage_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        raise ValueError("coverage_ratio must be between 0 and 1")
    return float(value)


def _safe_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in VALIDATION_REASON_CODES:
        raise ValueError("invalid validation_reason_code")
    return value


def _sanitize_note(note: str | None) -> str | None:
    """Allow normal expert prose while rejecting explicit credentials/dumps."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise TypeError("human note must be a string or None")
    if len(note) > 4_000:
        raise ValueError("human note exceeds 4000 characters")
    if _note_contains_sensitive_evidence(note):
        raise ValueError("human note contains sensitive credential evidence")
    return note


def _note_contains_sensitive_evidence(note: str) -> bool:
    if re.search(
        r"(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
        r"github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16})\b",
        note,
    ):
        return True
    if re.search(r"(?im)^\s*authorization\s*:\s*(?:bearer|basic)\s+\S+", note):
        return True
    if re.search(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+", note):
        return True
    if re.search(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", note):
        return True
    if re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", note):
        return True
    if re.search(
        r"(?is)^\s*(?:request|response)(?:\s+(?:body|payload)|_(?:body|payload))?\s*:\s*(?:\{|\[|(?:GET|POST|PUT|PATCH|DELETE)\s+|HTTP/)",
        note,
    ):
        return True
    return bool(re.search(r"(?im)^\s*(?:GET|POST|PUT|PATCH|DELETE)\s+\S+\s+HTTP/\d", note))


_NOTE_FORBIDDEN_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "token:",
    "token=",
    "secret:",
    "secret=",
    "password:",
    "password=",
    "request body",
    "request_body",
    "response body",
    "response_body",
    "document content",
    "document_content",
    "raw docx",
    "raw_docx",
    "full body",
    "full_body",
    "github_pat",
    "google ai",
    "jwt",
    "private key",
    "request payload",
    "response payload",
    "raw document",
    "document text",
    "document content",
    "raw docx",
    "原始 docx",
    "原始文本",
    "全文内容",
)


def _contains_prohibited_content(value: str, *, reject_body_shape: bool = True) -> bool:
    normalized = value.casefold()
    if any(marker in normalized for marker in _NOTE_FORBIDDEN_MARKERS):
        return True
    if re.search(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16})\b", value):
        return True
    if re.search(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", value):
        return True
    if re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
        return True
    if re.search(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+", value):
        return True
    if re.search(r"(?i)\bbearer\s+[A-Za-z0-9._-]{12,}", value):
        return True
    if reject_body_shape and _looks_like_full_body(value):
        return True
    return False


def _looks_like_full_body(value: str) -> bool:
    # Persisted prose must remain a bounded review summary, never a body dump.
    return value.count("\n") >= 3 or len(value) > 1_000 or (
        value.lstrip().startswith(("{", "[")) and len(value) > 160
    )


def sanitize_persistence_metadata(value: Any) -> Any:
    """Allow only bounded metadata and reject prohibited scalar content."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        return _safe_text(value, "metadata value")
    if isinstance(value, list | tuple):
        if len(value) > 100:
            raise ValueError("metadata list exceeds 100 items")
        return [sanitize_persistence_metadata(item) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("metadata object exceeds 100 fields")
        output = {}
        for key, item in value.items():
            raw_key = str(key)
            if _is_sensitive_key(raw_key):
                continue
            safe_key = _safe_text(raw_key, "metadata key")
            output[safe_key] = sanitize_persistence_metadata(item)
        return output
    raise TypeError(f"unsupported persistence metadata type: {type(value).__name__}")


def _sanitize_json(value: Any) -> Any:
    """Backward-compatible internal alias for structured metadata cleaning."""
    return sanitize_persistence_metadata(value)


def _sanitize_ledger(value: Any, label: str) -> dict[str, Any]:
    """Validate the bounded ledger envelope without changing its decisions."""
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    required = {
        "ledger_schema_version", "ledger_entry_count", "ledger_truncated",
        "ledger_size_bytes", "entries", "summary",
    }
    if not required.issubset(value):
        raise ValueError(f"{label} is missing required envelope fields")
    if not isinstance(value["ledger_entry_count"], int) or value["ledger_entry_count"] < 0:
        raise ValueError(f"{label} has invalid entry count")
    if not isinstance(value["ledger_size_bytes"], int) or value["ledger_size_bytes"] < 0:
        raise ValueError(f"{label} has invalid byte count")
    if not isinstance(value["ledger_truncated"], bool):
        raise ValueError(f"{label} has invalid truncation flag")
    if not isinstance(value["entries"], list) or not isinstance(value["summary"], dict):
        raise ValueError(f"{label} has invalid payload")
    # Generic persistence metadata intentionally caps arbitrary lists at 100
    # items. A lifecycle ledger is the explicit bounded exception: its own
    # configured cap is 10,000 entries, so sanitize each entry without applying
    # the generic list cap to the entries field itself.
    if len(value["entries"]) > DEFAULT_LEDGER_MAX_ENTRIES:
        raise ValueError(f"{label} exceeds its configured entry limit")
    if value["ledger_entry_count"] < len(value["entries"]):
        raise ValueError(f"{label} entry count is inconsistent")
    cleaned_entries = []
    for entry in value["entries"]:
        if not isinstance(entry, dict):
            raise TypeError(f"{label} entries must be objects")
        cleaned_entries.append(sanitize_persistence_metadata(entry))
    cleaned_summary = sanitize_persistence_metadata(value["summary"])
    if not isinstance(cleaned_summary, dict):
        raise TypeError(f"{label} summary must be an object")
    return {
        "ledger_schema_version": _safe_text(
            value["ledger_schema_version"], f"{label} schema"
        ) or LEDGER_SCHEMA_VERSION,
        "ledger_entry_count": value["ledger_entry_count"],
        "ledger_truncated": value["ledger_truncated"],
        "ledger_size_bytes": value["ledger_size_bytes"],
        "entries": cleaned_entries,
        "summary": cleaned_summary,
    }


def _empty_ledger_fallback(label: str) -> dict[str, Any]:
    """Return a safe observability fallback after a ledger-only failure."""
    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "ledger_entry_count": 0,
        "ledger_truncated": True,
        "ledger_size_bytes": 0,
        "entries": [],
        "summary": {f"warning:{label}": 1},
    }


def _safe_observability_json(value: Any, label: str, fallback: Any) -> Any:
    """Persist diagnostics without allowing them to change a terminal result."""
    try:
        return _sanitize_json(value)
    except Exception:
        LOGGER.warning("%s persistence failed; retaining a bounded fallback", label)
        return fallback


def _safe_observability_ledger(value: Any, label: str) -> dict[str, Any]:
    try:
        return _sanitize_ledger(value, label)
    except Exception:
        LOGGER.warning("%s persistence failed; retaining a truncated fallback", label)
        return _empty_ledger_fallback(label)


def _sanitize_statistics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("statistics must be an object")
    unknown = {str(key) for key in value} - _STATISTICS_ALLOWED_KEYS
    if unknown:
        raise ValueError("statistics contains unsupported fields")
    cleaned = sanitize_persistence_metadata(value)
    if not isinstance(cleaned, dict):
        raise TypeError("statistics must be an object")
    return cleaned


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_METADATA_KEYS or normalized.endswith(
        _SENSITIVE_METADATA_SUFFIXES
    )
