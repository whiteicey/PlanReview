"""SQLAlchemy ORM records for safe, metadata-only review persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.storage.case_files import StoredFile


class Base(DeclarativeBase):
    pass


@dataclass(frozen=True)
class CaseRecord:
    """Safe case metadata; document bytes and bodies never belong here."""

    case_id: str
    files: list[StoredFile] = field(default_factory=list)
    statistics: dict[str, int | float | str | bool | None] = field(default_factory=dict)


class CaseORM(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    statistics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    files: Mapped[list["CaseFileORM"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    review_runs: Mapped[list["ReviewRunORM"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    recycle_entry: Mapped["RecycleBinORM | None"] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class CaseFileORM(Base):
    __tablename__ = "case_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    safe_name: Mapped[str] = mapped_column(String(255), nullable=False)
    case: Mapped[CaseORM] = relationship(back_populates="files")


class ReviewRunORM(Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        Index("ix_review_runs_case_created", "case_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True
    )
    final_status: Mapped[str] = mapped_column(String(64), nullable=False)
    facts: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    stage_records: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    evidence_text_hashes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_locations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(String(128))
    llm_model: Mapped[str | None] = mapped_column(String(255))
    llm_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_RUN")
    llm_finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_error_summary: Mapped[str | None] = mapped_column(Text)
    validation_reason_code: Mapped[str | None] = mapped_column(String(64))
    candidate_count: Mapped[int | None] = mapped_column(Integer)
    valid_count: Mapped[int | None] = mapped_column(Integer)
    rejected_count: Mapped[int | None] = mapped_column(Integer)
    available_span_count: Mapped[int | None] = mapped_column(Integer)
    selected_span_count: Mapped[int | None] = mapped_column(Integer)
    selected_character_count: Mapped[int | None] = mapped_column(Integer)
    coverage_ratio: Mapped[float | None] = mapped_column(Float)
    git_sha: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(128))
    evidence_selector_version: Mapped[str | None] = mapped_column(String(128))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    timeout: Mapped[float | None] = mapped_column(Float)
    temperature: Mapped[float | None] = mapped_column(Float)
    batch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batch_metrics: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    premerge_finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    postmerge_finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplicated_finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    ai_guard_rejections: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    deduplication_records: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    packet_lifecycle_ledger: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ai_candidate_lifecycle_ledger: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    rule_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    worker_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    case: Mapped[CaseORM] = relationship(back_populates="review_runs")
    rule_results: Mapped[list["RuleResultORM"]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan", passive_deletes=True
    )
    findings: Mapped[list["FindingORM"]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan", passive_deletes=True
    )
    progress_events: Mapped[list["ReviewProgressEventORM"]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan", passive_deletes=True
    )


class ReviewProgressEventORM(Base):
    __tablename__ = "review_progress_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_review_progress_run_sequence"),
        Index("ix_review_progress_run_sequence", "run_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    review_run: Mapped[ReviewRunORM] = relationship(back_populates="progress_events")


class RuleResultORM(Base):
    __tablename__ = "rule_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_version: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    parameter: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_span_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    involved_fact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(nullable=False, default=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    review_run: Mapped[ReviewRunORM] = relationship(back_populates="rule_results")


class FindingORM(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("review_run_id", "finding_id", name="uq_finding_run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    parameter: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(255))
    evidence_span_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(nullable=False, default=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    human_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_expert_experience: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    experience_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    experience_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    experience_summary_job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    ai_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    review_run: Mapped[ReviewRunORM] = relationship(back_populates="findings")
    experience_jobs: Mapped[list["ExpertExperienceSummaryJobORM"]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", passive_deletes=True
    )


class ExpertExperienceSummaryJobORM(Base):
    __tablename__ = "expert_experience_summary_jobs"
    __table_args__ = (
        UniqueConstraint("finding_row_id", "source_hash", name="uq_experience_finding_source"),
        Index("ix_experience_status_lease", "status", "lease_expires_at"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    finding_row_id: Mapped[int] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_finding_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="EXPERT_EXPERIENCE_SUMMARY")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text)
    summary_provider: Mapped[str | None] = mapped_column(String(64))
    summary_model: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False, default="expert-experience-summary-v1")
    evidence_snapshot: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    worker_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    experience_is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    experience_deleted_reason: Mapped[str | None] = mapped_column(String(500))
    experience_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    experience_restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    finding: Mapped[FindingORM] = relationship(back_populates="experience_jobs")


class ExpertExperienceEvidenceSpanORM(Base):
    __tablename__ = "expert_experience_evidence_spans"
    __table_args__ = (UniqueConstraint("run_id", "span_id", name="uq_experience_span_run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    location: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RecycleBinORM(Base):
    __tablename__ = "recycle_bin"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    case: Mapped[CaseORM] = relationship(back_populates="recycle_entry")


class FileOperationAuditORM(Base):
    __tablename__ = "file_operation_audit"
    __table_args__ = (
        Index("ix_file_operation_audit_recovery_created", "recovery_required", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(255))
    recovery_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
