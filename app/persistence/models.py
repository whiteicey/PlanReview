"""SQLAlchemy ORM records for safe, metadata-only review persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    final_status: Mapped[str] = mapped_column(String(64), nullable=False)
    facts: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    stage_records: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
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


class RuleResultORM(Base):
    __tablename__ = "rule_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_run_id: Mapped[int] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
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
    ai_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    review_run: Mapped[ReviewRunORM] = relationship(back_populates="findings")


class RecycleBinORM(Base):
    __tablename__ = "recycle_bin"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    case: Mapped[CaseORM] = relationship(back_populates="recycle_entry")
