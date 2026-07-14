"""Repository for durable, deliberately limited review metadata."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import (
    BlockType,
    ExtractionMethod,
    Origin,
    PipelineStage,
    ReviewStatus,
    RuleStatus,
    Severity,
)
from app.domain.schemas import Finding, ParameterFact, RuleResult, SourceSpan, StageRecord
from app.persistence.models import (
    CaseFileORM,
    CaseORM,
    CaseRecord,
    FindingORM,
    RecycleBinORM,
    ReviewRunORM,
    RuleResultORM,
)
from app.review.pipeline import ReviewRun
from app.storage.case_files import StoredFile

_SECRET_OR_BODY_MARKERS = (
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
)


class ReviewRepository:
    """Persist review artefacts in SQLite without retaining source document bodies."""

    persisted_field_names = frozenset(
        {
            "case_id",
            "files",
            "sha256",
            "storage_relative_path",
            "statistics",
            "facts",
            "stage_records",
            "final_status",
            "rule_results",
            "findings",
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
        self._validate_case_files(case.files)
        safe_statistics = _sanitize_json(case.statistics)
        existing = self.session.get(CaseORM, case.case_id)
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
        self.session.refresh(existing)
        return existing.case_id

    def save_run(self, run: ReviewRun) -> str:
        """Replace a case's persisted run atomically with sanitized ORM rows."""
        if not isinstance(run, ReviewRun) or not run.case_id:
            raise ValueError("run.case_id is required")
        case = self.session.get(CaseORM, run.case_id)
        if case is None:
            case = CaseORM(case_id=run.case_id, statistics={})
            self.session.add(case)
        recycle_entry = self.session.get(RecycleBinORM, run.case_id)
        if recycle_entry is not None:
            self.session.delete(recycle_entry)

        record = self._active_run(run.case_id)
        if record is None:
            record = ReviewRunORM(
                case=case,
                final_status=run.final_status,
                facts=_sanitize_json(_models_to_dict(run.facts)),
                stage_records=_sanitize_json(_models_to_dict(run.stage_records)),
            )
            self.session.add(record)
        else:
            record.final_status = run.final_status
            record.facts = _sanitize_json(_models_to_dict(run.facts))
            record.stage_records = _sanitize_json(_models_to_dict(run.stage_records))
            record.rule_results.clear()
            record.findings.clear()

        record.rule_results.extend(
            _rule_result_row(item, position)
            for position, item in enumerate(run.rule_results)
        )
        record.findings.extend(
            _finding_row(item, position) for position, item in enumerate(run.findings)
        )
        self.session.commit()
        self.session.refresh(record)
        return run.case_id

    def get_run(self, run_id: str) -> ReviewRun | None:
        """Hydrate an active review run directly from the database."""
        record = self.session.scalar(
            select(ReviewRunORM)
            .join(CaseORM)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == CaseORM.case_id)
            .where(ReviewRunORM.case_id == run_id, RecycleBinORM.case_id.is_(None))
            .options(
                selectinload(ReviewRunORM.rule_results),
                selectinload(ReviewRunORM.findings),
            )
        )
        if record is None:
            return None
        return ReviewRun(
            case_id=record.case_id,
            facts=[ParameterFact.model_validate(item) for item in record.facts],
            rule_results=[_to_rule_result(item) for item in sorted(record.rule_results, key=lambda row: row.position)],
            findings=[_to_finding(item) for item in sorted(record.findings, key=lambda row: row.position)],
            stage_records=[StageRecord.model_validate(item) for item in record.stage_records],
            final_status=record.final_status,
        )

    def update_finding_review(
        self, finding_id: str, status: ReviewStatus, note: str | None
    ) -> None:
        """Persist a human-review decision; records cannot be updated from the bin."""
        if not isinstance(status, ReviewStatus):
            status = ReviewStatus(status)
        finding = self.session.scalar(
            select(FindingORM)
            .join(ReviewRunORM)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == ReviewRunORM.case_id)
            .where(FindingORM.finding_id == finding_id, RecycleBinORM.case_id.is_(None))
        )
        if finding is None:
            raise KeyError(f"finding not found: {finding_id}")
        finding.review_status = status.value
        finding.human_note = _sanitize_note(note)
        self.session.commit()
        self.session.refresh(finding)

    def delete_case_to_recycle_bin(self, case_id: str) -> None:
        """Hide a case from active queries while retaining it for confirmed deletion."""
        if self.session.get(CaseORM, case_id) is None:
            raise KeyError(f"case not found: {case_id}")
        if self.session.get(RecycleBinORM, case_id) is None:
            self.session.add(RecycleBinORM(case_id=case_id))
        self.session.commit()

    def permanently_delete_case(self, case_id: str, confirmation: str) -> None:
        """Delete only a recycled case after the explicit, case-bound confirmation."""
        if confirmation != f"DELETE {case_id}":
            raise ValueError("confirmation must equal 'DELETE {case_id}'")
        recycle_entry = self.session.get(RecycleBinORM, case_id)
        if recycle_entry is None:
            raise ValueError("case must be in recycle bin before permanent deletion")
        case = self.session.get(CaseORM, case_id)
        if case is not None:
            self.session.delete(case)
        self.session.commit()

    def recycle_bin_case_ids(self) -> list[str]:
        """Return database-backed recycle-bin IDs for administrative confirmation."""
        return list(
            self.session.scalars(select(RecycleBinORM.case_id).order_by(RecycleBinORM.case_id))
        )

    def _active_run(self, case_id: str) -> ReviewRunORM | None:
        return self.session.scalar(
            select(ReviewRunORM)
            .where(ReviewRunORM.case_id == case_id)
            .options(
                selectinload(ReviewRunORM.rule_results),
                selectinload(ReviewRunORM.findings),
            )
        )

    @staticmethod
    def _validate_case_files(files: list[StoredFile]) -> None:
        for item in files:
            path = item.storage_relative_path
            native = Path(path)
            windows = PureWindowsPath(path)
            if not path or native.is_absolute() or windows.is_absolute() or windows.drive or ".." in native.parts or ".." in windows.parts:
                raise ValueError("storage_relative_path must be a relative path")
            if len(item.sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in item.sha256):
                raise ValueError("sha256 must be a 64-character hexadecimal digest")


def _rule_result_row(result: RuleResult, position: int) -> RuleResultORM:
    return RuleResultORM(
        position=position,
        rule_id=result.rule_id,
        status=result.status.value,
        severity=result.severity.value,
        category=result.category,
        parameter=result.parameter,
        message=result.message,
        evidence_span_ids=list(result.evidence_span_ids),
        involved_fact_ids=list(result.involved_fact_ids),
        needs_human_review=result.needs_human_review,
        details=_sanitize_json(result.details),
    )


def _finding_row(finding: Finding, position: int) -> FindingORM:
    return FindingORM(
        position=position,
        finding_id=finding.finding_id,
        origin=finding.origin.value,
        category=finding.category,
        severity=finding.severity.value,
        parameter=finding.parameter,
        title=finding.title,
        description=finding.description,
        suggestion=finding.suggestion,
        rule_id=finding.rule_id,
        evidence_span_ids=list(finding.evidence_span_ids),
        needs_human_review=finding.needs_human_review,
        review_status=finding.review_status.value,
        human_note=_sanitize_note(finding.human_note),
        ai_snapshot=_sanitize_json(finding.original_ai_snapshot),
    )


def _to_rule_result(row: RuleResultORM) -> RuleResult:
    return RuleResult(
        rule_id=row.rule_id,
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


def _to_finding(row: FindingORM) -> Finding:
    return Finding(
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
        original_ai_snapshot=dict(row.ai_snapshot),
    )


def _models_to_dict(values: list[Any]) -> list[dict[str, Any]]:
    return [_to_plain_json(value) for value in values]


def _to_plain_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    return value


def _sanitize_note(note: str | None) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise TypeError("human note must be a string or None")
    if len(note) > 4_000:
        raise ValueError("human note exceeds 4000 characters")
    return note


def _sanitize_json(value: Any) -> Any:
    """Drop credential/body-bearing JSON keys and retain plain structured metadata."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_json(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    raise TypeError(f"unsupported persistence metadata type: {type(value).__name__}")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return any(marker in normalized for marker in _SECRET_OR_BODY_MARKERS)
