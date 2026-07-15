"""Explicit public request and response schemas for the local API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ReviewStatus


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
    final_status: str
    finding_count: int
    fact_count: int
    stages: list[str]
    rules_loaded: bool
    rule_count: int


class FindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    origin: str
    category: str
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


class FindingReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=36, max_length=36)
    review_status: ReviewStatus
    human_note: str | None = Field(default=None, max_length=4000)


class DeleteCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=200)


class RulesetStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loaded: bool
    rule_count: int
    root: str | None = None


class RulesetReloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str | None = Field(default=None, max_length=1024)


class LLMConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    base_url: str | None = None
    model: str | None = None
    key_present: bool


class LLMConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock", "anthropic"]
    base_url: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=512)


class LLMHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str


ExportFormat = Literal["xlsx", "docx", "anonymous"]
