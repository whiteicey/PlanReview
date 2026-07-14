"""Repository for durable, deliberately limited review metadata."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
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

# Evidence span identifiers use colon-separated parser coordinates (for example,
# ``document:p:0``); they remain metadata identifiers and never file paths.
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

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
    "document_content",
    "document_text",
    "raw_content",
    "full_body",
    "request",
    "response",
    "document",
    "docx",
    "原始",
    "全文",
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
        _safe_identifier(case.case_id, "case_id")
        self._validate_case_files(case.files)
        safe_statistics = _sanitize_json(case.statistics)
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
        """Replace a case's persisted run atomically with sanitized ORM rows."""
        if not isinstance(run, ReviewRun) or not run.case_id:
            raise ValueError("run.case_id is required")
        _safe_identifier(run.case_id, "case_id")
        _safe_text(run.final_status, "final status")
        # Validate every payload before touching ORM state. The commit block below
        # still rolls back database errors; this preflight prevents a validation
        # exception from leaving pending replacement rows in the Session.
        _validate_run_payload(run)
        try:
            case = self.session.get(CaseORM, run.case_id)
            if case is None:
                case = CaseORM(case_id=run.case_id, statistics={})
                self.session.add(case)
            if self.session.get(RecycleBinORM, run.case_id) is not None:
                raise ValueError("cannot save a recycled case without explicit restore")

            record = self._active_run(run.case_id)
            if record is None:
                record = ReviewRunORM(
                    case=case,
                    final_status=run.final_status,
                    facts=_sanitize_facts(run.facts),
                    stage_records=_sanitize_json(_models_to_dict(run.stage_records)),
                )
                self.session.add(record)
                self.session.flush()
            else:
                record.final_status = run.final_status
                record.facts = _sanitize_facts(run.facts)
                record.stage_records = _sanitize_json(_models_to_dict(run.stage_records))
                # Delete children explicitly and flush before inserting replacements.
                self.session.query(RuleResultORM).filter(
                    RuleResultORM.review_run_id == record.id
                ).delete(synchronize_session=False)
                self.session.query(FindingORM).filter(
                    FindingORM.review_run_id == record.id
                ).delete(synchronize_session=False)
                for child in (*record.rule_results, *record.findings):
                    self.session.expunge(child)
                self.session.flush()

            self.session.add_all(
                [
                    _rule_result_row(item, position, record.id)
                    for position, item in enumerate(run.rule_results)
                ]
            )
            self.session.add_all(
                [
                    _finding_row(item, position, record.id)
                    for position, item in enumerate(run.findings)
                ]
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(record)
        return run.case_id

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
            facts=[_fact_from_row(item) for item in record.facts],
            rule_results=[_to_rule_result(item) for item in sorted(record.rule_results, key=lambda row: row.position)],
            findings=[_to_finding(item) for item in sorted(record.findings, key=lambda row: row.position)],
            stage_records=[StageRecord.model_validate(item) for item in record.stage_records],
            final_status=record.final_status,
        )

    def update_finding_review(
        self, case_id: str, finding_id: str, status: ReviewStatus, note: str | None
    ) -> None:
        """Persist a human-review decision; records cannot be updated from the bin."""
        _safe_identifier(case_id, "case_id")
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
                    RecycleBinORM.case_id.is_(None),
                )
            )
            if finding is None:
                raise KeyError(f"finding not found: {finding_id}")
            finding.review_status = status.value
            finding.human_note = _sanitize_note(note)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(finding)

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


def _validate_run_payload(run: ReviewRun) -> None:
    _sanitize_facts(run.facts)
    _sanitize_json(_models_to_dict(run.stage_records))
    for result in run.rule_results:
        _rule_result_row(result, 0, 0)
    for finding in run.findings:
        _finding_row(finding, 0, 0)


def _rule_result_row(result: RuleResult, position: int, review_run_id: int) -> RuleResultORM:
    return RuleResultORM(
        review_run_id=review_run_id,
        position=position,
        rule_id=_safe_identifier(result.rule_id, "rule_id"),
        status=result.status.value,
        severity=result.severity.value,
        category=_safe_identifier(result.category, "rule category"),
        parameter=_safe_identifier(result.parameter, "rule parameter", optional=True),
        message=_safe_text(result.message, "rule result message"),
        evidence_span_ids=_safe_identifier_list(result.evidence_span_ids, "evidence_span_ids"),
        involved_fact_ids=_safe_identifier_list(result.involved_fact_ids, "involved_fact_ids"),
        needs_human_review=result.needs_human_review,
        details=_sanitize_json(result.details),
    )


def _finding_row(finding: Finding, position: int, review_run_id: int) -> FindingORM:
    return FindingORM(
        review_run_id=review_run_id,
        position=position,
        finding_id=_safe_identifier(finding.finding_id, "finding_id"),
        origin=finding.origin.value,
        category=_safe_identifier(finding.category, "finding category"),
        severity=finding.severity.value,
        parameter=_safe_identifier(finding.parameter, "finding parameter", optional=True),
        title=_safe_text(finding.title, "finding title"),
        description=_safe_text(finding.description, "finding description"),
        suggestion=_safe_text(finding.suggestion, "finding suggestion"),
        rule_id=_safe_identifier(finding.rule_id, "finding rule_id", optional=True),
        evidence_span_ids=_safe_identifier_list(finding.evidence_span_ids, "evidence_span_ids"),
        needs_human_review=finding.needs_human_review,
        review_status=finding.review_status.value,
        human_note=_sanitize_note(finding.human_note),
        ai_snapshot=_sanitize_json(finding.original_ai_snapshot),
    )


def _fact_from_row(row: dict[str, Any]) -> ParameterFact:
    return ParameterFact.model_validate(row)


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


def _sanitize_facts(values: list[ParameterFact]) -> list[dict[str, Any]]:
    facts = _facts_to_dict(values)
    # Do not pass this field-aware structure through generic key filtering:
    # source_document is a safe metadata identifier, not document content.
    return facts


def _facts_to_dict(values: list[ParameterFact]) -> list[dict[str, Any]]:
    return [
        {
            "fact_id": _safe_identifier(value.fact_id, "fact_id"),
            "canonical_name": _safe_identifier(value.canonical_name, "canonical_name"),
            "raw_name": _safe_text(value.raw_name, "raw_name"),
            "raw_value": _safe_text(value.raw_value, "raw_value"),
            "normalized_value": value.normalized_value,
            "raw_unit": _safe_identifier(value.raw_unit, "raw_unit", optional=True),
            "canonical_unit": _safe_identifier(value.canonical_unit, "canonical_unit", optional=True),
            "subject": _safe_identifier(value.subject, "subject", optional=True),
            "time_scope": _safe_identifier(value.time_scope, "time_scope", optional=True),
            "statistical_scope": _safe_identifier(value.statistical_scope, "statistical_scope", optional=True),
            "condition": _safe_identifier(value.condition, "condition", optional=True),
            "source_document": _safe_source_document(value.source_document),
            "source_version": _safe_identifier(value.source_version, "source_version", optional=True),
            "source_span_id": _safe_identifier(value.source_span_id, "source_span_id"),
            "extraction_method": value.extraction_method.value,
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


def _safe_identifier_list(values: list[str], field_name: str) -> list[str]:
    if not isinstance(values, list) or len(values) > 100:
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


def _sanitize_note(note: str | None) -> str | None:
    """Fail closed for secrets, request bodies, and document content in notes."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise TypeError("human note must be a string or None")
    if len(note) > 4_000:
        raise ValueError("human note exceeds 4000 characters")
    if _contains_prohibited_content(note):
        raise ValueError("human note contains forbidden secret or body content")
    return note


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


def _contains_prohibited_content(value: str) -> bool:
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
    if _looks_like_full_body(value):
        return True
    return False


def _looks_like_full_body(value: str) -> bool:
    # Persisted prose must remain a bounded review summary, never a body dump.
    return value.count("\n") >= 3 or len(value) > 1_000 or (
        value.lstrip().startswith(("{", "[")) and len(value) > 160
    )


def _sanitize_json(value: Any) -> Any:
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
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("metadata object exceeds 100 fields")
        output = {}
        for key, item in value.items():
            raw_key = str(key)
            if _is_sensitive_key(raw_key):
                continue
            safe_key = _safe_text(raw_key, "metadata key")
            output[safe_key] = _sanitize_json(item)
        return output
    raise TypeError(f"unsupported persistence metadata type: {type(value).__name__}")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return any(marker in normalized for marker in _SECRET_OR_BODY_MARKERS)
