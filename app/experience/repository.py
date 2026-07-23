"""Transactional persistence and concurrency gates for expert experiences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import ReviewStatus
from app.persistence.models import (
    CaseFileORM,
    ExpertExperienceEvidenceSpanORM,
    ExpertExperienceSummaryJobORM,
    FindingORM,
    ReviewRunORM,
    RecycleBinORM,
)
from app.experience.schemas import ExperienceSummary

PROMPT_VERSION = "expert-experience-summary-v1"
TASK_TYPE = "EXPERT_EXPERIENCE_SUMMARY"
LEASE_SECONDS = 90


@dataclass(frozen=True)
class RequestedJob:
    job_id: str
    status: str
    finding_row_id: int
    source_run_id: str
    source_finding_id: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ExperienceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _finding(self, case_id: str, run_id: str, finding_id: str) -> FindingORM | None:
        return self.session.scalar(
            select(FindingORM)
            .join(ReviewRunORM)
            .where(
                ReviewRunORM.case_id == case_id,
                ReviewRunORM.run_id == run_id,
                FindingORM.finding_id == finding_id,
            )
        )

    def source_hash_for(self, finding: FindingORM) -> str:
        run = finding.review_run
        evidence_hashes = run.evidence_text_hashes or {}
        evidence = []
        for span_id in finding.evidence_span_ids:
            content_hash = evidence_hashes.get(span_id)
            if (
                not isinstance(content_hash, str) or len(content_hash) != 64
                or any(character not in "0123456789abcdefABCDEF" for character in content_hash)
            ):
                raise ValueError("persisted evidence content hash is missing or invalid")
            evidence.append({"span_id": span_id, "text_hash": content_hash.lower()})
        document_hashes = self.session.scalars(
            select(CaseFileORM.sha256).where(CaseFileORM.case_id == run.case_id).order_by(CaseFileORM.id)
        ).all()
        payload = {
            "run_id": run.run_id,
            "finding_id": finding.finding_id,
            "review_status": finding.review_status,
            "human_note": finding.human_note,
            "severity": finding.severity,
            "origin": finding.origin,
            "rule_id": finding.rule_id,
            "title": finding.title,
            "description": finding.description,
            "suggestion": finding.suggestion,
            "evidence": evidence,
            "document_hashes": list(document_hashes),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def synchronize_after_review(
        self, case_id: str, run_id: str, finding_id: str, *, force_retry: bool = False
    ) -> RequestedJob | None:
        finding = self._finding(case_id, run_id, finding_id)
        if finding is None:
            raise KeyError("finding not found")
        if not finding.is_expert_experience or finding.review_status == ReviewStatus.PENDING.value:
            current = None if not finding.experience_summary_job_id else self.session.get(
                ExpertExperienceSummaryJobORM, finding.experience_summary_job_id
            )
            if current is not None and current.status != "DELETED":
                current.status = "STALE"
                current.worker_token = None
                current.lease_expires_at = None
            finding.experience_summary_job_id = None
            self.session.commit()
            return None

        source_hash = self.source_hash_for(finding)
        existing = self.session.scalar(
            select(ExpertExperienceSummaryJobORM).where(
                ExpertExperienceSummaryJobORM.finding_row_id == finding.id,
                ExpertExperienceSummaryJobORM.source_hash == source_hash,
            )
        )
        if existing is not None:
            if existing.status == "FAILED" and force_retry:
                existing.status = "PENDING"
                existing.error_summary = None
                existing.worker_token = None
                existing.lease_expires_at = None
                existing.heartbeat_at = None
            finding.experience_summary_job_id = existing.job_id
            self.session.commit()
            return RequestedJob(existing.job_id, existing.status, finding.id, run_id, finding_id)

        previous = None if not finding.experience_summary_job_id else self.session.get(
            ExpertExperienceSummaryJobORM, finding.experience_summary_job_id
        )
        if previous is not None and previous.status != "DELETED":
            previous.status = "STALE"
            previous.worker_token = None
            previous.lease_expires_at = None
        job = ExpertExperienceSummaryJobORM(
            job_id=str(uuid4()),
            finding_row_id=finding.id,
            source_run_id=run_id,
            source_finding_id=finding_id,
            source_hash=source_hash,
            task_type=TASK_TYPE,
            status="PENDING",
            prompt_version=PROMPT_VERSION,
        )
        self.session.add(job)
        finding.experience_summary_job_id = job.job_id
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(ExpertExperienceSummaryJobORM).where(
                    ExpertExperienceSummaryJobORM.finding_row_id == finding.id,
                    ExpertExperienceSummaryJobORM.source_hash == source_hash,
                )
            )
            if existing is None:
                raise
            finding = self._finding(case_id, run_id, finding_id)
            assert finding is not None
            finding.experience_summary_job_id = existing.job_id
            self.session.commit()
            job = existing
        return RequestedJob(job.job_id, job.status, finding.id, run_id, finding_id)

    def recover_expired_jobs(self) -> list[str]:
        now = utc_now()
        expired = self.session.scalars(
            select(ExpertExperienceSummaryJobORM).where(
                ExpertExperienceSummaryJobORM.status == "RUNNING",
                ExpertExperienceSummaryJobORM.lease_expires_at < now,
            )
        ).all()
        for job in expired:
            job.status = "PENDING"
            job.worker_token = None
            job.lease_expires_at = None
        pending = self.session.scalars(
            select(ExpertExperienceSummaryJobORM.job_id).where(
                ExpertExperienceSummaryJobORM.status == "PENDING"
            )
        ).all()
        self.session.commit()
        return list(pending)

    def claim(self, job_id: str, worker_token: str) -> bool:
        now = utc_now()
        result = self.session.execute(
            update(ExpertExperienceSummaryJobORM)
            .execution_options(synchronize_session=False)
            .where(
                ExpertExperienceSummaryJobORM.job_id == job_id,
                or_(
                    ExpertExperienceSummaryJobORM.status == "PENDING",
                    (
                        (ExpertExperienceSummaryJobORM.status == "RUNNING")
                        & (ExpertExperienceSummaryJobORM.lease_expires_at < now)
                    ),
                ),
                ExpertExperienceSummaryJobORM.experience_is_deleted.is_(False),
            )
            .values(
                status="RUNNING",
                worker_token=worker_token,
                started_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                attempt_count=ExpertExperienceSummaryJobORM.attempt_count + 1,
                error_summary=None,
            )
        )
        self.session.commit()
        return result.rowcount == 1

    def heartbeat(self, job_id: str, worker_token: str) -> bool:
        now = utc_now()
        result = self.session.execute(
            update(ExpertExperienceSummaryJobORM)
            .execution_options(synchronize_session=False)
            .where(
                ExpertExperienceSummaryJobORM.job_id == job_id,
                ExpertExperienceSummaryJobORM.status == "RUNNING",
                ExpertExperienceSummaryJobORM.worker_token == worker_token,
                ExpertExperienceSummaryJobORM.lease_expires_at > now,
            )
            .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS))
        )
        self.session.commit()
        return result.rowcount == 1

    def save_evidence_snapshot(self, job_id: str, worker_token: str, snapshot: list[dict]) -> None:
        job = self.session.get(ExpertExperienceSummaryJobORM, job_id, populate_existing=True)
        if (
            job is None or job.worker_token != worker_token or job.status != "RUNNING"
            or _utc(job.lease_expires_at) is None or _utc(job.lease_expires_at) <= utc_now()
        ):
            raise RuntimeError("job lease lost")
        job.evidence_snapshot = snapshot
        for item in snapshot:
            existing = self.session.scalar(
                select(ExpertExperienceEvidenceSpanORM).where(
                    ExpertExperienceEvidenceSpanORM.run_id == job.source_run_id,
                    ExpertExperienceEvidenceSpanORM.span_id == item["span_id"],
                )
            )
            if existing is None:
                self.session.add(ExpertExperienceEvidenceSpanORM(
                    run_id=job.source_run_id,
                    span_id=item["span_id"],
                    text=item["text"],
                    text_hash=item["text_hash"],
                    location=item["location"],
                    document_sha256=item["document_sha256"],
                ))
        self.session.commit()

    def persisted_evidence(self, job: ExpertExperienceSummaryJobORM, span_ids: list[str]) -> list[dict]:
        if job.evidence_snapshot:
            by_id = {item.get("span_id"): item for item in job.evidence_snapshot}
            if all(span_id in by_id for span_id in span_ids):
                return [by_id[span_id] for span_id in span_ids]
        rows = self.session.scalars(
            select(ExpertExperienceEvidenceSpanORM).where(
                ExpertExperienceEvidenceSpanORM.run_id == job.source_run_id,
                ExpertExperienceEvidenceSpanORM.span_id.in_(span_ids),
            )
        ).all()
        by_id = {row.span_id: row for row in rows}
        if not all(span_id in by_id for span_id in span_ids):
            return []
        return [{
            "span_id": row.span_id,
            "text": row.text,
            "text_hash": row.text_hash,
            "location": row.location,
            "document_sha256": row.document_sha256,
        } for row in (by_id[span_id] for span_id in span_ids)]

    def complete(
        self, job_id: str, worker_token: str, summary: ExperienceSummary, provider: str, model: str
    ) -> bool:
        job = self.session.get(ExpertExperienceSummaryJobORM, job_id, populate_existing=True)
        if job is None:
            return False
        finding = self.session.get(FindingORM, job.finding_row_id, populate_existing=True)
        eligible = (
            finding is not None
            and finding.experience_summary_job_id == job_id
            and finding.is_expert_experience
            and finding.review_status != ReviewStatus.PENDING.value
            and not job.experience_is_deleted
            and self.source_hash_for(finding) == job.source_hash
        )
        owned = (
            job.status == "RUNNING"
            and job.worker_token == worker_token
            and _utc(job.lease_expires_at) is not None
            and _utc(job.lease_expires_at) > utc_now()
        )
        if not eligible:
            self.session.execute(
                update(ExpertExperienceSummaryJobORM)
                .execution_options(synchronize_session=False)
                .where(
                    ExpertExperienceSummaryJobORM.job_id == job_id,
                    ExpertExperienceSummaryJobORM.status == "RUNNING",
                    ExpertExperienceSummaryJobORM.worker_token == worker_token,
                )
                .values(
                    status="DELETED" if job.experience_is_deleted else "STALE",
                    worker_token=None,
                    lease_expires_at=None,
                )
            )
            self.session.commit()
            return False
        if not owned:
            return False
        now = utc_now()
        payload = summary.model_dump(mode="json")
        result = self.session.execute(
            update(ExpertExperienceSummaryJobORM)
            .execution_options(synchronize_session=False)
            .where(
                ExpertExperienceSummaryJobORM.job_id == job_id,
                ExpertExperienceSummaryJobORM.status == "RUNNING",
                ExpertExperienceSummaryJobORM.worker_token == worker_token,
                ExpertExperienceSummaryJobORM.experience_is_deleted.is_(False),
                ExpertExperienceSummaryJobORM.lease_expires_at > now,
            )
            .values(
                summary_json=payload,
                summary_text="；".join([
                    payload["experience_title"], payload["problem_pattern"],
                    "；".join(payload["recommended_action"]),
                ]),
                summary_provider=provider,
                summary_model=model,
                status="COMPLETED",
                completed_at=now,
                heartbeat_at=now,
                lease_expires_at=None,
                worker_token=None,
            )
        )
        self.session.commit()
        return result.rowcount == 1

    def fail(self, job_id: str, worker_token: str, error_summary: str) -> bool:
        now = utc_now()
        result = self.session.execute(
            update(ExpertExperienceSummaryJobORM)
            .execution_options(synchronize_session=False)
            .where(
                ExpertExperienceSummaryJobORM.job_id == job_id,
                ExpertExperienceSummaryJobORM.status == "RUNNING",
                ExpertExperienceSummaryJobORM.worker_token == worker_token,
                ExpertExperienceSummaryJobORM.lease_expires_at > now,
            )
            .values(
                status="FAILED", error_summary=error_summary[:500], worker_token=None,
                lease_expires_at=None, heartbeat_at=now,
            )
        )
        self.session.commit()
        return result.rowcount == 1

    def active_count(self) -> int:
        return int(self.session.scalar(
            select(func.count(ExpertExperienceSummaryJobORM.job_id))
            .join(FindingORM, FindingORM.id == ExpertExperienceSummaryJobORM.finding_row_id)
            .join(ReviewRunORM, ReviewRunORM.id == FindingORM.review_run_id)
            .outerjoin(RecycleBinORM, RecycleBinORM.case_id == ReviewRunORM.case_id)
            .where(
                FindingORM.experience_summary_job_id == ExpertExperienceSummaryJobORM.job_id,
                FindingORM.is_expert_experience.is_(True),
                FindingORM.review_status != ReviewStatus.PENDING.value,
                ExpertExperienceSummaryJobORM.status == "COMPLETED",
                ExpertExperienceSummaryJobORM.experience_is_deleted.is_(False),
                RecycleBinORM.case_id.is_(None),
            )
        ) or 0)

    def deleted_count(self) -> int:
        return int(self.session.scalar(select(func.count(ExpertExperienceSummaryJobORM.job_id)).where(
            ExpertExperienceSummaryJobORM.experience_is_deleted.is_(True)
        )) or 0)

    def get_job(self, job_id: str) -> ExpertExperienceSummaryJobORM | None:
        return self.session.get(ExpertExperienceSummaryJobORM, job_id)

    def list_current(
        self, *, page: int, page_size: int, query: str | None = None,
        severity: str | None = None, origin: str | None = None,
        rule_id: str | None = None, view: str = "active",
    ) -> tuple[list[tuple[ExpertExperienceSummaryJobORM, FindingORM]], int, int, int, int, datetime | None]:
        base = (
            select(ExpertExperienceSummaryJobORM, FindingORM)
            .join(FindingORM, FindingORM.id == ExpertExperienceSummaryJobORM.finding_row_id)
        )
        if view == "deleted":
            base = base.where(ExpertExperienceSummaryJobORM.experience_is_deleted.is_(True))
        elif view == "failed":
            base = base.where(
                FindingORM.experience_summary_job_id == ExpertExperienceSummaryJobORM.job_id,
                ExpertExperienceSummaryJobORM.status == "FAILED",
                ExpertExperienceSummaryJobORM.experience_is_deleted.is_(False),
            )
        else:
            base = base.where(
                FindingORM.experience_summary_job_id == ExpertExperienceSummaryJobORM.job_id,
                ExpertExperienceSummaryJobORM.status == "COMPLETED",
                ExpertExperienceSummaryJobORM.experience_is_deleted.is_(False),
                FindingORM.is_expert_experience.is_(True),
                FindingORM.review_status != ReviewStatus.PENDING.value,
            )
        if query:
            pattern = f"%{query.strip()}%"
            base = base.where(or_(
                FindingORM.title.like(pattern), FindingORM.human_note.like(pattern),
                ExpertExperienceSummaryJobORM.summary_text.like(pattern),
            ))
        if severity:
            base = base.where(FindingORM.severity == severity)
        if origin:
            base = base.where(FindingORM.origin == origin)
        if rule_id:
            base = base.where(FindingORM.rule_id == rule_id)
        filtered = base.subquery()
        total = int(self.session.scalar(select(func.count()).select_from(filtered)) or 0)
        rows = self.session.execute(
            base.order_by(ExpertExperienceSummaryJobORM.updated_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        deleted = self.deleted_count()
        failed = int(self.session.scalar(select(func.count(ExpertExperienceSummaryJobORM.job_id)).where(
            ExpertExperienceSummaryJobORM.status == "FAILED",
            ExpertExperienceSummaryJobORM.experience_is_deleted.is_(False),
        )) or 0)
        updated_at = self.session.scalar(select(func.max(ExpertExperienceSummaryJobORM.updated_at)))
        return list(rows), total, self.active_count(), deleted, failed, updated_at

    def delete(self, experience_id: str, reason: str | None) -> ExpertExperienceSummaryJobORM:
        job = self.session.get(ExpertExperienceSummaryJobORM, experience_id)
        if job is None:
            raise KeyError("experience not found")
        if not job.experience_is_deleted:
            job.experience_is_deleted = True
            job.experience_deleted_reason = reason.strip() if reason else None
            job.experience_deleted_at = utc_now()
            job.status = "DELETED"
            job.worker_token = None
            job.lease_expires_at = None
            self.session.commit()
        return job

    def restore(self, experience_id: str) -> tuple[ExpertExperienceSummaryJobORM, RequestedJob | None]:
        job = self.session.get(ExpertExperienceSummaryJobORM, experience_id)
        if job is None:
            raise KeyError("experience not found")
        finding = self.session.get(FindingORM, job.finding_row_id)
        if finding is None:
            raise KeyError("finding not found")
        current_hash = self.source_hash_for(finding)
        if current_hash == job.source_hash and job.summary_json:
            job.experience_is_deleted = False
            job.experience_deleted_reason = None
            job.experience_restored_at = utc_now()
            job.status = "COMPLETED"
            finding.experience_summary_job_id = job.job_id
            finding.is_expert_experience = True
            self.session.commit()
            return job, None
        job.status = "STALE"
        job.experience_is_deleted = True
        finding.is_expert_experience = True
        self.session.commit()
        requested = self.synchronize_after_review(
            finding.review_run.case_id, finding.review_run.run_id, finding.finding_id
        )
        assert requested is not None
        return self.session.get(ExpertExperienceSummaryJobORM, requested.job_id), requested
