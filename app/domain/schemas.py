from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import (
    BlockType,
    ExtractionMethod,
    OnMissing,
    Origin,
    ReviewStatus,
    RuleStatus,
    Severity,
)


class SourceSpan(BaseModel):
    span_id: str
    document_id: str
    section_path: list[str] = Field(default_factory=list)
    block_type: BlockType
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    text: str
    text_hash: str


class ParameterFact(BaseModel):
    fact_id: str
    canonical_name: str
    raw_name: str
    raw_value: str
    normalized_value: float | None = None
    raw_unit: str | None = None
    canonical_unit: str | None = None
    subject: str | None = None
    time_scope: str | None = None
    statistical_scope: str | None = None
    condition: str | None = None
    source_document: str
    source_version: str | None = None
    source_span_id: str
    extraction_method: ExtractionMethod
    confidence: float = 1.0
    human_status: ReviewStatus = ReviewStatus.PENDING

    def comparison_key(self) -> tuple[str, str | None, str | None, str | None, str | None]:
        return (
            self.canonical_name,
            self.subject,
            self.time_scope,
            self.statistical_scope,
            self.condition,
        )

    @property
    def has_complete_key(self) -> bool:
        return (
            self.subject is not None
            and self.time_scope is not None
            and self.statistical_scope is not None
        )


class RuleDefinition(BaseModel):
    rule_id: str
    version: str
    name: str
    category: str
    severity: Severity
    operator: str
    on_missing: OnMissing
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "DEMO_ONLY"


class RuleResult(BaseModel):
    rule_id: str
    status: RuleStatus
    severity: Severity
    category: str
    parameter: str | None = None
    message: str = ""
    evidence_span_ids: list[str] = Field(default_factory=list)
    involved_fact_ids: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    finding_id: str
    origin: Origin
    category: str
    severity: Severity
    parameter: str | None = None
    title: str
    description: str = ""
    suggestion: str = ""
    rule_id: str | None = None
    evidence_span_ids: list[str] = Field(default_factory=list)
    needs_human_review: bool
    review_status: ReviewStatus = ReviewStatus.PENDING
    human_note: str | None = None
    original_ai_snapshot: dict[str, Any] = Field(default_factory=dict)
