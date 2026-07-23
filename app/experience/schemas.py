"""Strict public and provider schemas for expert-experience summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ExperienceStatus = Literal["NOT_REQUESTED", "PENDING", "RUNNING", "COMPLETED", "FAILED", "STALE", "DELETED"]


class ExperienceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    experience_title: str = Field(min_length=1, max_length=200)
    problem_pattern: str = Field(min_length=1, max_length=1000)
    judgment_basis: list[str] = Field(min_length=1, max_length=5)
    recommended_action: list[str] = Field(min_length=1, max_length=5)
    applicable_scope: str = Field(min_length=1, max_length=500)
    keywords: list[str] = Field(min_length=2, max_length=8)

    @field_validator("judgment_basis", "recommended_action")
    @classmethod
    def validate_long_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 or any(ord(char) < 32 and char not in "\n\t" for char in value) for value in values):
            raise ValueError("summary list item is invalid")
        return values

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 64 or any(ord(char) < 32 for char in value) for value in values):
            raise ValueError("keyword is invalid")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("keywords must be unique")
        return values


class ExperienceJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    experience_id: str
    source_run_id: str
    source_finding_id: str
    finding_row_id: int
    status: ExperienceStatus
    expert_review_status: str
    experience_summary: ExperienceSummary | None = None
    error_summary: str | None = None
    expert_experience_total_count: int = Field(ge=0)
    updated_at: datetime


class ExperienceListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experience_id: str
    source_case_id: str
    source_run_id: str
    source_finding_id: str
    finding_row_id: int
    status: ExperienceStatus
    expert_review_status: str
    title: str
    category: str
    severity: str
    origin: str
    rule_id: str | None = None
    expert_note: str | None = None
    summary: ExperienceSummary | None = None
    summary_model: str | None = None
    saved_at: datetime | None = None
    updated_at: datetime


class ExperienceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExperienceListItem]
    total_count: int = Field(ge=0)
    active_count: int = Field(ge=0)
    deleted_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    updated_at: datetime | None = None


class ExperienceDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


class ExperienceMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_id: str
    status: ExperienceStatus
    deleted: bool
    expert_experience_total_count: int = Field(ge=0)
    deleted_experience_count: int = Field(ge=0)
