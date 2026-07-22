"""Explicit public request and response schemas for the local API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import FindingCategory, ReviewStatus


class CaseCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    file_name: str
    size: int
    sha256: str
    storage_relative_path: str


class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    final_status: str
    finding_count: int
    fact_count: int
    stages: list[str]
    rules_loaded: bool
    rule_count: int
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_status: str
    llm_finding_count: int
    llm_error_summary: str | None = None
    validation_reason_code: str | None = None
    candidate_count: int | None = None
    valid_count: int | None = None
    rejected_count: int | None = None
    available_span_count: int | None = None
    selected_span_count: int | None = None
    selected_character_count: int | None = None
    coverage_ratio: float | None = None


class ReviewJobAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: Literal["RUNNING"]


class ReviewProgressEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    stage: str
    event_type: str
    status: str
    message: str
    details: dict
    created_at: datetime


class ReviewProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_status: str
    last_sequence: int
    events: list[ReviewProgressEventResponse]


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    final_status: str
    created_at: datetime | None = None
    finding_count: int
    fact_count: int
    stages: list[str]
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_status: str
    llm_finding_count: int
    llm_error_summary: str | None = None
    validation_reason_code: str | None = None
    candidate_count: int | None = None
    valid_count: int | None = None
    rejected_count: int | None = None
    available_span_count: int | None = None
    selected_span_count: int | None = None
    selected_character_count: int | None = None
    coverage_ratio: float | None = None


class RunDiagnostics(BaseModel):
    """Read-only structured diagnostics; no prompt/provider payloads are exposed."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    evidence_selector_version: str | None = None
    packet_lifecycle_ledger: dict
    ai_candidate_lifecycle_ledger: dict
    rule_metrics: dict
    batch_metrics: list[dict]
    selection_diagnostics: dict
    integrity: dict


class ReviewFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    final_status: Literal["FAILED"]
    failed_stage: str
    failure_detail: str


class FindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    finding_id: str
    origin: str
    category: FindingCategory
    severity: str
    parameter: str | None = None
    title: str
    description: str
    suggestion: str
    rule_id: str | None = None
    evidence_span_ids: list[str]
    needs_human_review: bool
    review_status: ReviewStatus
    human_note: str | None = None
    reviewed_at: datetime | None = None
    is_expert_experience: bool = False
    experience_saved_at: datetime | None = None
    experience_updated_at: datetime | None = None


class FindingReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=36, max_length=36)
    run_id: str = Field(min_length=36, max_length=36)
    review_status: ReviewStatus
    human_note: str | None = None
    is_expert_experience: bool | None = None

    @field_validator("human_note")
    @classmethod
    def validate_human_note_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4_000:
            raise ValueError("专家备注最大 4000 字")
        return value


class FindingReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus
    human_note: str | None = None
    is_expert_experience: bool | None = None

    @field_validator("human_note")
    @classmethod
    def validate_human_note_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4_000:
            raise ValueError("专家备注最大 4000 字")
        return value


class ExpertExperienceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_count: int = Field(ge=0)
    updated_at: datetime | None = None


class FindingReviewResponse(FindingResponse):
    review_saved: bool
    expert_experience_saved: bool
    expert_experience_total_count: int = Field(ge=0)


class DeleteCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=200)


class RulesetStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loaded: bool
    rule_count: int


class RulesetReloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    base_url: str | None = None
    model: str | None = None
    allow_private_endpoint: bool = False
    key_present: bool
    credential_storage_available: bool
    configuration_error: str | None = None


class LLMConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock", "anthropic"]
    base_url: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=512)
    allow_private_endpoint: bool = False


class LLMHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str


class LLMStructuredOutputTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_ok: bool
    structured_output_ok: bool
    validation_reason_code: str | None = None
    candidate_count: int | None = None
    valid_count: int | None = None
    rejected_count: int | None = None
    detail: str


ExportFormat = Literal["xlsx", "docx", "anonymous"]
