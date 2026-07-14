"""Reusable, fail-closed lifecycle runner for review pipeline stages."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from app.domain.enums import PipelineStage
from app.domain.schemas import StageRecord


class PipelineRun:
    """Observable result of a stage run."""

    def __init__(self, stage_records: list[StageRecord], final_status: str) -> None:
        self.stage_records = stage_records
        self.final_status = final_status


class StageRunner:
    """Run stages in order and stop permanently at the first exception."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        stages: Iterable[tuple[PipelineStage, Callable[[], Any]]],
    ) -> PipelineRun:
        records: list[StageRecord] = []
        for stage, callback in stages:
            started = self._clock()
            try:
                callback()
            except Exception as exc:
                ended = self._clock()
                error = sanitize_error(str(exc))
                records.append(
                    StageRecord(
                        stage=stage,
                        started_at=started,
                        ended_at=ended,
                        status="failed",
                        exception_type=type(exc).__name__,
                        error=error,
                    )
                )
                records.append(
                    StageRecord(
                        stage=PipelineStage.FAILED,
                        started_at=ended,
                        ended_at=ended,
                        status="failed",
                        exception_type=type(exc).__name__,
                        error=error,
                    )
                )
                return PipelineRun(records, "FAILED")
            records.append(
                StageRecord(
                    stage=stage,
                    started_at=started,
                    ended_at=self._clock(),
                    status="completed",
                )
            )
        return PipelineRun(records, "READY_FOR_HUMAN_REVIEW")


def sanitize_error(error: str, *, max_length: int = 240) -> str:
    """Return a short diagnostic without paths, credentials, or request bodies."""
    sanitized = re.sub(r"(?i)(?:[A-Za-z]:)?[\\/](?:[^\s\\/]+[\\/])*[^\s\\/]+", "[path]", error)
    sanitized = re.sub(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", "[redacted]", sanitized)
    sanitized = re.sub(r"(?i)\b(?:body|request|payload)\s*=\s*.*", "[redacted body]", sanitized)
    sanitized = re.sub(r"\b[A-Za-z0-9+/]{24,}={0,2}\b", "[redacted]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:max_length] or "stage failed"
